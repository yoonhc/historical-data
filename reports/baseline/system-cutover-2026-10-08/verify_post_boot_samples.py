"""Bounded read-only examination of already-recorded post-boot segments."""

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq
from marketdata.fileio import read_file

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
EVIDENCE = BASE / 'post-reboot-initial'
COLLECTIONS = json.loads((EVIDENCE / 'collections.json').read_text())
db = sqlite3.connect(f'file:{ROOT}/state/catalog.sqlite?mode=ro', uri=True, isolation_level=None)
db.row_factory = sqlite3.Row
result = {'at': datetime.now().astimezone().isoformat(), 'streams': {},
          'scope': 'Canonical hash/row count and ID continuity for initial persisted subset; '
                   'raw hash and selected row metadata for first/last 3 segments. '
                   'No order-book recalculation, API request, report rewrite or whole-store audit.'}
try:
    for stream, collection in COLLECTIONS.items():
        segments = collection['segments']
        artifacts = {x['id']: x for x in collection['artifacts']}
        total = 0
        kinds = Counter()
        failures = []
        reverse_received = 0
        prev_recv = None
        first_recv = None
        last_recv = None
        previous_id = None
        id_gaps = []
        samples = []
        selected = {x['id'] for x in segments[:3] + segments[-3:]}
        for segment in segments:
            blob = read_file(ROOT, db, segment['path'])
            if hashlib.sha256(blob).hexdigest() != segment['file_sha']:
                failures.append({'path': segment['path'], 'kind': 'canonical_hash'})
            cols = (['sequence', 'kind', 'received_at', 'final_update_id', 'previous_update_id']
                    if stream == 'depth' else ['agg_trade_id', 'received_at'])
            rows = pq.ParquetFile(pa.BufferReader(blob)).read(columns=cols).to_pylist()
            total += len(rows)
            if len(rows) != segment['row_count']:
                failures.append({'path': segment['path'], 'kind': 'row_count'})
            for r in rows:
                kind = r['kind'] if stream == 'depth' else 'websocket'
                kinds[kind] += 1
                if stream == 'depth' and kind != 'update':
                    continue
                rec = r['received_at']
                first_recv = rec if first_recv is None else min(first_recv, rec)
                last_recv = rec if last_recv is None else max(last_recv, rec)
                if prev_recv is not None and rec < prev_recv:
                    reverse_received += 1
                prev_recv = rec
                identity = r['final_update_id'] if stream == 'depth' else r['agg_trade_id']
                if previous_id is not None:
                    if stream == 'depth' and r['previous_update_id'] != previous_id:
                        id_gaps.append([previous_id, r['previous_update_id'], identity])
                    if stream == 'aggtrade' and identity != previous_id + 1:
                        id_gaps.append([previous_id + 1, identity - 1])
                previous_id = identity
            if segment['id'] in selected:
                raw = artifacts[segment['source_id']]
                raw_blob = read_file(ROOT, db, raw['path'])
                raw_ok = hashlib.sha256(raw_blob).hexdigest() == raw['id']
                if not raw_ok:
                    failures.append({'path': raw['path'], 'kind': 'raw_hash'})
                samples.append({'canonical_path': segment['path'], 'raw_path': raw['path'],
                                'raw_kind': raw['kind'], 'raw_hash_matches': raw_ok,
                                'raw_observed_at': raw['observed_at'], 'rows': len(rows),
                                'first_row': rows[0], 'last_row': rows[-1]})
        result['streams'][stream] = {'collection_id': collection['id'], 'segments_checked': len(segments),
                                    'rows_read': total, 'kind_counts': dict(kinds),
                                    'first_ws_received_at': first_recv, 'last_ws_received_at': last_recv,
                                    'receive_reversals': reverse_received, 'id_chain_breaks': id_gaps,
                                    'failures': failures, 'samples': samples,
                                    'report_messages': collection['report']['messages'],
                                    'report_persisted_messages': collection['report']['persisted_messages']}
    prior = json.loads((BASE / 'before.json').read_text())['config_hashes']
    result['config_hash_correction'] = {
        'reason': 'Initial observation helper compared a digest to the whole {sha256, mode} object; '
                  'initial artifact is preserved. Compare sha256 member here.',
        'business_config_checks': {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() == rec['sha256']
                                  for p, rec in prior.items() if p != '/etc/adjtime'},
        'rtc': {'expected_changed_from_pre_transition_local': True,
                'adjtime_utc': Path('/etc/adjtime').read_text().splitlines()[-1] == 'UTC'}}
    result['passed'] = all(not x['failures'] and not x['id_chain_breaks'] and not x['receive_reversals']
                           for x in result['streams'].values())
    path = EVIDENCE / 'stored-data-verification.json'
    with path.open('x') as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2, default=str) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'streams'}, ensure_ascii=False))
    print(json.dumps({k: {a: b for a, b in v.items() if a != 'samples'}
                      for k, v in result['streams'].items()}, default=str))
finally:
    db.close()
