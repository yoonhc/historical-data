"""Verify this repair from saved HTTP bodies and hash-checked Parquet files.

No network or market-data changes. Raw-to-canonical comparison does not call
parse_trade/same_trade. Global coverage is metadata plus anomalous-segment checks,
not a new complete historical content audit.
"""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

from marketdata import aggtrades
from marketdata.domain import json_bytes, now
from marketdata.store import Store, atomic_write

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
OUT = Path(__file__).resolve().parent
LOW, HIGH = 3478886750, 3479150818
CONTINUOUS = 3462028665
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def raw_economic(row):
    return (EPOCH + timedelta(milliseconds=row['T']), Decimal(row['p']), Decimal(row['q']),
            row['f'], row['l'], row['m'])


def canonical_economic(row):
    return tuple(row[k] for k in ('timestamp', 'price', 'quantity', 'first_trade_id',
                                 'last_trade_id', 'buyer_is_maker'))


def checked_rows(store, record):
    rows = store._revision_table(record).to_pylist()
    require(len(rows) == record['row_count'], 'Catalog row count mismatch')
    require(rows[0]['agg_trade_id'] == record['first_id'], 'First ID metadata mismatch')
    require(rows[-1]['agg_trade_id'] == record['last_id'], 'Last ID metadata mismatch')
    require(rows[0]['timestamp'].isoformat() == record['first_time'], 'First time metadata mismatch')
    require(rows[-1]['timestamp'].isoformat() == record['last_time'], 'Last time metadata mismatch')
    require(all(a['agg_trade_id'] < b['agg_trade_id'] and a['timestamp'] <= b['timestamp']
                for a, b in zip(rows, rows[1:])), 'Internal ID/time ordering')
    return rows


