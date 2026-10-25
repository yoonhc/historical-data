"""Read-only native before/after capture for the installed T05 preview."""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIOR = HERE.parent / 't05-disabled-scope-2026-10-17'
sys.path.insert(0, str(PRIOR))
import refresh_scope as reviewed

UNIT = 'marketdata-checkpoint-retirement-preview.service'
STATE = Path('/var/lib/marketdata-checkpoint-retirement')


def save(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('before', 'after'), required=True)
    args = parser.parse_args()
    output = HERE / (args.stage + '.json')
    if output.exists():
        raise RuntimeError('Preserve earlier capture: ' + str(output))
    plan = json.loads((PRIOR / 'plan.json').read_bytes())
    reviewed.validate_plan(plan)
    with reviewed.locks():
        live = reviewed.verify_live(plan)
        reviewed.require(live['position'] == 'after', 'Disabled scope differs')
        props = subprocess.run(
            ['/usr/bin/systemctl', 'show', UNIT,
             '--property=Id,FragmentPath,DropInPaths,NeedDaemonReload,User,Group,ExecStart,'
             'ProtectClock,ProtectSystem,ProtectHome,NoNewPrivileges,ReadWritePaths,'
             'ActiveState,SubState,MainPID,InvocationID,Result,ExecMainStatus,'
             'ExecMainStartTimestamp,ExecMainExitTimestamp'],
            capture_output=True, text=True, check=True, timeout=30).stdout
        expected = reviewed.CONFIG / 'units' / UNIT
        installed = Path('/etc/systemd/system') / UNIT
        reviewed.require(installed.resolve() == expected, 'Unexpected preview unit link')
        reviewed.require(installed.read_bytes() == expected.read_bytes(), 'Unit bytes differ')
        reviewed.require(' preview ;' in props and 'DropInPaths=\n' in props
                         and 'NeedDaemonReload=no\n' in props,
                         'Unexpected effective preview command/configuration')
        registry = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((STATE / 'evidence').rglob('*.json'))}
        runs = {p.parent.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((STATE / 'runs').glob('*/run.json'))}
        result = {'checked_at': datetime.now(timezone.utc).isoformat(),
                  'stage': args.stage, 'passed': True,
                  'boot_id': reviewed.BOOT.read_text().strip(),
                  'scope_update_plan_id': plan['plan_id'],
                  'disabled_grant': reviewed.fingerprint(reviewed.TARGET),
                  'protected': {p: reviewed.fingerprint(p) for p in plan['protected']},
                  'units': live['units'], 'preview_unit_show': props,
                  'registry_files': registry, 'runs': runs,
                  'new_evidence_registration': False, 'checkpoint_deletion': False}
        if args.stage == 'after':
            before = json.loads((HERE / 'before.json').read_bytes())
            reviewed.require(before['boot_id'] == result['boot_id'], 'Boot changed')
            reviewed.require(before['protected'] == result['protected'], 'Protected input changed')
            reviewed.require(before['disabled_grant'] == result['disabled_grant'], 'Grant changed')
            reviewed.require(all(registry.get(p) == d for p, d in before['registry_files'].items()),
                             'Existing evidence changed')
            for name in reviewed.PROCESS_UNITS:
                for key in ('MainPID', 'InvocationID', 'ActiveState'):
                    reviewed.require(before['units'][name][key] == result['units'][name][key],
                                     'Protected process changed')
            reviewed.require(all(runs.get(p) == d for p, d in before['runs'].items()),
                             'Existing run report changed')
            result.update(protected_processes_unchanged=True,
                          new_registry_files=sorted(set(registry)-set(before['registry_files'])),
                          new_runs=sorted(set(runs)-set(before['runs'])))
        save(output, result)
    print(json.dumps({'passed': True, 'stage': args.stage, 'report': str(output),
                      'protected_inputs': len(result['protected']),
                      'registry_files': len(registry), 'new_runs': result.get('new_runs')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
