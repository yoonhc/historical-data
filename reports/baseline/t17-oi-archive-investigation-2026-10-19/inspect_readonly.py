"""Inspect one T17 target with an installed Python; write only this report directory.

No Store/Collector/Scheduler constructor, HTTP request, service command, writer
lock, or operating database write is used. The actual installed parser,
inventory, proof and reconcile functions are exercised through a read-only DB
facade. This is an investigation of saved bytes, not an ingestion/retry test.
"""

import argparse
import ast
import csv
import hashlib
import inspect
import io
import json
import sqlite3
import sys
import textwrap
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import marketdata.daily_review as review_module
import marketdata.daily_schedule as schedule_module
import marketdata.normalize as normalize_module
import marketdata.operations as operations_module
import marketdata.store as store_module
from marketdata.domain import DataError, Key, json_default, utc


ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
OUT = Path(__file__).resolve().parent
TASK = 'c24159c0b9f2893037ac67c41e52d5bbdfd0a9ed2c3f62ecdae43d6c7b32b6e9'
SOURCE = '5f1c31bdf645489221d120079529bd311168258ef524a669e88908db22139d90'
CHECKSUM = 'e83db5cde9085b39e8545c7c620f4de5044fae82648ad61967e6113c828524e5'
MEMBER = 'BTCUSDT-metrics-2026-10-15.csv'
ZIP_NAME = 'BTCUSDT-metrics-2026-10-15.zip'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def same_or_create(path, data):
    if path.exists():
        assert path.read_bytes() == data, str(path)
    else:
        with path.open('xb') as stream:
            stream.write(data)


def function_info(function):
    source = inspect.getsource(function)
    return {
        'module_path': inspect.getsourcefile(function),
        'line': inspect.getsourcelines(function)[1],
        'source_sha256': digest(source.encode()),
        'ast_sha256': digest(ast.dump(ast.parse(textwrap.dedent(source)),
                                     include_attributes=False).encode()),
    }


class ReadOnlyStore:
    revision_list = store_module.Store.revision_list

    def __init__(self, db):
        self.db = db

    def coverage(self, *args, **kwargs):
        raise AssertionError('Unexpected canonical read; this target has no active revision')


