"""Bounded public S3 metadata and stored HTTP-header review; no ingestion."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import statistics
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
TYPES = {'trade': ('klines/BTCUSDT/1m/', 'BTCUSDT-1m-'),
         'mark': ('markPriceKlines/BTCUSDT/1m/', 'BTCUSDT-1m-'),
         'index': ('indexPriceKlines/BTCUSDT/1m/', 'BTCUSDT-1m-'),
         'oi': ('metrics/BTCUSDT/', 'BTCUSDT-metrics-')}


def save(name, value):
    with (HERE / name).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'marketdata-public-metadata-review/1'})
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read(262145)
        assert len(body) <= 262144
        return body, {'url': url, 'status': response.status, 'headers': dict(response.headers),
                      'observed_at': datetime.now(timezone.utc).isoformat(),
                      'sha256': hashlib.sha256(body).hexdigest()}


def remote():
    html, receipt = fetch('https://data.binance.vision/')
    with (HERE / 'binance-index.html').open('xb') as f:
        f.write(html)
    match = re.search(r"var BUCKET_URL = '([^']+)'", html.decode())
    assert match
    bucket = match[1]
    assert bucket == 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'

    def listing(item):
        name, (directory, stem) = item
        prefix = 'data/futures/um/daily/' + directory
        args = {'list-type': '2', 'prefix': prefix,
                'start-after': prefix + stem + '2026-09-21.zip.CHECKSUM', 'max-keys': '50'}
        url = bucket + '?' + urllib.parse.urlencode(args)
        body, meta = fetch(url)
        with (HERE / (name + '-listing.xml')).open('xb') as f:
            f.write(body)
        tree = ET.fromstring(body)
        ns = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
        records = []
        for node in tree.findall('s:Contents', ns):
            key = node.findtext('s:Key', namespaces=ns)
            filename = key.rsplit('/', 1)[1]
            m = re.fullmatch(re.escape(stem) + r'(\d{4}-\d{2}-\d{2})\.zip(\.CHECKSUM)?', filename)
            if not m or not '2026-09-22' <= m[1] <= '2026-10-09':
                continue
            modified = datetime.fromisoformat(node.findtext('s:LastModified', namespaces=ns).replace('Z', '+00:00'))
            records.append({'dataset': name, 'data_date': m[1], 'kind': 'checksum' if m[2] else 'zip',
                            'key': key, 'url': 'https://data.binance.vision/' + key,
                            'last_modified': modified.isoformat(), 'last_modified_kst': modified.astimezone(KST).isoformat(),
                            'etag': node.findtext('s:ETag', namespaces=ns),
                            'size': int(node.findtext('s:Size', namespaces=ns))})
        assert len(records) == 36, (name, len(records))
        return {'dataset': name, 'receipt': meta, 'is_truncated': tree.findtext('s:IsTruncated', namespaces=ns),
                'records': records}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(listing, TYPES.items()))
    save('remote-listings.json', {'observed_at': datetime.now(timezone.utc).isoformat(),
                                 'read_only': True, 'index_receipt': receipt, 'listings': results})
    print(json.dumps({'requests': 5, 'objects': sum(len(r['records']) for r in results),
                      'datasets': [r['dataset'] for r in results]}))


def analyze():
    remote_data = json.loads((HERE / 'remote-listings.json').read_text())
    records = [r for listing in remote_data['listings'] for r in listing['records']]
    history = json.loads((ROOT / 'reports/baseline/daily-schedule-history-2026-10-10/catalog-history.json').read_text())
    source_ids = {json.loads(r['quality'])['source_id'] for r in history['active_waited_partitions']}
    db = sqlite3.connect('file:' + str(ROOT / 'state/catalog.sqlite') + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    first = {}
    query = 'SELECT * FROM fetches WHERE artifact_id IN (' + ','.join('?' for _ in source_ids) + ') ORDER BY observed_at,id'
    for r in db.execute(query, sorted(source_ids)):
        value = dict(r)
        value['metadata'] = json.loads(value['metadata'])
        if value['metadata'].get('status') == 200:
            first.setdefault(value['artifact_id'], value)
    db.close()
    local_by_url = {r['url']: r for r in first.values()}
    comparisons = []
    for r in records:
        if r['kind'] != 'zip':
            continue
        old = local_by_url.get(r['url'])
        assert old
        headers = {k.lower(): v for k, v in old['metadata']['headers'].items()}
        old_time = parsedate_to_datetime(headers['last-modified'])
        new_time = datetime.fromisoformat(r['last_modified'])
        comparisons.append({'url': r['url'], 'source_id': old['artifact_id'], 'fetch_id': old['id'],
                            'first_recorded_success': old['observed_at'], 'stored_last_modified': old_time.isoformat(),
                            'listed_last_modified': r['last_modified'], 'same_last_modified': old_time == new_time,
                            'same_etag': headers.get('etag') == r['etag'], 'recorded_headers': headers})

    def seconds_of_day(t):
        t = t.astimezone(KST)
        return t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1_000_000

    def time_text(seconds):
        seconds = round(seconds)
        return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'

    daily = []
    for day in sorted({r['data_date'] for r in records}):
        selected = [r for r in records if r['data_date'] == day]
        pair_times = {name: max(datetime.fromisoformat(r['last_modified']) for r in selected if r['dataset'] == name)
                      for name in TYPES}
        end = max(pair_times.values())
        daily.append({'data_date': day, 'all_eight_objects_last_modified_by_kst': end.astimezone(KST).isoformat(),
                      'pair_last_modified_kst': {k: t.astimezone(KST).isoformat() for k, t in pair_times.items()},
                      'last_modified_on_next_kst_date': all(t.astimezone(KST).date() == date.fromisoformat(day) + timedelta(days=1)
                                                          for t in pair_times.values())})
    stats = {}
    for name in (*TYPES, 'all_four_pairs'):
        values = [datetime.fromisoformat(r['all_eight_objects_last_modified_by_kst'] if name == 'all_four_pairs'
                                         else r['pair_last_modified_kst'][name]) for r in daily]
        seconds = sorted(seconds_of_day(t) for t in values)
        stats[name] = {'days': len(values), 'earliest_kst': time_text(seconds[0]),
                       'median_kst': time_text(statistics.median(seconds)), 'mean_kst': time_text(statistics.mean(seconds)),
                       'latest_kst': time_text(seconds[-1]),
                       'not_after_1730': sum(s <= 17.5 * 3600 for s in seconds),
                       'not_after_1800': sum(s <= 18 * 3600 for s in seconds)}
    value = {'observed_at': datetime.now(timezone.utc).isoformat(), 'meaning': 'LastModified metadata, not proven first publication time',
             'statistics': stats, 'daily': daily, 'stored_response_comparisons': comparisons,
             'same_last_modified_count': sum(r['same_last_modified'] for r in comparisons),
             'same_etag_count': sum(r['same_etag'] for r in comparisons),
             'comparison_count': len(comparisons)}
    save('analysis.json', value)
    print(json.dumps({k: v for k, v in value.items() if k != 'stored_response_comparisons'}, ensure_ascii=False))


if __name__ == '__main__':
    {'remote': remote, 'analyze': analyze}[sys.argv[1]]()