def global_coverage(store):
    records = [dict(r) for r in store.db.execute(
        'SELECT * FROM event_segments WHERE dataset=? AND instrument_id=? ORDER BY first_id,last_id',
        (aggtrades.DATASET, aggtrades.INSTRUMENT))]
    last, gaps, anomalies = None, [], []
    totals = Counter()
    for r in records:
        totals[r['kind'] + '_segments'] += 1
        totals[r['kind'] + '_rows'] += r['row_count']
        if last is not None and r['first_id'] > last+1:
            gaps.append([last+1, r['first_id']-1])
        last = max(last or r['last_id'], r['last_id'])
        if r['row_count'] != r['last_id'] - r['first_id'] + 1:
            rows = checked_rows(store, r)
            holes = [[a['agg_trade_id']+1,b['agg_trade_id']-1] for a,b in zip(rows,rows[1:])
                     if b['agg_trade_id'] > a['agg_trade_id']+1]
            unresolved = []
            for a,b in holes:
                overlapping = store.db.execute(
                    'SELECT * FROM event_segments WHERE dataset=? AND first_id<=? AND last_id>=?',
                    (aggtrades.DATASET, b, a)).fetchall()
                known = set()
                for other in overlapping:
                    known.update(row['agg_trade_id'] for row in checked_rows(store, dict(other))
                                 if a <= row['agg_trade_id'] <= b)
                unresolved.extend(i for i in range(a,b+1) if i not in known)
            anomalies.append({'segment_id':r['id'], 'internal_gaps':holes,
                              'remaining_missing_ids':unresolved})
    return {'observed_at': now(), 'segments':len(records), 'source_totals':dict(totals),
            'first_id':records[0]['first_id'], 'last_id':last,
            'last_trade_time':max(r['last_time'] for r in records), 'envelope_gaps':gaps,
            'post_continuous_start_envelope_gaps':[g for g in gaps if g[1] >= CONTINUOUS],
            'pre_continuous_missing_count':sum(b-a+1 for a,b in gaps if b < CONTINUOUS),
            'internal_anomalies':anomalies,
            'scope':'All segment ID envelopes; hash/ID reads of internally noncontiguous segments and their repairs. Not full historical row/content audit.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt', required=True)
    args = parser.parse_args()
    folder = OUT / args.attempt
    require(folder.parent == OUT and folder.is_dir(), 'Expected an existing direct attempt folder')
    receipt = json.loads((folder / 'receipt.json').read_bytes())
    plan = json.loads((folder / 'plan.json').read_bytes())
    require(receipt['state'] == 'collected', 'Collection did not finish')
    reports = receipt['collections']
    require(reports and all(r['complete'] for r in reports), 'Incomplete collection')
    collection_ids = {r['id'] for r in reports}
    http = [json.loads(line) for line in (folder/'http.jsonl').read_text().splitlines()]
    require(all(r['status']==200 for r in http), 'Non-200 response')
    by_sha = {r['body_sha256']: r for r in http}
    store = Store(ROOT)
    try:
        marks = ','.join('?' for _ in collection_ids)
        records = [dict(r) for r in store.db.execute(
            f'SELECT * FROM event_segments WHERE collection_id IN ({marks}) ORDER BY first_id',
            tuple(collection_ids))]
        source_ids = [r['source_id'] for r in records]
        fetch_marks = ','.join('?' for _ in source_ids)
        fetches = [dict(r) for r in store.db.execute(
            f'SELECT * FROM fetches WHERE artifact_id IN ({fetch_marks})', tuple(source_ids))]
        saved, raw_overlap, file_evidence = {}, {}, []
        raw_count = raw_bytes = canonical_bytes = 0
        for record in records:
            body, observed = store.raw(record['source_id'])
            require(hashlib.sha256(body).hexdigest()==record['source_id'], 'Raw hash mismatch')
            wire = by_sha[record['source_id']]
            payload = json.loads(body)
            rows = checked_rows(store, record)
            require(any(f['artifact_id']==record['source_id']
                        and json.loads(f['metadata']).get('collection_id')==record['collection_id']
                        and f['observed_at']==wire['received_at'] for f in fetches), 'Fetch provenance absent')
            for row in rows:
                identity = row['agg_trade_id']
                raw = payload[row['source_row']]
                require(identity==raw['a'], 'Raw ordinal/ID mismatch')
                require(canonical_economic(row)==raw_economic(raw), 'Raw economic value mismatch')
                normal = Decimal(raw['nq']) if raw.get('nq') is not None else None
                require(row['normal_quantity']==normal, 'Normal quantity mismatch')
                require(row['event_time'] is None, 'REST must not invent WS event time')
                require(row['received_at'].isoformat()==wire['received_at'], 'Receipt time mismatch')
                require(row['source_id']==record['source_id'], 'Source ID mismatch')
                require(identity not in saved, 'Unexpected duplicated repair ID')
                saved[identity] = row
            for raw in payload:
                if raw['a'] < LOW or raw['a'] > HIGH:
                    raw_overlap[raw['a']] = raw
            raw_count += len(payload)
            raw_bytes += len(body)
            size = store.file_info(record['path'])['bytes']
            canonical_bytes += size
            file_evidence.append({**record, 'raw_bytes':len(body), 'canonical_bytes':size,
                                  'canonical_and_raw_hashes_verified':True,
                                  'raw_economic_and_receipt_fields_equal':True})
        ids = sorted(saved)
        require(ids==list(range(LOW-1,HIGH+1)), 'Exact repaired range/boundary is incomplete')
        require(all(saved[a]['timestamp'] <= saved[b]['timestamp'] for a,b in zip(ids,ids[1:])),
                'Time reversal between pages')
        outside_max = max(raw_overlap)
        others = [dict(r) for r in store.db.execute(
            f'SELECT * FROM event_segments WHERE dataset=? AND first_id<=? AND last_id>=? '
            f'AND collection_id NOT IN ({marks}) ORDER BY first_id',
            (aggtrades.DATASET, outside_max, LOW-1, *collection_ids))]
        compared = set()
        old_boundaries = {}
        for record in others:
            for row in checked_rows(store, record):
                identity = row['agg_trade_id']
                if identity in raw_overlap:
                    raw = raw_overlap[identity]
                    require(canonical_economic(row)==raw_economic(raw), 'Existing source overlap conflict')
                    if row['normal_quantity'] is not None and raw.get('nq') is not None:
                        require(row['normal_quantity']==Decimal(raw['nq']), 'Existing normal quantity conflict')
                    compared.add(identity)
                    if identity in (LOW-1,HIGH+1):
                        old_boundaries[identity] = row
        require(set(raw_overlap)==compared, 'Some returned outside-boundary overlap was not compared')
        require(LOW-1 in compared and HIGH+1 in compared, 'Both boundary values must match')
        require(old_boundaries[LOW-1]['timestamp'] <= saved[LOW]['timestamp'], 'Left boundary time')
        require(saved[HIGH]['timestamp'] <= old_boundaries[HIGH+1]['timestamp'], 'Right boundary time')
        old_unchanged = {}
        for identity, old in plan['historical_collections'].items():
            current = dict(store.db.execute('SELECT * FROM event_collections WHERE id=?',(identity,)).fetchone())
            old_unchanged[identity] = current==old
            require(current==old, 'An original closed collection report changed')
        coverage = global_coverage(store)
        atomic_write(folder/'global-coverage-after.json', json_bytes(coverage))
        atomic_write(folder/'verified-files.json', json_bytes(file_evidence))
        starts = [r['started_monotonic'] for r in http]
        query_audits = []
        # Actual public reader: include the left and right boundary in <=1h windows.
        begin = old_boundaries[LOW-1]['timestamp']
        end = old_boundaries[HIGH+1]['timestamp'] + timedelta(milliseconds=1)
        query_ids = set()
        while begin < end:
            stop = min(begin+timedelta(hours=1),end)
            rows, audit = aggtrades.read_trades(store, begin, stop)
            require(audit['valid'], 'The existing combined reader rejected the repaired window')
            query_ids.update(r['agg_trade_id'] for r in rows if LOW-1<=r['agg_trade_id']<=HIGH+1)
            query_audits.append({k:v for k,v in audit.items() if k!='segments'})
            begin = stop
        require(query_ids==set(range(LOW-1,HIGH+2)), 'Public reader exact ID inclusion')
        before = json.loads((folder/'environment-before.json').read_bytes())
        after = json.loads((folder/'environment-after.json').read_bytes())
        require(before['protected_hashes']==after['protected_hashes'], 'Protected code/config changed')
        result = {'verified_at':now(), 'passed':True, 'collection_ids':sorted(collection_ids),
                  'target_first_id':LOW,'target_last_id':HIGH,'new_target_ids':HIGH-LOW+1,
                  'canonical_rows':len(saved),'boundary_repeat_rows':1,
                  'first_target_trade_time':saved[LOW]['timestamp'],
                  'last_target_trade_time':saved[HIGH]['timestamp'],
                  'first_received_at':min(r['received_at'] for r in http),
                  'last_received_at':max(r['received_at'] for r in http),
                  'raw_rows':raw_count,'raw_bytes':raw_bytes,'canonical_bytes':canonical_bytes,
                  'segments_verified':len(records), 'raw_bodies_verified':len(by_sha),
                  'outside_target_overlap_ids_compared':len(compared), 'conflicts':[],
                  'min_request_start_interval_seconds':min(b-a for a,b in zip(starts,starts[1:])),
                  'max_observed_weight_1m':max(r['used_weight_1m'] for r in http),
                  'sampled_max_abs_wall_monotonic_delta_seconds':max(abs(r['wall_minus_monotonic_delta_seconds']) for r in http),
                  'max_absolute_http_date_receipt_difference_seconds':max(abs(
                      (datetime.fromisoformat(r['received_at'])-parsedate_to_datetime(r['headers']['date'])).total_seconds())
                      for r in http),
                  'http_date_scope':'Coarse seconds-resolution diagnostic, not a UTC accuracy certificate.',
                  'historical_reports_unchanged':old_unchanged, 'protected_code_configs_unchanged':True,
                  'public_reader_queries':query_audits,
                  'post_continuous_start_envelope_gaps':coverage['post_continuous_start_envelope_gaps'],
                  'clock_quality_scope':'Actual local receipt timestamps preserved; runtime clock_usable=false evidence retained separately. No per-event UTC accuracy certification.',
                  'global_audit_scope':coverage['scope'],
                  'backup_status':'Not yet verified in a new checkpoint/restic snapshot'}
        atomic_write(folder/'verification.json',json_bytes(result))
        print(json_bytes(result).decode(),flush=True)
    finally:
        store.close()


if __name__=='__main__':
    main()