class ReadOnlyRunner:
    proof = schedule_module.Scheduler.proof

    def __init__(self, db):
        self.store = ReadOnlyStore(db)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', choices=['original', 'current'], required=True)
    args = parser.parse_args()
    result = {'started_at': datetime.now(timezone.utc), 'label': args.label,
              'python': sys.version, 'executable': sys.executable,
              'database': str(ROOT / 'state/catalog.sqlite'),
              'operating_mutations_requested': False, 'network_requests': 0,
              'parser_replay_only': True}
    with closing(sqlite3.connect((ROOT / 'state/catalog.sqlite').as_uri() + '?mode=ro',
                                 uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')

        def rows(sql, parameters=()):
            return [dict(row) for row in db.execute(sql, parameters).fetchall()]

        task_record = rows('SELECT id,record FROM daily_tasks WHERE id=?', (TASK,))[0]
        task = json.loads(task_record['record'])
        key = Key(**task['key'])
        start, end = utc(task['day']), utc(task['day']) + timedelta(days=1)
        attempt_records = [rows('SELECT id,record FROM daily_attempts WHERE id=?', (i,))[0]
                           for i in task['attempts']]
        attempts = [json.loads(r['record']) for r in attempt_records]
        related = [r for r in rows("SELECT id,record FROM daily_tasks "
                                   "WHERE json_extract(record,'$.day')=?", (task['day'],))
                   if json.loads(r['record'])['key'] == task['key']]
        selected = {
            'task_record': task_record, 'attempt_records': attempt_records,
            'related_task_records': related,
            'issues': rows('SELECT * FROM issues WHERE key_id=? AND day=?',
                           (key.id, task['day'])),
            'jobs': rows('SELECT * FROM jobs WHERE key_id=? AND start=? AND end=?',
                         (key.id, start.isoformat(), end.isoformat())),
            'revisions': rows('SELECT * FROM revisions WHERE key_id=? AND day=?',
                              (key.id, task['day'])),
            'active': rows('SELECT * FROM active WHERE key_id=? AND day=?',
                           (key.id, task['day'])),
            'queries': rows('SELECT * FROM queries WHERE key_id=? AND start<? AND end>?',
                            (key.id, end.isoformat(), start.isoformat())),
            'reviews': rows('SELECT id,record FROM daily_reviews WHERE record LIKE ?',
                            ('%' + TASK + '%',)),
            'artifacts': rows('SELECT * FROM artifacts WHERE id IN (?,?) ORDER BY id',
                              (SOURCE, CHECKSUM)),
            'fetches': rows('SELECT * FROM fetches WHERE artifact_id IN (?,?) ORDER BY id',
                            (SOURCE, CHECKSUM)),
        }
        result['selected_records'] = selected
        previous = ROOT / 'reports/baseline/scheduled-clock-operating-review-2026-10-18/preexisting-oi-attempts.json'
        result['previous_attempts'] = {
            'path': str(previous), 'sha256': digest(previous.read_bytes()),
            'same_records': sorted(attempts, key=lambda r: r['id']) ==
                            sorted(json.loads(previous.read_bytes()), key=lambda r: r['id']),
        }
        assert result['previous_attempts']['same_records']
        assert not selected['revisions'] and not selected['active'] and not selected['queries']
        assert len(attempts) == 2 and task['state'] == 'needs_review'

        payloads = {}
        for artifact in selected['artifacts']:
            data = (ROOT / artifact['path']).read_bytes()
            assert digest(data) == artifact['id'] and len(data) == artifact['bytes']
            payloads[artifact['id']] = data
            name = ZIP_NAME if artifact['id'] == SOURCE else ZIP_NAME + '.CHECKSUM'
            same_or_create(OUT / name, data)
        checksum_parts = payloads[CHECKSUM].decode().strip().split()
        assert checksum_parts == [SOURCE, ZIP_NAME]
        with zipfile.ZipFile(io.BytesIO(payloads[SOURCE])) as archive:
            assert archive.namelist() == [MEMBER]
            csv_bytes = archive.read(MEMBER)
        same_or_create(OUT / MEMBER, csv_bytes)
        csv_rows = list(csv.DictReader(io.StringIO(csv_bytes.decode('utf-8-sig'))))
        timestamps = [utc(r['create_time']) for r in csv_rows]
        expected = [start + timedelta(minutes=5 * n) for n in range(288)]
        assert sorted(timestamps) == expected

        replay = []
        for attempt in attempts:
            raw_input = next(i for i in attempt['inputs'] if i['id'] == SOURCE)
            batch = normalize_module.normalize_archive(payloads[SOURCE], key, SOURCE,
                                                       utc(raw_input['received_at']), MEMBER)
            fingerprint = schedule_module.fingerprint(batch.rows, key)
            assert fingerprint == attempt['publish_intent']['economic_sha256']
            zero_rows = [{'timestamp': r['timestamp'], 'source_row': r['source_row'],
                          'quantity': str(r['quantity']),
                          'notional_value': str(r['notional_value']),
                          'raw_csv': csv_rows[r['source_row'] - 2]}
                         for r in batch.rows if r['quantity'] == 0 or r['notional_value'] == 0]
            replay.append({
                'attempt_id': attempt['id'], 'observed_at': raw_input['received_at'],
                'normalized_rows': len(batch.rows), 'quarantine': batch.quarantine,
                'issues': batch.issues, 'zero_rows': zero_rows,
                'quantity_zero_count': sum(r['quantity'] == 0 for r in batch.rows),
                'notional_zero_count': sum(r['notional_value'] == 0 for r in batch.rows),
                'both_zero_count': sum(r['quantity'] == 0 and r['notional_value'] == 0
                                       for r in batch.rows),
                'economic_sha256': fingerprint, 'matches_publish_intent': True,
            })
        assert all(r['quarantine'] and r['quantity_zero_count'] == 8 and
                   r['notional_zero_count'] == 14 and len(r['zero_rows']) == 14 for r in replay)
        result['parser_replay'] = replay
        result['source_checks'] = {
            'zip_sha256': SOURCE, 'checksum_sha256': CHECKSUM,
            'checksum_filename_and_digest_match': True,
            'zip_bytes': len(payloads[SOURCE]), 'checksum_bytes': len(payloads[CHECKSUM]),
            'csv_sha256': digest(csv_bytes), 'csv_rows': len(csv_rows),
            'distinct_timestamps': len(set(timestamps)), 'exact_utc_day_5m_grid': True,
            'adjacent_reverse_order_count': sum(b < a for a, b in zip(timestamps, timestamps[1:])),
        }
        runner = ReadOnlyRunner(db)
        result['inventory'] = operations_module.inventory(runner.store, key, start, end, cache=False)
        result['proofs'] = [runner.proof(key, task['day'], a['publish_intent'], writer_held=True)
                            for a in attempts]
        assert not result['inventory']['complete'] and result['proofs'] == [None, None]
        try:
            review_module.reconcile_proof(runner, task)
        except DataError as exc:
            result['reconcile'] = {'accepted': False, 'error': str(exc)}
        else:
            raise AssertionError('Quarantined day unexpectedly reconciled')
        result['query_only'] = bool(db.execute('PRAGMA query_only').fetchone()[0])
        result['database_changes_by_connection'] = db.total_changes
        assert result['query_only'] and db.total_changes == 0
        db.rollback()

    modules = [normalize_module, store_module, operations_module, schedule_module, review_module]
    result['modules'] = {m.__name__: {'path': m.__file__, 'sha256': digest(Path(m.__file__).read_bytes())}
                         for m in modules}
    functions = {
        'normalize_archive': normalize_module.normalize_archive,
        'Store.publish': store_module.Store.publish,
        'inventory': operations_module.inventory,
        'Scheduler.proof': schedule_module.Scheduler.proof,
        'RecordingCollector._publish': schedule_module.RecordingCollector._publish,
        'reconcile_proof': review_module.reconcile_proof,
    }
    result['functions'] = {name: function_info(function) for name, function in functions.items()}
    result['finished_at'] = datetime.now(timezone.utc)
    result['passed'] = True
    with (OUT / (args.label + '-inspection.json')).open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, default=json_default)
        stream.write('\n')
    print(json.dumps({'label': args.label, 'passed': True, 'rows': len(csv_rows),
                      'quantity_zero': 8, 'notional_zero': 14, 'proof': None,
                      'reconcile': result['reconcile'],
                      'database_changes': result['database_changes_by_connection']}))


if __name__ == '__main__':
    main()
