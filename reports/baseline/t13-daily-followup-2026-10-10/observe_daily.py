"""Read-only daily follow-up; avoid shadowing Python's inspect module."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
HERE = Path(__file__).resolve().parent


def stamp():
    return datetime.now(timezone.utc).isoformat()


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def save(name, value):
    with (HERE / name).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.write('\n')


def local():
    import pyarrow as pa
    import pyarrow.parquet as pq
    from marketdata.fileio import read_file

    blob = (ROOT / 'state/daily-latest.json').read_bytes()
    latest = json.loads(blob)
    with (HERE / 'daily-latest.json').open('xb') as f:
        f.write(blob)
    keys = [r['key'] for r in latest]
    pending = [r for r in latest if any(x.get('state') == 'waiting_source' for x in r.get('results', []))]
    assert len(pending) == 4
    result = {'observed_at': stamp(), 'sqlite_mode': 'ro', 'daily_report_sha256': sha(blob),
              'configuration': {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in (
                  ROOT / 'configs/daily.toml', ROOT / 'integrations/systemd/marketdata-daily.service',
                  ROOT / 'integrations/systemd/marketdata-daily.timer')},
              'runs': [], 'resolved_20261008': [], 'current_missing': []}
    db = sqlite3.connect('file:' + str(ROOT / 'state/catalog.sqlite') + '?mode=ro', uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    try:
        placeholders = ','.join('?' for _ in keys)
        for row in db.execute('SELECT * FROM sync_runs WHERE key_id IN (' + placeholders +
                              ') AND started_at>=? ORDER BY started_at,key_id', (*keys, '2026-10-10T00:00:00')):
            item = dict(row)
            item['report'] = json.loads(item['report'])
            result['runs'].append(item)
        for target in pending:
            row = db.execute('SELECT r.* FROM active a JOIN revisions r ON r.id=a.revision_id '
                             'WHERE a.key_id=? AND a.day=?', (target['key'], '2026-10-08')).fetchone()
            assert row is not None
            record = dict(row)
            blob = read_file(ROOT, db, row['path'])
            assert sha(blob) == row['file_sha']
            table = pq.ParquetFile(pa.BufferReader(blob)).read()
            step = 300 if ':open_interest:' in target['key'] else 60
            expected = [datetime(2026, 10, 8, tzinfo=timezone.utc) + timedelta(seconds=i * step)
                        for i in range(86400 // step)]
            observed = table['timestamp'].to_pylist()
            assert observed == expected and len(observed) == row['row_count']
            quality = json.loads(row['quality'])
            source = dict(db.execute('SELECT * FROM artifacts WHERE id=?', (quality['source_id'],)).fetchone())
            raw = read_file(ROOT, db, source['path'])
            assert sha(raw) == source['id'] and len(raw) == source['bytes']
            blocking = [dict(r) for r in db.execute('SELECT * FROM issues WHERE key_id=? AND day=? '
                                                   'AND blocking=1 AND resolved=0', (target['key'], '2026-10-08'))]
            assert not blocking
            proof = {'revision': record, 'source': source, 'canonical_sha256': sha(blob),
                     'source_sha256': sha(raw), 'rows': len(observed), 'label_step_seconds': step,
                     'first_timestamp': observed[0], 'last_timestamp': observed[-1],
                     'exact_label_grid': True, 'blocking_issues': blocking}
            result['resolved_20261008'].append(proof)
            missing = [dict(r) for r in db.execute('SELECT * FROM active WHERE key_id=? AND day=?',
                                                 (target['key'], '2026-10-09'))]
            result['current_missing'].append({'key': target['key'], 'coverage': target['coverage'],
                                              'active_20261009': missing})
        assert (ROOT / 'state/daily-latest.json').read_bytes() == (HERE / 'daily-latest.json').read_bytes()
    finally:
        db.close()
    result['finished_at'] = stamp()
    save('catalog-evidence.json', result)
    print(json.dumps({'resolved_date': '2026-10-08', 'datasets': len(result['resolved_20261008']),
                      'rows_verified': sum(r['rows'] for r in result['resolved_20261008']),
                      'current_pending_date': '2026-10-09', 'runs': len(result['runs'])}))


def remote():
    original = ROOT / 'reports/baseline/system-cutover-readiness-2026-10-07/check_remote.py'
    spec = importlib.util.spec_from_file_location('existing_remote_check', original)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    latest = json.loads((HERE / 'daily-latest.json').read_text())
    targets = [(r['key'], x['url']) for r in latest for x in r.get('results', [])
               if x.get('state') == 'waiting_source']
    assert len(targets) == 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        observations = list(pool.map(module.inspect, targets))
    value = {'observed_at': stamp(), 'read_only': True, 'helper': str(original),
             'helper_sha256': sha(original.read_bytes()), 'observations': observations}
    name = 'remote-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '.json'
    save(name, value)
    print(json.dumps({'report': name, 'results': [{'key': r['key'], 'classification': r['classification'],
                       'statuses': [x['status'] for x in r['requests']]} for r in observations]}))
    return 3 if any(x['status'] is None for r in observations for x in r['requests']) else 0


def host():
    commands = {
        'systemd': ['/usr/bin/systemctl', '--user', 'show', 'marketdata-daily.service', 'marketdata-daily.timer',
                    'marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer',
                    '--all', '--property=Id,LoadState,UnitFileState,ActiveState,SubState,Result,NRestarts,'
                    'ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp,LastTriggerUSec,'
                    'NextElapseUSecRealtime,StartLimitBurst,StartLimitIntervalUSec'],
        'journal': ['/usr/bin/journalctl', '--user', '-u', 'marketdata-daily.service', '--since',
                    '2026-10-10 00:00:00', '--until', '2026-10-10 18:40:00', '--no-pager', '-o', 'json'],
    }
    value = {'observed_at': stamp(), 'read_only': True}
    for name, argv in commands.items():
        p = subprocess.run(argv, capture_output=True, timeout=45)
        assert p.returncode == 0, p.stderr.decode()
        value[name] = {'argv': argv, 'exit_code': p.returncode, 'stdout_sha256': sha(p.stdout),
                       'stderr': p.stderr.decode()}
        if name == 'journal':
            with (HERE / 'daily-journal.jsonl').open('xb') as f:
                f.write(p.stdout)
            rows = [json.loads(line) for line in p.stdout.splitlines()]
            value[name]['rows'] = len(rows)
            value[name]['summary'] = [{k: r.get(k) for k in ('__REALTIME_TIMESTAMP', '__MONOTONIC_TIMESTAMP',
                                      '_BOOT_ID', '_SYSTEMD_INVOCATION_ID', 'MESSAGE')}
                                     for r in rows if not str(r.get('MESSAGE', '')).lstrip().startswith(('{', '['))]
        else:
            value[name]['stdout'] = p.stdout.decode()
    save('host-and-journal.json', value)
    print(json.dumps({'host_evidence_saved': True, 'journal_rows': value['journal']['rows'],
                      'systemd': value['systemd']['stdout']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('local', 'remote', 'host'))
    phase = parser.parse_args().phase
    raise SystemExit({'local': local, 'remote': remote, 'host': host}[phase]())
