"""Read-only native before/after audit; writes evidence only in this report."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import refresh_scope as r


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('before', 'after'), required=True)
    args = parser.parse_args()
    plan = json.loads((HERE / 'plan.json').read_bytes())
    r.validate_plan(plan)
    evidence = HERE / 'operating-evidence'
    evidence.mkdir(exist_ok=True)
    output = evidence / (args.stage + '.json')
    r.require(not output.exists(), 'Preserve the earlier native audit')
    with r.locks():
        verified = r.verify_live(plan)
        r.require(verified['position'] == args.stage, 'Grant is not at the requested audit stage')
        registry = Path('/var/lib/marketdata-checkpoint-retirement/evidence')
        files = {str(p): r.sha(p.read_bytes()) for p in sorted(registry.rglob('*.json'))}
        for path in files:
            r.require(not Path(path).is_symlink(), 'Registry link requires review')
        result = {'checked_at': datetime.now(timezone.utc).isoformat(),
                  'plan_id': plan['plan_id'], 'stage': args.stage, 'passed': True,
                  'position': verified['position'], 'boot_id': r.BOOT.read_text().strip(),
                  'target': r.fingerprint(r.TARGET),
                  'grant_flags': {k: json.loads(r.TARGET.read_bytes())[k]
                                  for k in ('enabled', 'approved_at', 'approval_reference', 'scope_id')},
                  'protected': {path: r.fingerprint(path) for path in plan['protected']},
                  'units': verified['units'], 'registry_files': files,
                  'operating_write': False, 'formal_preview_run': False}
        if args.stage == 'after':
            before = json.loads((evidence / 'before.json').read_bytes())
            r.require(before['protected'] == result['protected'], 'Protected inputs changed')
            r.require(all(files.get(path) == digest for path, digest in before['registry_files'].items()),
                      'Existing registration/completion/order record changed')
            for name in r.PROCESS_UNITS:
                for key in ('MainPID', 'InvocationID', 'ActiveState'):
                    r.require(before['units'][name][key] == result['units'][name][key],
                              'Protected process changed')
            root = r.OPS / plan['plan_id']
            results = sorted(root.glob('apply-*.result.json'))
            r.require(bool(results), 'No root apply result')
            copies = {}
            for path in sorted(root.iterdir()):
                r.require(path.is_file() and not path.is_symlink(), 'Unexpected root audit object')
                raw = path.read_bytes()
                destination = evidence / ('root-' + path.name)
                r.write_new(destination, raw)
                copies[destination.name] = {'path': str(path), 'sha256': r.sha(raw),
                                            'fingerprint': r.fingerprint(path)}
            last = max((json.loads(p.read_bytes()) for p in results), key=lambda x: x['finished_at'])
            r.require(last['passed'] is True and last['position_after'] == 'after'
                      and last['plan_id'] == plan['plan_id'], 'Root apply result is not successful')
            result.update(source_copies=copies, root_result=last,
                          existing_registry_preserved=True,
                          new_registry_files=sorted(set(files) - set(before['registry_files'])),
                          protected_processes_unchanged=True)
        r.write_new(output, r.blob(result))
    print(json.dumps({k: v for k, v in result.items() if k not in ('units', 'protected', 'registry_files', 'source_copies')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
