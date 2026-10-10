"""Read-only verification of the authorized 2026-10-10 daily rerun."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
HERE = Path(__file__).resolve().parent


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def save(name, value):
    with (HERE / name).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.write('\n')


def verify():
    import pyarrow as pa
    import pyarrow.parquet as pq
    from marketdata.fileio import read_file

    before = json.loads((HERE / 'before.json').read_text())
    previous = json.loads((HERE / 'before-daily-latest.json').read_text())
    blob = (HERE / 'after-daily-latest.json').read_bytes()
    after = json.loads(blob)
    assert len(after) == 8 and all(r['state'] == 'complete' and not r['errors'] for r in after)
    assert (ROOT / 'state/daily-latest.json').read_bytes() == blob
    keys = [r['key'] for r in previous if r['state'] == 'partial']
    assert len(keys) == 4
    result = {'observed_at': datetime.now(timezone.utc).isoformat(), 'sqlite_mode': 'ro',
              'daily_sha256': sha(blob), 'targets_complete': len(after),
              'resolved_date': '2026-10-09', 'resolved': [], 'runs': []}
    db = sqlite3.connect('file:' + str(ROOT / 'state/catalog.sqlite') + '?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    try:
        for key in keys:
            record = db.execute('SELECT r.* FROM active a JOIN revisions r ON r.id=a.revision_id '
                                'WHERE a.key_id=? AND a.day=?', (key, '2026-10-09')).fetchone()
            assert record is not None and record['created_at'] > before['observed_at']
            record = dict(record)
            canonical = read_file(ROOT, db, record['path'])
            assert sha(canonical) == record['file_sha']
            table = pq.ParquetFile(pa.BufferReader(canonical)).read()
            step = 300 if ':open_interest:' in key else 60
            expected = [datetime(2026, 10, 9, tzinfo=timezone.utc) + timedelta(seconds=i*step)
                        for i in range(86400 // step)]
            observed = table['timestamp'].to_pylist()
            assert observed == expected and len(observed) == record['row_count']
            quality = json.loads(record['quality'])
            source = dict(db.execute('SELECT * FROM artifacts WHERE id=?', (quality['source_id'],)).fetchone())
            raw = read_file(ROOT, db, source['path'])
            assert sha(raw) == source['id'] and len(raw) == source['bytes']
            issues = [dict(r) for r in db.execute('SELECT * FROM issues WHERE key_id=? AND day=?',
                                                 (key, '2026-10-09'))]
            assert not any(r['blocking'] and not r['resolved'] for r in issues)
            target = next(r for r in after if r['key'] == key)
            coverage = target['coverage']
            assert coverage['complete'] and coverage['missing_rows'] == 0 and not coverage['blocked_days']
            assert any(r.get('revision') == record['id'] and r['date'] == '2026-10-09'
                       for r in target['results'])
            result['resolved'].append({'key': key, 'revision': record, 'source': source,
                                       'canonical_sha256': sha(canonical), 'source_sha256': sha(raw),
                                       'rows': len(observed), 'label_step_seconds': step,
                                       'first_timestamp': observed[0], 'last_timestamp': observed[-1],
                                       'exact_label_grid': True, 'issues': issues,
                                       'coverage': coverage})
        all_keys = [r['key'] for r in after]
        slots = ','.join('?' for _ in all_keys)
        for r in db.execute('SELECT * FROM sync_runs WHERE started_at>=? AND key_id IN (' + slots +
                            ') ORDER BY started_at,key_id', (before['observed_at'], *all_keys)):
            row = dict(r)
            row['report'] = json.loads(row['report'])
            result['runs'].append(row)
        assert len(result['runs']) == 8 and all(r['state'] == 'complete' for r in result['runs'])
    finally:
        db.close()
    after_hashes = {s: sha((ROOT / s).read_bytes()) for s in before['files_sha256']}
    assert after_hashes == before['files_sha256']
    assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == before['boot_id']
    result.update(files_sha256_after=after_hashes, configuration_unchanged=True,
                  same_boot=True, rows_verified=sum(r['rows'] for r in result['resolved']), passed=True)
    save('verification.json', result)
    print(json.dumps({'passed': True, 'targets_complete': len(after), 'datasets_verified': len(keys),
                      'rows_verified': result['rows_verified'],
                      'quality': [{'key': r['key'], 'quality': json.loads(r['revision']['quality']),
                                   'issues': r['issues']} for r in result['resolved']],
                      'runs': [{k: r[k] for k in ('id', 'key_id', 'state', 'started_at', 'finished_at')}
                               for r in result['runs']]}, ensure_ascii=False))


def host():
    commands = {
        'daily': ['/usr/bin/systemctl', '--user', 'show', 'marketdata-daily.service',
                  'marketdata-daily.timer', 'marketdata-checkpoint-retirement.service',
                  'marketdata-checkpoint-retirement.timer',
                  '-p', 'Id,ActiveState,SubState,UnitFileState,Result,ExecMainStatus,'
                  'ExecMainStartTimestamp,ExecMainExitTimestamp,ExecMainStartTimestampMonotonic,'
                  'ExecMainExitTimestampMonotonic,NRestarts,NextElapseUSecRealtime,InvocationID'],
        'collectors_and_time': ['/usr/bin/systemctl', 'show', 'chrony.service',
                               'marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service',
                               '-p', 'Id,ActiveState,SubState,MainPID,InvocationID,ExecMainStartTimestamp'],
        'journal': ['/usr/bin/journalctl', '--user', '-u', 'marketdata-daily.service',
                    '-u', 'marketdata-clock-ready.service', '--since', '2026-10-10 18:52:35',
                    '--until', '2026-10-10 18:53:00', '--no-pager', '-o', 'json'],
    }
    result = {'observed_at': datetime.now(timezone.utc).isoformat(), 'read_only': True}
    for name, argv in commands.items():
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        result[name] = {'argv': argv, 'code': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr}
        assert proc.returncode == 0, proc.stderr
    save('host-result.json', result)
    print(json.dumps({'host_saved': True, 'daily': result['daily']['stdout'],
                      'collectors_and_time': result['collectors_and_time']['stdout']}))


if __name__ == '__main__':
    {'verify': verify, 'host': host}[sys.argv[1]]()
