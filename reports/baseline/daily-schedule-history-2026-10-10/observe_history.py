"""Read-only history of the configured eight-target daily update."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))


def save(name, value):
    with (HERE / name).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.write('\n')


def catalog():
    latest = json.loads((ROOT / 'state/daily-latest.json').read_text())
    keys = [r['key'] for r in latest]
    missing_keys = [r['key'] for r in latest if r['key'].startswith('binance:linear_perpetual:')
                    and (':open_interest:5m:archive' in r['key'] or '_bars:' in r['key'])]
    assert len(keys) == 8 and len(missing_keys) == 4
    groups = defaultdict(list)
    db = sqlite3.connect('file:' + str(ROOT / 'state/catalog.sqlite') + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    query = 'SELECT * FROM sync_runs WHERE key_id IN (' + ','.join('?' for _ in keys) + ') ORDER BY started_at,key_id'
    for row in db.execute(query, keys):
        record = dict(row)
        report = json.loads(record.pop('report'))
        record['waiting'] = [r for r in report.get('results', []) if r.get('state') == 'waiting_source']
        record['errors'] = report.get('errors', [])
        record['coverage'] = report.get('coverage')
        groups[record['started_at']].append(record)
    batches = []
    excluded = 0
    for started, rows in groups.items():
        if {r['key_id'] for r in rows} != set(keys) or len(rows) != 8:
            excluded += 1
            continue
        local = datetime.fromisoformat(started).astimezone(KST)
        batches.append({'started_at': started, 'started_kst': local.isoformat(),
                        'finished_at': max(r['finished_at'] or r['updated_at'] for r in rows),
                        'states': dict(Counter(r['state'] for r in rows)),
                        'all_complete': all(r['state'] == 'complete' for r in rows),
                        'waiting_keys': [r['key_id'] for r in rows if r['waiting']],
                        'waiting_count': sum(len(r['waiting']) for r in rows),
                        'waiting_dates': sorted({str(w.get('start', w.get('date', '')))[:10]
                                                 for r in rows for w in r['waiting']}),
                        'errors': sum(len(r['errors']) for r in rows), 'runs': rows})
    noon = [b for b in batches if '2026-09-23' <= b['started_kst'][:10] <= '2026-10-10'
            and b['started_kst'][11:13] == '12']
    by_day = defaultdict(list)
    for b in noon:
        by_day[b['started_kst'][:10]].append(b)
    daily = [{'day_kst': day, 'attempts': len(bs), 'first_start': bs[0]['started_kst'],
              'last_start': bs[-1]['started_kst'], 'all_complete_attempts': sum(b['all_complete'] for b in bs),
              'waiting_counts': sorted({b['waiting_count'] for b in bs}),
              'waiting_dates': sorted({d for b in bs for d in b['waiting_dates']}),
              'errors': sum(b['errors'] for b in bs)} for day, bs in sorted(by_day.items())]
    active = []
    for key in missing_keys:
        for r in db.execute('SELECT r.* FROM active a JOIN revisions r ON r.id=a.revision_id '
                            'WHERE a.key_id=? AND a.day>=? AND a.day<=? ORDER BY a.day',
                            (key, '2026-09-22', '2026-10-09')):
            r = dict(r)
            active.append({k: r[k] for k in ('key_id', 'day', 'id', 'created_at', 'row_count', 'quality')})
    db.close()
    result = {'observed_at': datetime.now(timezone.utc).isoformat(), 'sqlite_mode': 'ro',
              'selection': 'exact same eight configured keys grouped by identical started_at',
              'query': query, 'keys': keys, 'incomplete_key_groups_excluded': excluded,
              'batches': batches, 'noon_by_day': daily, 'active_waited_partitions': active,
              'totals': {'eight_target_batches': len(batches), 'complete_batches': sum(b['all_complete'] for b in batches),
                         'noon_days': len(daily), 'noon_attempts': len(noon),
                         'noon_complete_attempts': sum(b['all_complete'] for b in noon),
                         'noon_all_four_expected_keys': all(set(b['waiting_keys']) == set(missing_keys) for b in noon),
                         'noon_errors': sum(b['errors'] for b in noon), 'active_partition_count': len(active)}}
    assert len(active) == 72
    save('catalog-history.json', result)
    print(json.dumps({'totals': result['totals'], 'days': daily,
                      'complete': [{k: b[k] for k in ('started_kst', 'finished_at', 'states')}
                                   for b in batches if b['all_complete']],
                      'first_active_by_date': {d: sorted({r['created_at'][:16] for r in active if r['day'] == d})
                                               for d in sorted({r['day'] for r in active})}}, ensure_ascii=False))


def host():
    argv = ['/usr/bin/journalctl', '--user', '-u', 'marketdata-daily.service',
            '--since', '2026-09-22 00:00:00', '--no-pager', '-o', 'json']
    p = subprocess.run(argv, capture_output=True, timeout=45)
    assert p.returncode == 0, p.stderr.decode()
    with (HERE / 'daily-journal.jsonl').open('xb') as f:
        f.write(p.stdout)
    messages = []
    for line in p.stdout.splitlines():
        r = json.loads(line)
        msg = r.get('MESSAGE') or ''
        if not isinstance(msg, str):
            continue
        if any(term in msg for term in ('Starting marketdata-daily', 'Finished marketdata-daily',
                                       'Main process exited', 'Start request repeated', 'Scheduled restart job')):
            stamp = int(r['__REALTIME_TIMESTAMP']) / 1_000_000
            messages.append({'time_kst': datetime.fromtimestamp(stamp, KST).isoformat(),
                             'boot_id': r.get('_BOOT_ID'), 'message': msg})
    show = ['/usr/bin/systemctl', '--user', 'show', 'marketdata-daily.service', 'marketdata-daily.timer',
            '-p', 'Id,ActiveState,SubState,Result,ExecMainStatus,Restart,RestartUSec,StartLimitBurst,'
            'StartLimitIntervalUSec,TimersCalendar,TimersMonotonic,RandomizedDelayUSec,Persistent,'
            'LastTriggerUSec,NextElapseUSecRealtime,FragmentPath,DropInPaths']
    s = subprocess.run(show, capture_output=True, text=True, timeout=15)
    assert s.returncode == 0, s.stderr
    save('host-history.json', {'observed_at': datetime.now(timezone.utc).isoformat(), 'read_only': True,
                               'journal_argv': argv, 'journal_sha256': hashlib.sha256(p.stdout).hexdigest(),
                               'journal_rows': len(p.stdout.splitlines()), 'messages': messages,
                               'systemd_argv': show, 'systemd': s.stdout})
    counts = defaultdict(Counter)
    limits = []
    for r in messages:
        msg = r['message']; day = r['time_kst'][:10]
        if 'Starting marketdata-daily' in msg: counts[day]['service_starts'] += 1
        if 'Main process exited' in msg: counts[day]['nonzero_main_exit'] += 1
        if 'Finished marketdata-daily' in msg: counts[day]['finished'] += 1
        if 'Start request repeated' in msg:
            counts[day]['start_limit'] += 1
            limits.append(r)
    print(json.dumps({'journal_rows': len(p.stdout.splitlines()), 'by_day': counts,
                      'start_limits': limits, 'systemd': s.stdout}, ensure_ascii=False))


if __name__ == '__main__':
    {'catalog': catalog, 'host': host}[sys.argv[1]]()
