"""Targeted T16 evidence capture. Reads production; writes only this report directory.

Does not construct Store, run jobs, call systemctl, query Chrony, or invoke restic.
No whole-store validation or restore is performed.
"""
from contextlib import closing
from pathlib import Path
from collections import Counter
import datetime as dt
import hashlib
import importlib.util
import json
import sqlite3

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
OUT = Path(__file__).resolve().parent
RELEASE = Path('/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11')
JOBS = ('oi', 'oi-probe', 'oi-quality', 'daily', 'pack', 'backup', 'restic-archive', 'network-observer')
RUN_IDS = ('209157f2a455427a94ab15033958d92a', 'f27385967718423dbe93f1360ca054fd')
FOLLOW_IDS = ('35f3459b23194203a687a45989589e66', '4c2d80de76d04b859d074083ad63b61e')
sources = []


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(name, obj):
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def copy(path, name):
    path = Path(path)
    raw = path.read_bytes()
    dest = OUT / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    sources.append({'path': str(path), 'copy': name, 'sha256': sha(raw), 'bytes': len(raw), 'read_at': stamp()})
    return json.loads(raw)


spec = importlib.util.spec_from_file_location('t16_readonly_contract', RELEASE / 'policy/request_contract.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def request_check(request):
    disposition = contract.classify(request['job'], request['work_exit_code'], request['work_result'], request['pins'], request['arguments'])
    assert disposition == request['disposition'], 'stored disposition mismatch'
    assert request['state'] == 'complete'
    assert not request['attempt']['errors']
    assert request['attempt']['monitor']['stopped'] is True
    assert request['attempt']['monitor']['last_time_fault'] is None
    current = contract.inputs(request['job'], request['arguments'], Path('/etc/marketdata/scheduled-clock-policy/clock-binding.json'), RELEASE)
    assert current == request['scope'], 'input identity changed'
    return {'request_id': request['request_id'], 'attempt_id': request['attempt']['id'],
            'invocation_id': request['attempt']['invocation_id'], 'disposition': disposition,
            'monitor': request['attempt']['monitor'], 'work': request['attempt']['work'],
            'scope_matches_current_inputs': True, 'stored_evidence_verified': True}


started = stamp()
current = {}
for job in JOBS:
    r = copy(ROOT / f'state/scheduled-clock-policy/{job}/request.json', f'current/{job}.json')
    current[job] = request_check(r)
write('current-requests-review.json', {'started_at': started, 'finished_at': stamp(), 'jobs': current,
                                    'note': 'Per-file read times; not one global atomic snapshot.'})

dbpath = ROOT / 'state/catalog.sqlite'
with closing(sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    records = {}

    def record(table, identity):
        raw = db.execute(f'SELECT record FROM {table} WHERE id=?', (identity,)).fetchone()[0]
        records[f'{table}/{identity}'] = {'table': table, 'id': identity, 'record_sha256': sha(raw.encode()), 'record': json.loads(raw)}
        return json.loads(raw)

    all_tasks = [json.loads(r[0]) for r in db.execute('SELECT record FROM daily_tasks')]
    all_attempts = [json.loads(r[0]) for r in db.execute('SELECT record FROM daily_attempts')]
    all_runs = [json.loads(r[0]) for r in db.execute("SELECT record FROM daily_runs WHERE json_extract(record,'$.started_at') >= '2026-10-16T15:17:06' ORDER BY json_extract(record,'$.started_at')")]
    selected = {}
    file_checks = {}
    catalog_refs = {}

    def check_file(relative, digest):
        if relative in file_checks:
            assert file_checks[relative]['expected_sha256'] == digest
            return
        path = ROOT / relative
        assert path.is_file(), f'Expected selected loose artifact: {relative}'
        raw = path.read_bytes()
        actual = sha(raw)
        assert actual == digest, f'File hash differs: {relative}'
        file_checks[relative] = {'expected_sha256': digest, 'actual_sha256': actual, 'bytes': len(raw), 'read_at': stamp()}

    def row(table, identity):
        obj = dict(db.execute(f'SELECT * FROM {table} WHERE id=?', (identity,)).fetchone())
        catalog_refs[f'{table}/{identity}'] = obj
        return obj

    for runid in RUN_IDS:
        run = record('daily_runs', runid)
        assert run['runner_ok'] and run['run_outcome'] == 'tasks_processed'
        attempts = [a for a in all_attempts if run['started_at'] <= a['started_at'] <= run['finished_at']]
        assert {a['task_id'] for a in attempts} == set(run['processed'])
        aids = {a['id'] for a in attempts}
        related = [t for t in all_tasks if t['id'] in run['processed'] or t.get('covered_by') in aids]
        assert all(t['state'] == 'complete' for t in related)
        for t in related:
            record('daily_tasks', t['id'])
        for a in attempts:
            record('daily_attempts', a['id'])
            assert a['state'] == 'complete'
            proof = a['proof']
            task = next(t for t in related if t['id'] == a['task_id'])
            key = task['key']
            keyid = ':'.join(['binance', key['market'], key['symbol'], key['dataset'], key['interval'], key['series']])
            assert run['clock_observation']['lower_utc_ns'] / 1e9 >= dt.datetime.fromisoformat(task['first_eligible_at']).timestamp()
            for qid in proof['query_ids']:
                q = row('queries', qid)
                assert q['key_id'] == keyid and q['source_id'] in proof['source_ids']
            for rid, digest in zip(proof['revision_ids'], proof['file_sha256'], strict=True):
                rev = row('revisions', rid)
                assert rev['key_id'] == keyid and rev['file_sha'] == digest
                check_file(rev['path'], digest)
            for sid in sorted(set(proof['source_ids']) | {i['id'] for i in a['inputs']}):
                artifact = row('artifacts', sid)
                check_file(artifact['path'], sid)
        selected[runid] = {
            'started_at': run['started_at'], 'finished_at': run['finished_at'],
            'attempt_ids': sorted(aids), 'processed_task_ids': run['processed'],
            'related_task_ids': sorted(t['id'] for t in related),
            'purposes': dict(Counter(t['purpose'] for t in related if t['id'] in run['processed'])),
            'request_count': sum(len(a['requests']) for a in attempts),
            'http_statuses': dict(Counter(str(q['status']) for a in attempts for q in a['requests'])),
            'rows_reported': sum(item.get('rows', 0) for a in attempts for item in a['result']),
            'state_counts_at_run': run['state_counts'], 'reference_age_seconds': run['clock_observation']['reference_age_seconds'],
            'total_uncertainty': run['clock_observation']['total_uncertainty'],
            'all_related_still_complete': True,
            'attempt_counts_for_related_tasks_now': {t['id']: len(t['attempts']) for t in related},
        }
    for runid in FOLLOW_IDS:
        r = record('daily_runs', runid)
        assert r['processed'] == [] and r['run_outcome'] == 'no_due_tasks'
        assert not [a for a in all_attempts if r['started_at'] <= a['started_at'] <= r['finished_at']]
    first = all_runs[0]
    latest = all_runs[-1]
    record('daily_runs', latest['id'])
    remaining = [t for t in all_tasks if t['state'] != 'complete']
    for t in remaining:
        record('daily_tasks', t['id'])
    # Acquisition identity does not contain the refresh cycle.
    repeated = [t['id'] for t in all_tasks if t['purpose'] == 'acquire' and t['state'] == 'complete' and len(t['attempts']) > 1]
    later_cycles = {cycle: Counter() for cycle in ('2026-10-17', '2026-10-18')}
    for t in all_tasks:
        if t['cycle'] in later_cycles:
            later_cycles[t['cycle']][t['purpose'] + ':' + t['state']] += 1
    write('daily-records.json', {'read_at': stamp(), 'database': str(dbpath), 'records': records,
                                'catalog_refs': catalog_refs, 'one_read_transaction': True})
    write('daily-review.json', {'selected_runs': selected, 'zero_attempt_followup_runs': list(FOLLOW_IDS),
          'latest_run': latest['id'], 'latest_started_at': latest['started_at'], 'latest_state_counts': latest['state_counts'],
          'remaining': remaining, 'first_post_repair_state_counts': first['state_counts'],
          'completed_acquisitions_with_multiple_attempts': repeated,
          'cycles': {k: dict(v) for k, v in later_cycles.items()},
          'file_count': len(file_checks), 'file_bytes_read': sum(x['bytes'] for x in file_checks.values()),
          'note': 'Targeted exact proof/file hashes only; not whole-store validation or a new backup/restore.'})
    write('selected-file-hashes.json', file_checks)

wrappers = {}
for p in (ROOT / 'state/scheduled-clock-policy/daily/attempts').glob('*/*.json'):
    r = json.loads(p.read_bytes())
    runid = (r.get('work_result') or {}).get('result', {}).get('id')
    if runid in RUN_IDS + FOLLOW_IDS:
        saved = copy(p, f'daily-wrapper/{runid}.json')
        wrappers[runid] = request_check(saved)
assert set(wrappers) == set(RUN_IDS + FOLLOW_IDS)
write('daily-wrapper-review.json', wrappers)
requests = {v['request_id'] for v in wrappers.values()}
events = []
with (ROOT / 'state/scheduled-clock-policy/daily/decisions.jsonl').open() as f:
    for line in f:
        event = json.loads(line)
        if event.get('request_id') in requests:
            events.append(event)
write('daily-decisions.json', events)
write('source-copies.json', sources)
print(json.dumps({'captured_at': stamp(), 'jobs': {j: c['disposition'] for j, c in current.items()},
                  'daily': {k: {q: v[q] for q in ('purposes', 'request_count', 'rows_reported', 'reference_age_seconds', 'total_uncertainty')} for k, v in selected.items()},
                  'file_count': len(file_checks), 'bytes_read': sum(x['bytes'] for x in file_checks.values()),
                  'wrapper_invocations': {k: v['invocation_id'] for k, v in wrappers.items()},
                  'copied_sources': len(sources)}, ensure_ascii=False, indent=2))
