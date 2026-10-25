"""Small read-only recovery observations; no service control or database writes."""
import datetime as dt
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from contextlib import closing

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
HERE = Path(__file__).resolve().parent


def file_check(path, expected):
    p = ROOT / path
    info = p.stat()
    if info.st_size > 16 * 1024 * 1024:
        raise RuntimeError('Observation sample exceeds 16 MiB')
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    return {'path': str(p), 'bytes': info.st_size, 'expected_sha256': expected,
            'sha256': actual, 'matches': actual == expected}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--units-file', type=Path)
    options = parser.parse_args()
    result = {'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'service_control': False, 'database_writes': False, 'streams': {}}
    units = ['marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service',
             'marketdata-pack.service', 'marketdata-pack.timer', 'marketdata-restic-archive.service',
             'marketdata-restic-archive.timer', 'marketdata-checkpoint-retirement.timer', 'chrony.service']
    props = ['Id', 'MainPID', 'ControlPID', 'ActiveState', 'SubState', 'Job', 'InvocationID',
             'NRestarts', 'Result', 'ExecMainStatus', 'ExecMainStartTimestamp', 'UnitFileState',
             'NextElapseUSecRealtime', 'StartLimitIntervalUSec', 'StartLimitBurst', 'Restart']
    args = ['/usr/bin/systemctl', 'show', *units, '--no-pager']
    for prop in props:
        args += ['-p', prop]
    raw = options.units_file.read_text() if options.units_file else subprocess.check_output(args, text=True, timeout=10)
    result['unit_snapshot_file'] = str(options.units_file) if options.units_file else None
    result['units'] = {d['Id']: d for block in raw.strip().split('\n\n')
                       if 'Id' in (d := dict(line.split('=', 1) for line in block.splitlines() if '=' in line))}
    with closing(sqlite3.connect((ROOT / 'state/catalog.sqlite').as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        deadline = time.monotonic() + 10
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        for stream, dataset in [('depth', 'book_depth'), ('aggtrade', 'agg_trades')]:
            latest = json.loads((ROOT / 'state/runtime-clock/v2' / stream / 'latest.json').read_bytes())
            row = db.execute("SELECT * FROM event_collections WHERE dataset=? AND kind='websocket' ORDER BY started_at DESC LIMIT 1", (dataset,)).fetchone()
            collection = dict(row)
            report = json.loads(collection.pop('report'))
            keys = ['persisted_messages', 'messages', 'last_received_at', 'last_trade_time', 'last_id',
                    'previous_persisted_id', 'resume_after_id', 'runtime_clock', 'synchronized',
                    'sync_losses', 'connection_errors', 'detected_gaps', 'unrepaired_gaps', 'recent_recoveries']
            collection['report_subset'] = {k: report[k] for k in keys if k in report}
            segments = []
            instrument = db.execute('SELECT instrument_id FROM event_segments WHERE dataset=? LIMIT 1', (dataset,)).fetchone()[0]
            for segment in db.execute('SELECT * FROM event_segments WHERE dataset=? AND instrument_id=? AND first_time>=? AND collection_id=? ORDER BY first_time DESC LIMIT 2', (dataset, instrument, row['started_at'], row['id'])):
                item = dict(segment)
                item['file_check'] = file_check(item['path'], item['file_sha'])
                source = db.execute('SELECT * FROM artifacts WHERE id=?', (item['source_id'],)).fetchone()
                item['raw_check'] = file_check(source['path'], source['id'])
                segments.append(item)
            clock_chunks = []
            for fetch in db.execute('SELECT a.*,f.url,f.metadata FROM fetches f JOIN artifacts a ON a.id=f.artifact_id WHERE f.url LIKE ? ORDER BY f.id DESC LIMIT 2', (f"runtime-clock://{latest['run_id']}/%",)):
                chunk = dict(fetch)
                chunk['metadata'] = json.loads(chunk['metadata'])
                chunk['file_check'] = file_check(chunk['path'], chunk['id'])
                clock_chunks.append(chunk)
            system = result['units']['marketdata-collector@' + stream + '.service']
            result['streams'][stream] = {
                'latest_clock': latest, 'collection': collection, 'sample_segments': segments,
                'published_clock_chunks': clock_chunks,
                'clock_monotonic_age_seconds': (time.monotonic_ns() - latest['sample']['monotonic_ns']) / 1e9,
                'systemd_clock_identity_matches': system['MainPID'] == str(latest['supervisor_pid'])
                    and system['InvocationID'] == latest['systemd_invocation_id'],
                'collection_clock_identity_matches': report['runtime_clock']['run_id'] == latest['run_id'],
            }
        result['automatic_gap_recovery'] = [dict(r) for r in db.execute("SELECT id,started_at,state,report FROM event_collections WHERE dataset='agg_trades' AND kind='rest' AND started_at >= '2026-10-19T13:59:00' ORDER BY started_at")]
        for row in result['automatic_gap_recovery']:
            row['report'] = json.loads(row['report'])
    stat = os.statvfs(ROOT)
    result['available_bytes'] = stat.f_bavail * stat.f_frsize
    out = HERE / ('recovery-observation-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S') + '.json')
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report': str(out), 'available_bytes': result['available_bytes'],
          'streams': {k: {'identity_matches': v['systemd_clock_identity_matches'] and v['collection_clock_identity_matches'],
                         'persisted_messages': v['collection']['report_subset'].get('persisted_messages'),
                         'raw_samples_verified': all(x['file_check']['matches'] and x['raw_check']['matches'] for x in v['sample_segments']),
                         'clock_chunks': len(v['published_clock_chunks']), 'clock_age_seconds': v['clock_monotonic_age_seconds']}
                      for k, v in result['streams'].items()}}, indent=2))


if __name__ == '__main__':
    main()
