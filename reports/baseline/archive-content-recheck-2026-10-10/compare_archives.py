"""Bounded original/current ZIP comparison; no ingestion or operational changes."""
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import io
from itertools import zip_longest
import json
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request
import zipfile

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
HERE = Path(__file__).resolve().parent
BASE = 'https://data.binance.vision/data/futures/um/daily/'
FILE = 'BTCUSDT-1m-2026-10-04.zip'
TARGETS = {'mark': ('markPriceKlines', 602487, 602488),
           'trade': ('klines', 602481, 602482)}


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(name, value):
    with (HERE / name).open('x') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def save_body(name, blob):
    with (HERE / name).open('xb') as handle:
        handle.write(blob)


def local():
    from marketdata.fileio import read_file
    db = sqlite3.connect('file:' + str(ROOT / 'state/catalog.sqlite') + '?mode=ro',
                         uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    result = {'observed_at': now(), 'sqlite_mode': 'ro', 'files': []}
    try:
        for label, (directory, zip_fetch, checksum_fetch) in TARGETS.items():
            for kind, fetch_id in (('zip', zip_fetch), ('CHECKSUM', checksum_fetch)):
                url = BASE + directory + '/BTCUSDT/1m/' + FILE
                if kind == 'CHECKSUM':
                    url += '.CHECKSUM'
                fetch = dict(db.execute('SELECT * FROM fetches WHERE id=?', (fetch_id,)).fetchone())
                assert fetch['url'] == url
                fetch['metadata'] = json.loads(fetch['metadata'])
                assert fetch['metadata']['status'] == 200
                artifact = dict(db.execute('SELECT * FROM artifacts WHERE id=?',
                                           (fetch['artifact_id'],)).fetchone())
                blob = read_file(ROOT, db, artifact['path'])
                assert sha(blob) == artifact['id'] and len(blob) == artifact['bytes']
                filename = label + '-stored.' + kind
                save_body(filename, blob)
                result['files'].append({'dataset': label, 'kind': kind, 'file': filename,
                                        'sha256': sha(blob), 'bytes': len(blob),
                                        'artifact': artifact, 'original_fetch': fetch})
    finally:
        db.close()
    save_json('stored-receipt.json', result)
    print(json.dumps({'stored_files_verified': len(result['files'])}))


def remote():
    jobs = [(label, kind, BASE + directory + '/BTCUSDT/1m/' + FILE + suffix)
            for label, (directory, _, _) in TARGETS.items()
            for kind, suffix in (('zip', ''), ('CHECKSUM', '.CHECKSUM'))]

    def fetch(job):
        label, kind, url = job
        limit = 1_048_576 if kind == 'zip' else 4096
        headers = {'User-Agent': 'marketdata-archive-content-review/1',
                   'Cache-Control': 'no-cache', 'Accept-Encoding': 'identity'}
        started = now()
        tick = time.monotonic()
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            blob = response.read(limit + 1)
            assert response.status == 200 and len(blob) <= limit
            response_headers = {k.lower(): v for k, v in response.headers.items()}
            if response_headers.get('content-length'):
                assert len(blob) == int(response_headers['content-length'])
            receipt = {'dataset': label, 'kind': kind, 'url': url, 'final_url': response.url,
                       'status': response.status, 'request_headers': headers,
                       'headers': response_headers, 'started_at': started, 'finished_at': now(),
                       'elapsed_seconds': time.monotonic() - tick, 'bytes': len(blob),
                       'sha256': sha(blob), 'file': label + '-current.' + kind}
        save_body(receipt['file'], blob)
        return receipt

    with ThreadPoolExecutor(max_workers=2) as pool:
        files = list(pool.map(fetch, jobs))
    save_json('current-receipt.json', {'observed_at': now(), 'files': files,
                                      'requests': len(jobs), 'ingestion': False})
    print(json.dumps({'downloaded_files': len(files), 'bytes': sum(r['bytes'] for r in files)}))


def unpack(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)) and len(names) <= 5
        assert all(member.file_size <= 16_777_216 for member in archive.infolist())
        assert archive.testzip() is None
        return {name: archive.read(name) for name in names}


