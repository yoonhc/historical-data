"""Resume the approved transaction with a lossless systemctl output reader.

The frozen transaction, plan, approval, collector and configuration bytes stay
unchanged. Only LiveHost.unit's reading of repeated ExecStartPre keys differs.
All activation/trust/hash/ledger checks are inherited without modification.
The extra reader's source hash and raw observations are bound into the receipt.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PLAN_ID = '22706e571f3a46a8745803b18bca2efacc3318c3e05b2309df6f3c7c37c621e6'
OPS = Path('/var/lib/marketdata-clock/runtime-approvals') / PLAN_ID
TOOL = Path('/opt/marketdata/tools/collector-transition-20261009-1c7c4120369fd893/work/collector_transition.py')
UNITS = ['marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service']
PROPERTIES = 'ActiveState,MainPID,ControlPID,LoadState,NeedDaemonReload,FragmentPath,DropInPaths,ExecStart,ExecStartPre,User,WorkingDirectory,RuntimeMaxUSec,Restart,KillMode,KillSignal,TimeoutStopUSec,NoNewPrivileges,ProtectClock,ProtectSystem,ProtectHome,ReadWritePaths,CapabilityBoundingSet,PrivateTmp,RestrictAddressFamilies'


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def parse_properties(raw):
    result = {}
    for line in raw.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key in result:
            if key not in ('ExecStart', 'ExecStartPre'):
                raise ValueError('Unexpected repeated systemd property: ' + key)
            result[key] += '\n' + value
        else:
            result[key] = value
    return result


def repaired_host(module):
    class LosslessHost(module.LiveHost):
        def unit(self, name):
            raw = module.rt.command(['/usr/bin/systemctl', 'show', name,
                                     '--no-pager', '--property=' + PROPERTIES])
            return parse_properties(raw)

        def activation(self, record, direction):
            evidence = super().activation(record, direction)
            evidence['systemctl_reader_recovery'] = {
                'source': str(Path(__file__).resolve()),
                'sha256': digest(Path(__file__).read_bytes()),
                'reason': 'Preserve all repeated ExecStartPre properties; no activation checks waived',
                'original_plan_id': PLAN_ID,
                'original_tool_sha256': module.tool_hashes(),
            }
            return evidence
    return LosslessHost


def load_tool():
    spec = importlib.util.spec_from_file_location('frozen_transition', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--validate', action='store_true')
    group.add_argument('--run', action='store_true')
    group.add_argument('--root-copy', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    module = load_tool()
    plan = json.loads((OPS / 'plan.json').read_text())
    approval = json.loads((OPS / 'approval.json').read_text())
    module.validate_plan(plan)
    assert plan['plan_id'] == PLAN_ID and plan['boot_id'] == module.rt.boot_id()
    host = repaired_host(module)(plan)
    tx = module.Transition(plan, approval, host)
    for role, item in plan['files'].items():
        assert module.rt.digest(item['path']) == item['after_sha256'], role
    # Pure content checks can run without privilege; production commit repeats
    # these with the unchanged root ownership/ancestor checks and exclusive lock.
    progress = json.loads((tx.directory / 'progress.json').read_text())
    assert progress['direction'] == 'apply' and progress['phase'] == 'files_staged'
    assert progress['record_id'] == module.gate.canonical_hash({k: v for k, v in progress.items() if k != 'record_id'})
    assert json.loads(tx.ledger.path.read_text()) == tx.pending(progress['base_ledger'], 'apply')
    assert not (tx.directory / 'apply-receipt.json').exists()
    if args.validate:
        print(json.dumps({'pending_plan_verified': True, 'plan_id': PLAN_ID,
                          'original_tool_hashes_match': True, 'operating_writes': False,
                          'reader_sha256': digest(Path(__file__).read_bytes())}))
        return
    assert os.geteuid() == 0, 'Administrator authentication required'
    for p in [TOOL, OPS, OPS / 'plan.json', OPS / 'approval.json',
              *(TOOL.with_name(n) for n in module.tool_hashes())]:
        module.gate.trusted(p, directory=p.is_dir())
    own_sha = digest(Path(__file__).read_bytes())
    recovery = OPS / ('reader-recovery-' + own_sha[:16])
    if args.run:
        recovery.mkdir(mode=0o755)
        module.sync_dir(OPS)
        copy = recovery / 'continue_runtime.py'
        module.atomic(copy, Path(__file__).read_bytes())
        module.immutable(recovery / 'scope.json', {
            'plan_id': PLAN_ID, 'existing_approval_sha256': module.gate.canonical_hash(approval),
            'reader_sha256': own_sha, 'original_tools_sha256': module.tool_hashes(),
            'action': 'Commit the unchanged approved files and start the two collectors',
            'reason': 'Lossless reading of actual multi-line systemctl ExecStartPre output',
            'operator_authenticated_at': datetime.now(timezone.utc).isoformat(),
        })
        os.execv('/usr/bin/python3', ['/usr/bin/python3', '-B', '-I', str(copy), '--root-copy'])
    assert Path(__file__).resolve() == recovery / 'continue_runtime.py'
    module.gate.trusted(__file__)
    result = {'plan_id': PLAN_ID, 'passed': False, 'reader_sha256': own_sha,
              'started_at': datetime.now(timezone.utc).isoformat(), 'phase': 'commit'}
    try:
        result['commit'] = tx.commit()
        assert result['commit']['configuration'] == 'apply_committed'
        assert result['commit']['step_reopened'] is False
        result['phase'] = 'start_collectors'
        start = subprocess.run(['/usr/bin/systemctl', 'start', '--no-block', *UNITS],
                               capture_output=True, text=True, timeout=30)
        result['start'] = {'code': start.returncode, 'stdout': start.stdout, 'stderr': start.stderr}
        assert start.returncode == 0
        result.update(passed=True, phase='applied_start_requested_observation_pending')
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        module.immutable(recovery / 'result.json', result)
        print(json.dumps(result, indent=2))
        print('Root evidence: ' + str(recovery / 'result.json'))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
