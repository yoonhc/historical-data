"""Capture read-only operational evidence for this review."""

import collections
import datetime as dt
import json
from pathlib import Path
import sqlite3
import subprocess

ROOT = Path.cwd()
OUT = ROOT / 'reports/observation-2026-09-25'
START = '2026-09-23T23:30:00+00:00'
END = '2026-09-24T23:30:00+00:00'

def save(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')

codes = {}
for command in ['aggtrade-status', 'depth-status', 'maintenance-status', 'status', 'storage']:
    result = subprocess.run([str(ROOT / '.venv/bin/marketdata'), command], capture_output=True, text=True)
    codes[command] = result.returncode
    if result.stdout:
        save(command + '.json', json.loads(result.stdout))
    if result.stderr:
        (OUT / (command + '.stderr')).write_text(result.stderr)
save('command-exit-codes.json', codes)

db = sqlite3.connect((ROOT / 'state/catalog.sqlite').as_uri() + '?mode=ro', uri=True)
db.row_factory = sqlite3.Row
db.execute('BEGIN')
records = list(db.execute("SELECT * FROM event_collections WHERE started_at >= ? ORDER BY started_at", ('2026-09-23T07:00:17+00:00',)))
summaries = []
for record in records:
    report = json.loads(record['report'])
    keys = ['id', 'started_at', 'updated_at', 'finished_at', 'last_received_at', 'state', 'complete', 'connections', 'connection_errors', 'messages', 'persisted_messages', 'detected_gaps', 'repaired_gaps', 'unrepaired_gaps', 'sync_losses', 'snapshots', 'synchronized', 'errors', 'recent_errors', 'unreconstructed_updates', 'lagged_snapshots', 'lagged_snapshot_retries', 'verify_seconds', 'rotations']
    item = {k: report[k] for k in keys if k in report}
    item.update(collection_id=record['id'], dataset=record['dataset'], kind=record['kind'], db_state=record['state'], db_updated_at=record['updated_at'])
    item['overlaps_window'] = record['started_at'] < END and record['updated_at'] >= START
    if 'epochs' in report:
        item['epochs'] = []
        for epoch in report['epochs']:
            final = epoch.get('final') or {}
            checks = final.get('checks', [])
            item['epochs'].append({
                'epoch': epoch['epoch'], 'started_at': epoch.get('started_at'), 'reason': epoch.get('reason'),
                'error': final.get('error'), 'counts': final.get('counts'), 'synced': (final.get('book') or {}).get('synced'),
                'pending_check_sequence': final.get('pending_check_sequence'),
                'checks': len(checks), 'equal': sum(bool(c.get('equal')) for c in checks),
                'uncomparable': sum(not c.get('comparable') for c in checks),
                'unequal_comparable': sum(bool(c.get('comparable')) and not c.get('equal') for c in checks)
            })
    summaries.append(item)
save('collections.json', summaries)

# Logical bytes of segments intersecting the fixed time window; boundary files
# can contain a few seconds outside it. No backup, catalog or ZIP header bytes.
sizes = collections.defaultdict(lambda: collections.Counter())
seen_raw = set()
missing = []
for row in db.execute('SELECT s.*, a.bytes AS raw_bytes FROM event_segments s JOIN artifacts a ON a.id=s.source_id WHERE s.first_time < ? AND s.last_time >= ?', (END, START)):
    bucket = sizes[row['dataset']]
    bucket['segments'] += 1
    bucket['rows_in_intersecting_segments'] += row['row_count']
    if row['source_id'] not in seen_raw:
        bucket['raw_bytes'] += row['raw_bytes']
        seen_raw.add(row['source_id'])
    packed = db.execute('SELECT bytes FROM packed_files WHERE path=?', (row['path'],)).fetchone()
    if packed:
        bucket['canonical_bytes'] += packed['bytes']
    else:
        try:
            bucket['canonical_bytes'] += (ROOT / row['path']).stat().st_size
        except FileNotFoundError:
            missing.append(row['path'])
save('window-storage.json', {'start': START, 'end': END, 'datasets': dict(sizes), 'missing_size_paths': missing, 'semantics': 'all bytes of intersecting immutable segments, including boundary overhang; excludes catalog/ZIP overhead, OI/bars, exports and backups'})

maintenance = []
for path in sorted((ROOT / 'reports/maintenance').glob('*/*.json')):
    report = json.loads(path.read_text())
    if START <= report['started_at'] < END:
        maintenance.append({k: report.get(k) for k in ['id', 'started_at', 'finished_at', 'mode', 'state', 'ok', 'error', 'validation', 'backup', 'pending_prune', 'new_prune']})
save('maintenance-window.json', maintenance)

for pattern in ['*quality*', 'daily-latest.json', 'oi-latest.json', 'oi-probe-latest.json', 'maintenance-daily-latest.json']:
    for path in (ROOT / 'state').glob(pattern):
        if path.is_file() and path.suffix == '.json':
            save(path.name, json.loads(path.read_text()))

print(json.dumps({'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'command_exit_codes': codes,
 'collection_states_all': dict(collections.Counter((x['dataset'],x['db_state']).__str__() for x in summaries)),
 'window_states': dict(collections.Counter((x['dataset'],x['db_state']).__str__() for x in summaries if x['overlaps_window'])),
 'storage': dict(sizes), 'size_missing': len(missing), 'maintenance_runs': len(maintenance),
 'maintenance_failures': [x for x in maintenance if not x['ok']]}, ensure_ascii=False, indent=2))
