"""Freeze the already selected batch. Creates review files only, never authority."""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('candidate', HERE / 'reclaim.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def main():
    require = r.require
    require(not (HERE / 'operation.json').exists(), 'Sealed operation exists; do not overwrite')
    require(r.digest(r.SELECTION.read_bytes()) == r.SELECTION_SHA, 'Selection changed')
    plan = json.loads(r.SELECTION.read_bytes())['proposal']
    require(r.digest(r.canonical(plan)) == r.PROPOSAL_ID, 'Proposal changed')
    r.validate_paths(plan)
    for name in ('old_algorithm', 'compatibility_evidence', 'current_contract', 'current_writer'):
        ref = plan[name]
        require(r.digest(Path(ref['path']).read_bytes()) == ref['sha256'], 'Prior evidence dependency changed')
    suites = ET.parse(HERE / 'tests-final.xml').getroot().findall('testsuite')
    require(sum(int(s.get('tests')) for s in suites) == 29
            and all(s.get('failures') == s.get('errors') == '0' for s in suites), 'Tests not passed')
    release = Path('/opt/marketdata/releases/restic-output-20261022-585c3e3686704e5c')
    paths = [
        '/usr/bin/python3.12', '/usr/bin/systemctl', '/usr/bin/ionice',
        '/etc/systemd/system/marketdata-restic-archive.timer',
        '/etc/systemd/system/marketdata-restic-archive.service',
        '/home/yoonhc/.config/marketdata/restic-archive.toml',
        '/home/yoonhc/.config/marketdata/restic-service.env',
        '/etc/marketdata/scheduled-clock-policy/clock-binding.json',
        '/etc/marketdata/checkpoint-clock-retirement/recurring.json',
        '/etc/marketdata/checkpoint-clock-retirement/manual.json',
        '/etc/systemd/system/marketdata-checkpoint-retirement.timer',
        '/etc/systemd/system/marketdata-checkpoint-retirement.service',
        '/etc/chrony/marketdata.conf',
        str(r.OLD), plan['compatibility_evidence']['path'],
        *[str(release / 'policy' / name) for name in ('run_job.py', 'request_contract.py', 'job_clock.py')],
        *[str(release / 'preserve/scripts' / name) for name in ('archive_backups.py', 'restic_schedule.py', 'archive_output.py')],
        str(release / 'preserve/packages/marketdata/src/marketdata/job_clock.py'),
    ]
    links = ['/usr/bin/python3',
        '/etc/systemd/system/timers.target.wants/marketdata-restic-archive.timer',
        '/etc/systemd/system/timers.target.wants/marketdata-checkpoint-retirement.timer',
        '/home/yoonhc/.config/systemd/user/marketdata-checkpoint-retirement.timer',
        '/home/yoonhc/.config/systemd/user/marketdata-checkpoint-retirement.service']
    files = {p: r.file_identity(p) for p in paths}
    operation = {
        'schema': 't19-bounded-reclamation-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
        'proposal_id': r.PROPOSAL_ID, 'selection_path': str(r.SELECTION),
        'selection_sha256': r.SELECTION_SHA, 'boot_id': plan['boot_id'],
        'bundle_sha256': {name: r.digest((HERE / name).read_bytes()) for name in
                          ('reclaim.py', 'prepare_operation.py', 'test_reclaim.py', 'tests-final.xml')},
        'protected_files': files, 'protected_links': {p: os.readlink(p) for p in links},
        'archive_lock': str(r.LOCK), 'audit_path': str(HERE / 'audit'),
        'replacement_paths': 1022, 'retained_sources': 2,
        'expected_reclaim_bytes': 8612974592, 'temporary_payload_bytes': 0,
        'collector_reserve_bytes': 10 * 1024**3, 'audit_budget_bytes': 8 * 1024**2,
        'read_budget_bytes_per_second': 64 * 1024**2,
        'authority': 'Exact replacement set and same-operation resume only; accepted once by explicit apply.',
        'lock_contract': 'Nonblocking existing archive lock, no coordinator/request lock; idle on entry.',
        'timer_contract': 'Keep active/enabled; no service controls; natural lock_busy remains failed/75 and retries naturally.',
        'resume_contract': 'Only exact originals, expected shared inodes and this operation temporary links; reject anything else.',
        'stop_contract': 'SIGINT/SIGTERM preserve completed links and audit; SIGKILL may leave one known temporary link. No expanding rollback copies.',
        'normal_growth': 'New checkpoints, snapshot membership and unrelated new logs are not frozen; exact selected historical bytes and receipts remain frozen.',
        'unchanged': ['marketdata', 'checkpoint files', 'restic repository', 'requests/pins/budgets',
                      'T05 evidence/policy', 'units/enable/masks', 'Chrony/collectors/time policy'],
        'tests': {'passed': 29, 'reused_algorithm_tests': 12, 'new_adapter_tests': 17,
                  'python': sys.version, 'actual_installed_modules_with_isolated_inputs': True,
                  'actual_systemd_dispatch_tested': False, 'power_loss_tested': False},
        'source_equivalence': {k: plan[k] for k in
                               ('old_algorithm', 'compatibility_evidence', 'current_contract', 'current_writer')},
    }
    r.static_guard(operation)
    oid = r.digest(r.canonical(operation))
    raw = r.canonical({'operation_id': oid, 'operation': operation}) + b'\n'
    with (HERE / 'operation.json').open('xb') as f:
        f.write(raw)
    capacity = {'at': datetime.now(timezone.utc).isoformat(), 'available_bytes': r.available(),
                'expected_reclaim_bytes': operation['expected_reclaim_bytes'],
                'audit_budget_bytes': operation['audit_budget_bytes'], 'operating_changes': 0}
    capacity['projected_available_after_audit_bytes'] = capacity['available_bytes'] + 8612974592 - 8388608
    with (HERE / 'capacity-at-freeze.json').open('xb') as f:
        f.write(r.canonical(capacity) + b'\n')
    print(json.dumps({'operation_id': oid, 'operation_json_sha256': r.digest(raw),
                      'tool_sha256': operation['bundle_sha256']['reclaim.py'], 'capacity': capacity}, indent=2))


if __name__ == '__main__':
    main()
