"""Read-only follow-up of the first two post-cutover sessions; no network requests."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time

import pyarrow as pa
import pyarrow.parquet as pq
from marketdata import depth
from marketdata.fileio import read_file
from marketdata.store import Store


ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
OUT = Path(__file__).resolve().parent
FIRST = {'depth': 'bc6bc9d2cb6149ba864f0d2003adf3af',
         'aggtrade': 'c36431bac84d4092b5f1ca33b4740af4'}


def save(name, value):
    with (OUT / name).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.write('\n')


def open_ro():
    db = sqlite3.connect((ROOT / 'state/catalog.sqlite').as_uri() + '?mode=ro',
                         uri=True, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def record(db, identity):
    item = dict(db.execute('SELECT * FROM event_collections WHERE id=?', (identity,)).fetchone())
    item['report'] = json.loads(item['report'])
    item['segments'] = [dict(r) for r in db.execute(
        'SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id', (identity,))]
    return item


def metadata(item):
    r = item['report']
    fields = ('messages', 'persisted_messages', 'snapshots', 'complete', 'sync_losses',
              'synchronized', 'connection_errors', 'unreconstructed_messages',
              'first_id', 'last_id', 'last_received_at', 'previous_persisted_id',
              'resume_after_id', 'detected_gaps', 'repaired_gaps', 'unrepaired_gaps')
    return {k: item[k] for k in ('id', 'dataset', 'kind', 'started_at', 'updated_at', 'state')} | {
        k: r[k] for k in fields if k in r} | {'segments': len(item['segments']),
        'stored_rows': sum(s['row_count'] for s in item['segments'])}


def check_segments(db, item, stream, full):
    segments = item['segments'] if full else item['segments'][:2] + item['segments'][-2:]
    issues, samples, raw_samples = [], [], []
    kinds = Counter()
    total = reversals = 0
    previous = previous_recv = first_recv = last_recv = None
    first_id = last_id = None
    cols = (['sequence', 'kind', 'epoch', 'received_at', 'final_update_id', 'previous_update_id']
            if stream == 'depth' else ['agg_trade_id', 'received_at'])
    raw_ids = {s['id'] for s in item['segments'][:2] + item['segments'][-2:]}
    if stream == 'depth':
        for s in item['segments']:
            if s['first_id'] <= 5580 <= s['last_id'] or s['first_id'] <= 5581 <= s['last_id']:
                raw_ids.add(s['id'])
    for segment in segments:
        blob = read_file(ROOT, db, segment['path'])
        if hashlib.sha256(blob).hexdigest() != segment['file_sha']:
            issues.append({'kind': 'canonical_hash', 'path': segment['path']})
        rows = pq.ParquetFile(pa.BufferReader(blob)).read(columns=cols).to_pylist()
        total += len(rows)
        if len(rows) != segment['row_count']:
            issues.append({'kind': 'row_count', 'id': segment['id']})
        for row in rows:
            identity = row['sequence'] if stream == 'depth' else row['agg_trade_id']
            if full and previous is not None and identity != previous + 1:
                issues.append({'kind': 'sequence_or_id_gap', 'previous': previous, 'next': identity})
            previous = identity
            first_id = identity if first_id is None else first_id
            last_id = identity
            kind = row['kind'] if stream == 'depth' else item['kind']
            kinds[kind] += 1
            if stream == 'depth' and 5579 <= identity <= 5582:
                samples.append(row)
            if kind in ('update', 'websocket', 'rest'):
                recv = row['received_at']
                if previous_recv is not None and recv < previous_recv:
                    reversals += 1
                previous_recv = recv
                first_recv = recv if first_recv is None else first_recv
                last_recv = recv
        if segment['id'] in raw_ids:
            source = dict(db.execute('SELECT * FROM artifacts WHERE id=?', (segment['source_id'],)).fetchone())
            raw = read_file(ROOT, db, source['path'])
            matches = hashlib.sha256(raw).hexdigest() == source['id']
            if not matches:
                issues.append({'kind': 'raw_hash', 'path': source['path']})
            raw_samples.append({'segment_id': segment['id'], 'raw_path': source['path'],
                                'bytes': len(raw), 'sha256_matches': matches})
    receive_source = 'rest' if item['kind'] == 'rest' else 'ws'
    return {'full_collection': full, 'segments_checked': len(segments), 'rows_checked': total,
            'kind_counts': dict(kinds), 'first_id': first_id, 'last_id': last_id,
            f'first_{receive_source}_received_at': first_recv,
            f'last_{receive_source}_received_at': last_recv,
            'receive_time_reversals': reversals, 'issues': issues,
            'failure_boundary_rows': samples, 'raw_hash_samples': raw_samples}


def clock_logs():
    results = []
    for stream in FIRST:
        for path in sorted((ROOT / 'state/runtime-clock' / stream).glob('*.jsonl')):
            with path.open() as f:
                first = json.loads(next(f))
            at = first['sample']['wall_ns'] / 1e9
            if not 1791472200 < at < 1791476400:
                continue
            blob = path.read_bytes()
            rows = [json.loads(line) for line in blob.splitlines()]
            active = [r for r in rows if r.get('child_pid')]
            if not active:
                continue  # ExecStartPre readiness probes are not running collectors.
            running = [r for r in rows if r['state'] == 'running']
            changes, prev = [], None
            for row in rows:
                state = (row['state'], row.get('clock_usable'), tuple(row.get('reasons', [])))
                if state != prev:
                    changes.append(row)
                    prev = state
            results.append({'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(blob).hexdigest(),
                            'bytes': len(blob), 'records': len(rows), 'stream': stream,
                            'run_id': first['run_id'], 'invocation_id': first['systemd_invocation_id'],
                            'supervisor_pid': first['supervisor_pid'], 'child_pid': active[0]['child_pid'],
                            'start': first['sample'], 'end': rows[-1]['sample'],
                            'last_state': rows[-1]['state'], 'exit_code': rows[-1].get('collector_exit_code'),
                            'session_clock_clean': rows[-1].get('session_clock_clean'),
                            'violations': rows[-1].get('violations'),
                            'running_records': len(running),
                            'running_unusable_records': sum(not r['clock_usable'] for r in running),
                            'provider_reasons': dict(Counter(reason for r in running for reason in r.get('clock', {}).get('reasons', []))),
                            'max_reference_age_seconds': max(r.get('clock', {}).get('reference_age_seconds', 0) for r in running),
                            'max_abs_correction_seconds': max(abs(r.get('clock', {}).get('tracking', {}).get('correction', 0)) for r in running),
                            'max_error_bound_seconds': max(r.get('clock', {}).get('conditional_error_bound_seconds', 0) for r in running),
                            'selected_original_records': changes})
    return sorted(results, key=lambda r: (r['stream'], r['start']['wall_ns']))


def journal():
    cmd = ['journalctl', '-b', '--since', '2026-10-09 00:10:50', '--until', '2026-10-09 01:12:20',
           '-u', 'marketdata-collector@depth.service', '-u', 'marketdata-collector@aggtrade.service',
           '--no-pager', '--all', '-o', 'json']
    proc = subprocess.run(cmd, capture_output=True, timeout=30, check=True)
    rows = [json.loads(line) for line in proc.stdout.splitlines()]
    selected = [r for r in rows if r.get('_PID') == '1' or any(
        token in r.get('MESSAGE', '') for token in ('"state": "starting"', '"state": "finished"'))]
    return {'command': cmd, 'queried_records': len(rows), 'output_sha256': hashlib.sha256(proc.stdout).hexdigest(),
            'stderr': proc.stderr.decode(), 'selected_original_records': selected,
            'scope': 'Service lifecycle and runtime start/finish records; progress output omitted.'}


def preserved_state():
    paths = ['/etc/marketdata/runtime.json', '/etc/marketdata/clock-bootstrap.json',
             '/etc/chrony/marketdata.conf', '/etc/adjtime']
    masks = ('marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer')
    return {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'config_sha256': {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths},
            'automatic_retirement_links': {n: str((Path.home()/'.config/systemd/user'/n).readlink()) for n in masks}}


def main():
    started = time.monotonic()
    before = preserved_state()
    db = open_ro()
    collections, verification = {}, {}
    try:
        for stream, identity in FIRST.items():
            first = record(db, identity)
            next_id = db.execute("SELECT id FROM event_collections WHERE dataset=? AND kind='websocket' "
                                 'AND started_at>? ORDER BY started_at LIMIT 1',
                                 (first['dataset'], first['started_at'])).fetchone()['id']
            following = record(db, next_id)
            recoveries = {r['collection_id']: record(db, r['collection_id'])
                          for item in (first, following) for r in item['report'].get('recent_recoveries', [])}
            collections[stream] = {'first': first, 'next': following, 'recoveries': recoveries}
        save('collections.json', collections)
        for stream, records in collections.items():
            verification[stream] = {'first': check_segments(db, records['first'], stream, True),
                                    'next_boundaries': check_segments(db, records['next'], stream, False)}
            print('stored_files_checked', stream, flush=True)
        recovery = collections['aggtrade']['recoveries']['f63766c02bab44dea5ab59c4c790f381']
        verification['next_aggtrade_recovery'] = check_segments(db, recovery, 'aggtrade', True)
        save('stored-files.json', verification)
        reader = Store.__new__(Store)  # Avoid Store.__init__ schema/writer side effects.
        reader.root, reader.db, reader._files_db = ROOT, db, db
        replay = depth.replay(reader, FIRST['depth'], max_rows=40000)
        save('depth-replay.json', replay)
        print('depth_replayed', replay['rows'], replay['replay_matches_live'], replay['valid'], flush=True)
        clocks = clock_logs()
        save('clock-observation.json', clocks)
        logs = journal()
        save('service-boundary-journal.json', logs)
        after = preserved_state()
        checks = {
            'first_final_partial_preserved': all(r['first']['state'] == 'partial' and not r['first']['report']['complete'] for r in collections.values()),
            'first_received_counts_equal_persisted': all(r['first']['report']['messages'] == r['first']['report']['persisted_messages'] for r in collections.values()),
            'first_stored_counts_equal_report': verification['depth']['first']['kind_counts']['update'] == collections['depth']['first']['report']['persisted_messages'] and verification['aggtrade']['first']['rows_checked'] == collections['aggtrade']['first']['report']['persisted_messages'],
            'stored_subset_hashes_counts_and_sequences_match': all(not result['issues'] and not result['receive_time_reversals'] for stream in FIRST for result in verification[stream].values()) and not verification['next_aggtrade_recovery']['issues'],
            'depth_replay_matches_with_quality_rejection': replay['replay_matches_live'] and not replay['valid'],
            'first_runtime_logs_clean_and_terminal': len(clocks) == 4 and all(r['last_state'] == 'finished' and r['session_clock_clean'] and r['violations'] == 0 and r['running_unusable_records'] == 0 for r in clocks),
            'configuration_boot_and_mask_unchanged': before == after and all(p == '/dev/null' for p in after['automatic_retirement_links'].values()),
        }
        summary = {'observed_at': datetime.now(timezone.utc).isoformat(), 'scope': 'T02 first session and next startup; not the 24-hour observation or T11 new-supervisor boundary',
                   'read_only_catalog': True, 'release_module': depth.__file__, 'before': before, 'after': after,
                   'collections': {stream: {k: metadata(r[k]) for k in ('first', 'next')} for stream, r in collections.items()},
                   'checks': checks, 'passed': all(checks.values()), 'elapsed_seconds': time.monotonic()-started,
                   'pending_24h_review_not_before_kst': '2026-10-10T00:20:00+09:00'}
        save('result.json', summary)
        print(json.dumps({'passed': summary['passed'], 'checks': checks, 'elapsed_seconds': summary['elapsed_seconds']}, ensure_ascii=False))
    finally:
        db.close()


if __name__ == '__main__':
    main()
