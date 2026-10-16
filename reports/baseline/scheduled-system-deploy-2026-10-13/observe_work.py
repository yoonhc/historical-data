"""Read-only T15 business evidence and first natural collector boundaries.

Run with the installed collector Python. No Store initialization, service command,
HTTP request or backup creation. Only evidence copies/reports are written.
"""
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq
from marketdata.fileio import read_file

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
OUT = Path(__file__).resolve().parent
RAW = ROOT / 'reports/scheduled-system-operating-2026-10-13'
APPLIED = datetime.fromisoformat('2026-10-12T15:32:58.601659+00:00')
CASES = {
    'depth': ('6e366b3fbf884a99b487014bf9459576', '7983bc9be84649518108255aad94bac7'),
    'aggtrade': ('0353c09ed46741b2ab247e2ef1b8e476', '22a78ce70d3d46daaa0edd79fe62ec0c'),
}


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def retain(label, blob):
    digest = sha(blob)
    path = RAW / (label + '-' + digest[:16] + '.gz')
    compressed = gzip.compress(blob, mtime=0)
    if path.exists():
        assert gzip.decompress(path.read_bytes()) == blob
    else:
        with path.open('xb') as handle:
            handle.write(compressed)
    return {'path': str(path.relative_to(ROOT)), 'sha256': sha(path.read_bytes()),
            'uncompressed_sha256': digest, 'uncompressed_bytes': len(blob)}


def source(path):
    blob = path.read_bytes()
    return json.loads(blob), {'source': str(path), **retain(path.name, blob)}


def retained_json(label, value):
    return retain(label, json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode())


def collectors(db, stream, ids):
    items = []
    for identity in ids:
        row = dict(db.execute('SELECT * FROM event_collections WHERE id=?', (identity,)).fetchone())
        row['report'] = json.loads(row['report'])
        row['segments'] = [dict(s) for s in db.execute(
            'SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id', (identity,))]
        items.append(row)
    first, second = items
    link = first['report']['runtime_clock']
    after = second['report']['runtime_clock']
    lower_wall = int(APPLIED.timestamp() * 1e9) - 10_000_000_000
    upper_wall = after['collection_start']['wall_ns'] + 10_000_000_000
    prefix = 'runtime-clock://' + link['run_id'] + '/'
    artifacts = {}
    for row in db.execute('SELECT a.*,f.url,f.metadata FROM artifacts a JOIN fetches f ON f.artifact_id=a.id '
                          'WHERE f.url>=? AND f.url<?', (prefix, prefix + '\uffff')):
        row = dict(row)
        metadata = json.loads(row['metadata'])
        if metadata['last_sample']['wall_ns'] < lower_wall or metadata['first_sample']['wall_ns'] > upper_wall:
            continue
        row['metadata'] = metadata
        artifacts[row['id']] = row
    selected = sorted(artifacts.values(), key=lambda a: a['metadata']['chunk'])
    records, previous, proofs = [], None, []
    for artifact in selected:
        blob = read_file(ROOT, db, artifact['path'])
        assert sha(blob) == artifact['id']
        decoded = gzip.decompress(blob)
        lines = [json.loads(line) for line in decoded.splitlines()]
        header, rows, metadata = lines[0], lines[1:], artifact['metadata']
        assert header['run_id'] == link['run_id'] == metadata['run_id']
        assert header['chunk'] == metadata['chunk']
        assert sha(decoded) == metadata['uncompressed_sha256']
        assert len(rows) == metadata['records']
        assert rows[0]['sample'] == metadata['first_sample'] and rows[-1]['sample'] == metadata['last_sample']
        if previous:
            assert metadata['previous'] == previous['id'] and metadata['chunk'] == previous['metadata']['chunk'] + 1
        previous = artifact
        proofs.append({'artifact': artifact, 'retained': retain(artifact['id'] + '.jsonl', decoded)})
        records.extend(rows)
    records = [r for r in records if lower_wall <= r['sample']['wall_ns'] <= upper_wall]
    assert records
    finish = [r for r in records if r['state'] == 'child_finished' and r.get('child_id') == link['child_id']]
    assert len(finish) == 1
    finish = finish[0]
    starting = next(r for r in records if r['record'] > finish['record'] and r['state'] == 'starting_child')
    running = next(r for r in records if r['record'] > starting['record'] and r['state'] == 'running' and r.get('child_pid'))
    gap = [r for r in records if finish['record'] <= r['record'] <= running['record']]
    identity_keys = ('run_id', 'boot_id', 'supervisor_pid', 'supervisor_start_ticks', 'systemd_invocation_id', 'profile_sha256')
    identities = {k: sorted({str(r[k]) for r in records}) for k in identity_keys}
    samples = [r['sample']['boottime_ns'] for r in records]
    market_proofs = []
    for item in items:
        # One first and one last segment per collection; no full replay or full scan.
        chosen = {s['id']: s for s in item['segments'][:1] + item['segments'][-1:]}
        for s in chosen.values():
            blob = read_file(ROOT, db, s['path'])
            assert sha(blob) == s['file_sha']
            key = 'sequence' if stream == 'depth' else 'agg_trade_id'
            rows = pq.ParquetFile(pa.BufferReader(blob)).read(columns=[key, 'received_at']).to_pylist()
            assert (len(rows), rows[0][key], rows[-1][key]) == (s['row_count'], s['first_id'], s['last_id'])
            raw_artifact = dict(db.execute('SELECT * FROM artifacts WHERE id=?', (s['source_id'],)).fetchone())
            assert sha(read_file(ROOT, db, raw_artifact['path'])) == raw_artifact['id']
            market_proofs.append({'collection_id': item['id'], 'segment': s, 'source': raw_artifact,
                                  'hashes_match': True, 'rows': len(rows), 'first': rows[0], 'last': rows[-1]})
    history = [r for r in records if r.get('history', {}).get('reference_stale_seen')]
    checks = {
        'single_supervisor_identity': all(len(v) == 1 for v in identities.values()),
        'same_collection_run_and_boot': all(link[k] == after[k] for k in ('run_id', 'boot_id')),
        'new_child_id_and_pid': link['child_id'] != after['child_id'] and finish['child_pid'] != running['child_pid'],
        'normal_terminal_report_and_exit': first['state'] == 'complete' and finish['collector_exit_code'] == 0,
        'continuous_selected_records': all(b['record'] == a['record'] + 1 for a, b in zip(records, records[1:])),
        'sample_gap_within_5_seconds': max(b-a for a, b in zip(samples, samples[1:])) <= 5e9,
        'next_child_allowed': starting['decision']['raw_start_allowed'],
        'clock_estimate_valid_at_boundary': all(r['decision']['current_estimate_usable'] for r in gap),
        'boundary_not_refused': not any(r['state'] == 'start_refused_after_commit' for r in gap),
        'past_stale_kept': bool(history) and all(r.get('history', {}).get('reference_stale_seen') for r in records if r['record'] >= history[0]['record']),
        'market_raw_standard_hashes_and_rows': bool(market_proofs),
    }
    details = retained_json(stream + '-boundary', {'collections': items, 'artifacts': proofs,
        'market_sample': market_proofs, 'child_finished': finish, 'next_start': starting,
        'next_running': running, 'boundary_records': gap})
    return {'checks': checks, 'passed': all(checks.values()), 'details': details, 'identities': identities,
            'collections': [{k: i[k] for k in ('id', 'started_at', 'updated_at', 'state')} |
                            {'segments': len(i['segments']), 'stored_rows': sum(s['row_count'] for s in i['segments'])} for i in items],
            'previous_child_id': link['child_id'], 'next_child_id': after['child_id'],
            'previous_pid': finish['child_pid'], 'next_pid': running['child_pid'],
            'clock_artifacts': len(proofs), 'records': len(records),
            'start_decision': starting['decision'], 'start_clock': starting['clock'],
            'max_sample_gap_seconds': max(b-a for a, b in zip(samples, samples[1:])) / 1e9,
            'child_end_to_next_collection_seconds': (after['collection_start']['boottime_ns'] - link['collection_end']['boottime_ns']) / 1e9,
            'post_exit_wait_seconds': (starting['sample']['boottime_ns'] - finish['sample']['boottime_ns']) / 1e9,
            'market_segments_checked': len(market_proofs), 'market_rows_checked': sum(p['rows'] for p in market_proofs)}


