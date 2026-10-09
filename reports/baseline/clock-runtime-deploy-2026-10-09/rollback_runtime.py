"""Prepared rollback of this exact deployment; not executed during deployment."""

import argparse
import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

OPS = Path('/var/lib/marketdata-clock/runtime-approvals/22706e571f3a46a8745803b18bca2efacc3318c3e05b2309df6f3c7c37c621e6')
READER_SHA = 'fb7d502cc4516b916a366ce05c28e7a0014fbc195fbcb4837acb9d5261c82d23'
READER = OPS / ('reader-recovery-' + READER_SHA[:16]) / 'continue_runtime.py'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--validate', action='store_true')
    group.add_argument('--run', action='store_true')
    group.add_argument('--root-copy', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location('recorded_reader', READER)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    assert reader.digest(READER.read_bytes()) == READER_SHA
    module = reader.load_tool()
    plan = json.loads((OPS / 'plan.json').read_text())
    approval = json.loads((OPS / 'approval.json').read_text())
    module.validate_plan(plan)
    assert plan['boot_id'] == module.rt.boot_id()
    host = reader.repaired_host(module)(plan)
    tx = module.Transition(plan, approval, host)
    receipt = json.loads((tx.directory / 'apply-receipt.json').read_text())
    assert receipt['receipt_id'] == module.gate.canonical_hash({k: v for k, v in receipt.items() if k != 'receipt_id'})
    assert receipt['plan_id'] == plan['plan_id']
    assert receipt['approval_sha256'] == module.gate.canonical_hash(approval)
    assert receipt['systemctl_reader_recovery']['sha256'] == READER_SHA
    assert json.loads(tx.ledger.path.read_text()) == receipt['ledger_after']
    for role, item in plan['files'].items():
        assert module.rt.digest(item['path']) == item['after_sha256'], role
    assert host.snapshot() == {'provider': receipt['activation']['provider']['provider']}
    if args.validate:
        print(json.dumps({'rollback_baseline_matches': True, 'plan_id': plan['plan_id'],
                          'operating_writes': False, 'note': 'Current clock/jobs rechecked before any stop'}))
        return
    assert os.geteuid() == 0
    for p in [READER, reader.TOOL, OPS, OPS / 'plan.json', OPS / 'approval.json',
              *(reader.TOOL.with_name(n) for n in module.tool_hashes())]:
        module.gate.trusted(p, directory=p.is_dir())
    source_sha = reader.digest(Path(__file__).read_bytes())
    directory = OPS / ('rollback-operation-' + source_sha[:16])
    if args.run:
        directory.mkdir(mode=0o755)
        module.sync_dir(OPS)
        target = directory / 'rollback_runtime.py'
        module.atomic(target, Path(__file__).read_bytes())
        module.immutable(directory / 'scope.json', {
            'plan_id': plan['plan_id'], 'rollback_source_sha256': source_sha,
            'reader_sha256': READER_SHA, 'action': 'Stop two collectors; stage rollback; reload; commit; start',
            'data_action': 'none; preserve catalog, artifacts, index, spool and all history',
            'operator_authenticated_at': datetime.now(timezone.utc).isoformat(),
        })
        os.execv('/usr/bin/python3', ['/usr/bin/python3', '-B', '-I', str(target), '--root-copy'])
    assert Path(__file__).resolve() == directory / 'rollback_runtime.py'
    module.gate.trusted(__file__)
    result = {'passed': False, 'phase': 'pre_stop_check', 'plan_id': plan['plan_id']}
    try:
        bootstrap = json.loads(plan['files']['bootstrap']['after'])
        runtime = json.loads(plan['files']['runtime']['after'])
        assert module.rt.probe(runtime)['ready'], 'Strict preparation not ready; collectors kept running'
        # Check user jobs only before the collector stop. The frozen stage checks
        # all jobs again under its lock. Never terminate a running business job.
        inventory = {**bootstrap, 'system_collectors': []}
        module.gate.Host(inventory, runtime).audit_quiescent()
        result['phase'] = 'normal_stop'
        subprocess.run(['/usr/bin/systemctl', 'stop', *reader.UNITS], check=True, timeout=360)
        for name in reader.UNITS:
            state = reader.parse_properties(module.rt.command([
                '/usr/bin/systemctl', 'show', name,
                '--property=ActiveState,MainPID,ControlPID,Result']))
            assert state['MainPID'] == state['ControlPID'] == '0'
            assert state['ActiveState'] in ('inactive', 'failed')
            assert state['Result'] not in ('timeout', 'watchdog', 'signal', 'core-dump'), state
        result['phase'] = 'stage_rollback'
        result['stage'] = tx.stage('rollback')
        subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True, timeout=30)
        result['phase'] = 'commit_rollback'
        result['commit'] = tx.commit()
        assert result['commit']['configuration'] == 'rollback_committed'
        subprocess.run(['/usr/bin/systemctl', 'start', '--no-block', *reader.UNITS], check=True, timeout=30)
        result.update(passed=True, phase='rollback_start_requested_observation_pending')
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        module.immutable(directory / 'result.json', result)
        print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
