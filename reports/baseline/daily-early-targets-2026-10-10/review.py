"""Read-only completion counts and stored spot ZIP/CHECKSUM response times."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
history_path = ROOT / 'reports/baseline/daily-schedule-history-2026-10-10/catalog-history.json'
history_blob = history_path.read_bytes()
history = json.loads(history_blob)
keys = [key for key in history['keys'] if ':spot:' in key or ':funding_events:' in key
        or ':instrument_metadata:' in key]
assert len(keys) == 4
noon = [batch for batch in history['batches']
        if '2026-09-23' <= batch['started_kst'][:10] <= '2026-10-10'
        and batch['started_kst'][11:13] == '12']
assert len(noon) == 90
counts = []
for key in keys:
    all_rows = [row for batch in history['batches'] for row in batch['runs'] if row['key_id'] == key]
    noon_rows = [row for batch in noon for row in batch['runs'] if row['key_id'] == key]
    counts.append({'key': key, 'noon_states': dict(Counter(row['state'] for row in noon_rows)),
                   'all_eight_target_batch_states': dict(Counter(row['state'] for row in all_rows)),
                   'non_complete': [{'started_kst': batch['started_kst'], 'run': row}
                                    for batch in history['batches'] for row in batch['runs']
                                    if row['key_id'] == key and row['state'] != 'complete']})
assert all(row['noon_states'] == {'complete': 90} for row in counts)

dates = [date(2026, 9, 22) + timedelta(days=i) for i in range(18)]
urls = {f'https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{day}.zip{suffix}':
        (str(day), 'checksum' if suffix else 'zip') for day in dates for suffix in ('', '.CHECKSUM')}
db = sqlite3.connect('file:' + str(ROOT / 'state/catalog.sqlite') + '?mode=ro', uri=True, timeout=3)
db.row_factory = sqlite3.Row
first, last_404 = {}, {}
query = 'SELECT * FROM fetches WHERE url IN (' + ','.join('?' for _ in urls) + ') ORDER BY observed_at,id'
try:
    for source in db.execute(query, list(urls)):
        row = dict(source)
        row['metadata'] = json.loads(row['metadata'])
        url, status = row['url'], row['metadata'].get('status')
        if status == 404 and url not in first:
            last_404[url] = row
        if status == 200 and url not in first:
            first[url] = {'data_date': urls[url][0], 'kind': urls[url][1],
                          'fetch': row, 'last_404_before_first_200': last_404.get(url)}
    later_runs = [dict(row) for row in db.execute(
        'SELECT id,started_at,state FROM sync_runs WHERE key_id=? AND started_at>?',
        ('binance:spot:BTCUSDT:trade_bars:1m:primary',
         max(batch['started_at'] for batch in history['batches'])))]
finally:
    db.close()
assert len(first) == 36
daily, seconds = [], []
for day in dates:
    records = [row for row in first.values() if row['data_date'] == str(day)]
    assert len(records) == 2
    times = [parsedate_to_datetime({key.lower(): value for key, value in row['fetch']['metadata']['headers'].items()}
                                   ['last-modified']).astimezone(KST) for row in records]
    latest = max(times)
    assert latest.date() == day + timedelta(days=1)
    seconds.append(latest.hour * 3600 + latest.minute * 60 + latest.second)
    zip_record = next(row for row in records if row['kind'] == 'zip')
    prior_404 = zip_record['last_404_before_first_200']
    daily.append({'data_date_utc': str(day), 'later_first_response_last_modified_kst': latest.isoformat(),
                  'first_zip_200_kst': datetime.fromisoformat(zip_record['fetch']['observed_at']).astimezone(KST).isoformat(),
                  'last_zip_404_before_kst': datetime.fromisoformat(prior_404['observed_at']).astimezone(KST).isoformat()
                  if prior_404 else None})


def clock_text(value):
    value = round(value)
    return f'{value // 3600:02}:{value // 60 % 60:02}:{value % 60:02}'


result = {'observed_at': datetime.now(timezone.utc).isoformat(), 'sqlite_mode': 'ro',
          'history_path': str(history_path.relative_to(ROOT)), 'history_sha256': hashlib.sha256(history_blob).hexdigest(),
          'batch_count': len(history['batches']), 'noon_days': 18, 'noon_batches': len(noon),
          'counts': counts, 'later_spot_runs_not_in_saved_history': later_runs,
          'spot_scope': 'UTC dates 2026-09-22 through 2026-10-09; first successful stored ZIP and CHECKSUM responses',
          'spot_time_meaning': 'Last-Modified metadata, not exact first availability or a promised publication time',
          'spot_stats_kst': {'days': len(daily), 'min': clock_text(min(seconds)),
                             'median': clock_text(statistics.median(seconds)),
                             'mean': clock_text(statistics.mean(seconds)), 'max': clock_text(max(seconds))},
          'spot_days': daily, 'spot_responses': list(first.values()), 'operational_changes': False}
with (HERE / 'evidence.json').open('x') as handle:
    json.dump(result, handle, ensure_ascii=False, indent=2)
    handle.write('\n')
print(json.dumps({'noon_states': [row['noon_states'] for row in counts],
                  'all_batch_states': [row['all_eight_target_batch_states'] for row in counts],
                  'spot_stats_kst': result['spot_stats_kst'], 'later_runs': later_runs}, ensure_ascii=False))