def checksum(blob):
    parts = blob.decode('ascii').split()
    assert len(parts) == 2 and parts[1].lstrip('*') == FILE
    assert len(parts[0]) == 64 and all(c in '0123456789abcdef' for c in parts[0])
    return parts[0]


def compare():
    stored = json.loads((HERE / 'stored-receipt.json').read_text())
    current = json.loads((HERE / 'current-receipt.json').read_text())
    results = []
    for label in TARGETS:
        old = (HERE / (label + '-stored.zip')).read_bytes()
        new = (HERE / (label + '-current.zip')).read_bytes()
        old_check = (HERE / (label + '-stored.CHECKSUM')).read_bytes()
        new_check = (HERE / (label + '-current.CHECKSUM')).read_bytes()
        old_meta = next(r for r in stored['files'] if r['dataset'] == label and r['kind'] == 'zip')
        new_meta = next(r for r in current['files'] if r['dataset'] == label and r['kind'] == 'zip')
        old_members, new_members = unpack(old), unpack(new)
        members = []
        for name in sorted(set(old_members) | set(new_members)):
            left, right = old_members.get(name), new_members.get(name)
            item = {'name': name, 'in_stored': left is not None, 'in_current': right is not None,
                    'byte_identical': left == right,
                    'stored_sha256': sha(left) if left is not None else None,
                    'current_sha256': sha(right) if right is not None else None}
            if name.endswith('.csv') and left is not None and right is not None:
                old_rows = list(csv.reader(io.StringIO(left.decode('utf-8-sig'))))
                new_rows = list(csv.reader(io.StringIO(right.decode('utf-8-sig'))))
                differences = [{'csv_line': i, 'stored': a, 'current': b}
                               for i, (a, b) in enumerate(zip_longest(old_rows, new_rows), 1) if a != b]
                has_header = bool(old_rows) and old_rows[0][0] == 'open_time'
                item.update(stored_csv_lines=len(old_rows), current_csv_lines=len(new_rows),
                            header=old_rows[0] if has_header else None,
                            stored_data_rows=len(old_rows) - int(has_header),
                            current_data_rows=len(new_rows) - int(has_header),
                            changed_lines=len(differences), first_differences=differences[:20])
            members.append(item)
        results.append({'dataset': label, 'data_date_utc': '2026-10-04',
                        'url': new_meta['url'], 'first_stored_at': old_meta['original_fetch']['observed_at'],
                        'stored_last_modified': old_meta['original_fetch']['metadata']['headers']['last-modified'],
                        'current_last_modified': new_meta['headers'].get('last-modified'),
                        'stored_bytes': len(old), 'current_bytes': len(new),
                        'stored_sha256': sha(old), 'current_sha256': sha(new),
                        'zip_byte_identical': old == new, 'checksum_byte_identical': old_check == new_check,
                        'stored_checksum_matches': checksum(old_check) == sha(old),
                        'current_checksum_matches': checksum(new_check) == sha(new),
                        'current_checksum_matches_original': checksum(new_check) == sha(old),
                        'zip_crc_valid': True, 'members': members})
    value = {'observed_at': now(), 'results': results,
             'all_zip_byte_identical': all(r['zip_byte_identical'] for r in results),
             'all_checksum_valid': all(r['stored_checksum_matches'] and r['current_checksum_matches'] for r in results),
             'all_csv_byte_identical': all(m['byte_identical'] for r in results for m in r['members']),
             'scope': 'Original recorded response artifacts versus current public endpoint; no ingestion or data rewrite'}
    save_json('comparison.json', value)
    print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    {'local': local, 'remote': remote, 'compare': compare}[sys.argv[1]]()
