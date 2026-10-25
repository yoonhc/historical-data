"""Reconcile captured T16 evidence with earlier completed backup/recovery checks."""
from pathlib import Path
from collections import Counter
from contextlib import closing
import datetime as dt
import hashlib
import importlib.util
import json
import sqlite3

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
OUT = Path(__file__).resolve().parent
OLD = ROOT / 'reports/baseline/scheduled-clock-repair-deploy-2026-10-16/operating-evidence'
BACKUP = ROOT / 'reports/baseline/t05-disabled-scope-2026-10-17'
PREVIEW = ROOT / 'reports/baseline/t05-formal-preview-2026-10-17'
RELEASE = Path('/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11')
refs = {}


def load(p):
    p = Path(p)
    raw = p.read_bytes()
    refs[str(p)] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    return json.loads(raw)


spec = importlib.util.spec_from_file_location('contract', RELEASE / 'policy/request_contract.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
b = load(BACKUP / 'backup-completion-20261017.json')
r = load(BACKUP / 'restic-completion-20261017.json')
v = load(PREVIEW / 'verification.json')
wrappers = {}
for job, doc in [('backup', b), ('restic-archive', r)]:
    w = doc['sources'][f'state/scheduled-clock-policy/{job}/request.json']['document']
    assert w['state'] == 'complete' and not w['attempt']['errors']
    assert w['attempt']['monitor']['stopped'] is True
    assert w['scope']['release'] == str(RELEASE)
    # Verify exact immutable files/rows; do not compare old mutable latest pointers.
    assert contract.classify(job, w['work_exit_code'], w['work_result'], w['pins'], w['arguments']) == w['disposition']
    wrappers[job] = w
maintenance = wrappers['backup']['work_result']['result']
archive = wrappers['restic-archive']['work_result']['result']
assert maintenance['backup']['backup_id'] == r['backup_id'] == b['backup']['backup_id']
assert maintenance['backup']['reused'] is False and maintenance['validation']['ok']
assert not maintenance['validation']['failures']
assert maintenance['backup']['clock_evidence_state'] == 'available'
assert archive['context']['classification'] == 'verified_timer' and archive['context']['manager'] == 'system'
assert r['full_check_read_data'] is True and r['full_check_errors'] == 0 and r['full_check_returncode'] == 0
for item in r['check_output_files'].values():
    raw = Path(item['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == item['sha256']
    refs[item['path']] = {'sha256': item['sha256'], 'bytes': len(raw)}
bundle = v['latest_snapshot_bundle'][0]
assert bundle['receipt']['receipt']['arguments'] == ['dump', r['snapshot_id'], '/checkpoint-evidence.json']
bundle_ref = bundle['receipt']['bundle']
raw = (PREVIEW / bundle_ref['path']).read_bytes()
assert hashlib.sha256(raw).hexdigest() == bundle_ref['sha256'] == maintenance['backup']['evidence_sha256'] == r['checkpoint_evidence_archive_receipt']['sha256']
assert len(raw) == 42618 and bundle['objects'] == 14 and len(bundle['ancestors']) == 12
assert bundle['proof']['object_id'] == maintenance['backup']['completion_object']
refs[str(PREVIEW / bundle_ref['path'])] = {'sha256': bundle_ref['sha256'], 'bytes': len(raw)}
receipt = load(OLD / 'configuration-receipt.json')
assert load(OUT / 'current-boot-ledger.json') == receipt['ledger_after']
assert receipt['ledger_after']['phase'] == 'closed'

wrapper_review = load(OUT / 'daily-wrapper-review.json')
journals = []
for name in ('noon', 'evening'):
    p = OUT / f'daily-{name}-system-journal.jsonl'
    journals.extend(json.loads(line) for line in p.read_text().splitlines())
invocations = {}
for rid, w in wrapper_review.items():
    inv = w['invocation_id']
    user_records = [x for x in journals if x.get('_SYSTEMD_INVOCATION_ID') == inv]
    done = [x for x in journals if x.get('INVOCATION_ID') == inv and x.get('JOB_RESULT') == 'done']
    assert user_records and done
    assert all(x['_UID'] == '1000' for x in user_records)
    assert any(str(RELEASE / 'daily/.venv/bin/python') in x.get('_CMDLINE', '') for x in user_records)
    invocations[rid] = {'invocation_id': inv, 'uid': 1000, 'manager_job_done': True,
                        'process_ids': sorted({x['_PID'] for x in user_records}), 'fixed_command_verified': True}

old = load(OLD / 'daily-last-before-repair.json')['work_result']['result']
review = load(OUT / 'daily-review.json')
old_pending = {t['id']: t for t in old['pending']}
needs = next(t for t in review['remaining'] if t['state'] == 'needs_review')
assert old_pending[needs['id']]['state'] == 'needs_review'
assert old_pending[needs['id']]['reason'] == needs['reason']
with closing(sqlite3.connect((ROOT / 'state/catalog.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
    oi_attempts = [json.loads(db.execute('SELECT record FROM daily_attempts WHERE id=?', (a,)).fetchone()[0]) for a in needs['attempts']]
assert all(a['finished_at'] < '2026-10-16T13:23:43' for a in oi_attempts)
(OUT / 'preexisting-oi-attempts.json').write_text(json.dumps(oi_attempts, ensure_ascii=False, indent=2) + '\n')

event_review = {}
for job in ('oi', 'oi-probe', 'oi-quality', 'daily', 'pack', 'backup', 'restic-archive', 'network-observer'):
    p = ROOT / f'state/scheduled-clock-policy/{job}/decisions.jsonl'
    terminal = Counter()
    problems = []
    # Capture a bounded prefix of the existing JSONL. It can keep growing.
    size = p.stat().st_size
    with p.open('rb') as f:
        raw = f.read(size)
    lines = raw.split(b'\n')
    if lines[-1]:
        lines.pop()
    for line in lines:
        if b'request_finished' not in line:
            continue
        event = json.loads(line)
        if event.get('kind') != 'request_finished':
            continue
        terminal[event['state']] += 1
        if event.get('errors') or event['state'] in ('finalization_failed', 'failed'):
            problems.append(event)
    event_review[job] = {'source': str(p), 'prefix_bytes': size, 'prefix_sha256': hashlib.sha256(raw).hexdigest(),
                         'request_finished_counts': dict(terminal), 'problem_events': problems}
all_problems = [x for job in event_review.values() for x in job['problem_events']]
(OUT / 'terminal-events-review.json').write_text(json.dumps(event_review, ensure_ascii=False, indent=2) + '\n')
unexpected = [x for x in all_problems if x['request_id'] != 'a702240f54774dacab107dc28fa2a776']
lock_ids = {'3ec22a5fbf1b418e8c97e0034f7e57f7', '475cba150a7647abbd9d55535118f479'}
assert {x['request_id'] for x in unexpected} == lock_ids and len(unexpected) == 2
lock_journal = [json.loads(line) for line in (OUT / 'restic-lock-system-journal.jsonl').read_text().splitlines()]
lock_recovery = []
for event in unexpected:
    assert event['exit_code'] == 75 and not event['errors'] and event['monitor']['stopped']
    logs = [x for x in lock_journal if x['_SYSTEMD_INVOCATION_ID'] == event['invocation_id']]
    assert len(logs) == 1 and json.loads(logs[0]['MESSAGE'])['status'] == 'lock_busy'
    attempts = []
    for p in (ROOT / 'state/scheduled-clock-policy/restic-archive/attempts' / event['request_id']).glob('*.json'):
        a = load(p)
        attempts.append(a)
        dest = OUT / 'restic-lock-recovery' / p.name
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(p.read_bytes())
    failed = next(a for a in attempts if a['attempt']['id'] == event['attempt_id'])
    complete = [a for a in attempts if a['state'] == 'complete']
    assert len(complete) == 1
    done = complete[0]
    assert all(failed[k] == done[k] for k in ('request_id', 'identity', 'scope', 'pins', 'arguments'))
    assert contract.classify('restic-archive', done['work_exit_code'], done['work_result'], done['pins'], done['arguments']) == done['disposition']
    result = done['work_result']['result']
    assert result['context']['classification'] == 'verified_timer'
    assert not done['attempt']['errors'] and done['attempt']['monitor']['stopped']
    lock_recovery.append({'request_id': event['request_id'], 'failed_attempt': event['attempt_id'],
                          'failed_invocation': event['invocation_id'], 'original_state': 'failed', 'exit_code': 75,
                          'reason_from_system_journal': 'lock_busy',
                          'lock_at': dt.datetime.fromtimestamp(int(logs[0]['__REALTIME_TIMESTAMP']) / 1e6, dt.timezone.utc).isoformat(),
                          'completed_attempt': done['attempt']['id'], 'completed_invocation': done['attempt']['invocation_id'],
                          'resumed_at': result['started_at'], 'finished_at': result['finished_at'],
                          'same_request_identity_scope_pins': True, 'processed': result['archive']['result']['processed'],
                          'resumed_from_system_timer': True})

result = {
    'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'passed': True,
    'daily_system_invocations': invocations,
    'backup': {'maintenance_id': maintenance['id'], 'backup_id': r['backup_id'], 'reused': False,
               'request_id': wrappers['backup']['request_id'], 'invocation_id': wrappers['backup']['attempt']['invocation_id'],
               'completion_object': maintenance['backup']['completion_object'], 'evidence_sha256': bundle_ref['sha256']},
    'restic': {'snapshot_id': r['snapshot_id'], 'archive_run_id': archive['archive']['run_id'],
               'system_timer_verified': True, 'request_id': wrappers['restic-archive']['request_id'],
               'invocation_id': wrappers['restic-archive']['attempt']['invocation_id'],
               'stored_at': r['archive_finished_at'], 'full_check_finished_at': r['full_check_finished_at'],
               'read_data': True, 'num_errors': 0, 'bundle_bytes': 42618,
               'bundle_objects': 14, 'predecessors': 12,
               'direct_snapshot_read_reused_from': str(PREVIEW / 'verification.json')},
    'boot_ledger_matches_deployment_receipt': True,
    'preexisting_oi_review': {'task_id': needs['id'], 'prior_run': old['id'], 'prior_started_at': old['started_at'],
                             'attempt_ids': needs['attempts'], 'before_initial_T16_deployment': True,
                             'pending_refresh_cycles': [t['cycle'] for t in review['remaining'] if t['purpose'] == 'refresh']},
    'terminal_error_scan': {'known_recovered_decimal_request': True, 'additional_lock_busy_failures': 2,
                            'lock_busy_recovered_with_same_request': lock_recovery,
                            'unresolved_failed_or_finalization_failed_events_in_captured_prefixes': 0,
                            'limits': 'Only committed request_finished records in captured prefixes; not a guarantee against unrecorded crashes.'},
    'reused_sources': refs, 'operational_mutations': 0, 'new_full_check_or_restore': False,
}
(OUT / 'integration-review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'passed': True, 'backup_id': r['backup_id'], 'snapshot_id': r['snapshot_id'],
                  'system_daily_invocations_verified': len(invocations), 'known_decimal_error_preserved': True,
                  'lock_busy_then_same_request_completed': len(lock_recovery),
                  'OI_review_predates_T16': True, 'new_full_check_or_restore': False}, ensure_ascii=False))