def main():
    RAW.mkdir(exist_ok=True)
    report = {'observed_at': datetime.now(timezone.utc).isoformat(), 'operating_mutations': False, 'sources': {}, 'errors': []}
    for name in ('oi-latest.json', 'oi-probe-latest.json', 'daily-scheduler-latest.json', 'maintenance-hourly-latest.json', 'maintenance-daily-latest.json'):
        report[name], report['sources'][name] = source(ROOT / 'state' / name)
    pointer, report['sources']['oi-quality-latest'] = source(ROOT / 'reports/oi-quality/latest.json')
    quality, report['sources']['oi-quality-report'] = source(Path(pointer['path']) / 'report.json')
    assert sha((Path(pointer['path']) / 'report.json').read_bytes()) == pointer['files']['report.json']
    obs = quality['observations']
    report['oi_quality'] = {k: quality[k] for k in ('report_id', 'generated_at', 'needs_review', 'comparison_start', 'comparison_end', 'unit_status')}
    report['oi_quality'].update({'parity_totals': quality['parity']['totals'], 'parity_anomalies': quality['parity']['anomalies'],
        'counts': obs['counts'], 'anomalies': obs['anomalies'], 'probe_gaps_over_45_seconds': obs['probe_gaps_over_45_seconds']})
    report['network'], report['sources']['network'] = source(ROOT / 'reports/network-observer/latest.json')
    report['restic'] = []
    for p in sorted((ROOT / 'reports/restic-archive-runs/scheduled').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        value = json.loads(p.read_bytes())
        if datetime.fromisoformat(value['started_at']) < APPLIED:
            break
        value, retained = source(p)
        ctx = value['context']
        report['restic'].append({'source': retained, 'run_id': value['run_id'], 'started_at': value['started_at'],
            'finished_at': value['finished_at'], 'ok': value['ok'], 'context': ctx,
            'processed': value.get('archive', {}).get('result', {}).get('processed'), 'counted_dates': value['counted_dates']})
    with sqlite3.connect((ROOT / 'state/catalog.sqlite').as_uri() + '?mode=ro', uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        report['collectors'] = {}
        for stream, ids in CASES.items():
            try:
                report['collectors'][stream] = collectors(db, stream, ids)
            except Exception as exc:
                report['errors'].append({'stream': stream, 'error': f'{type(exc).__name__}: {exc}'})
        db.rollback()
    report['passed'] = not report['errors'] and all(c['passed'] for c in report['collectors'].values())
    path = OUT / ('work-observation-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    with path.open('x') as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write('\n')
    print(json.dumps({'report': str(path), 'boundary_passed': report['passed'], 'errors': report['errors'],
                     'boundaries': {s: {k: c[k] for k in ('checks', 'clock_artifacts', 'records', 'child_end_to_next_collection_seconds', 'max_sample_gap_seconds')} for s, c in report['collectors'].items()},
                     'system_restic_runs': len(report['restic'])}, indent=2))


if __name__ == '__main__':
    main()
