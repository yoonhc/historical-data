"""One approved, bounded operator job; uses the installed collector unchanged.

--plan is read-only apart from evidence. --run publishes REST raw/canonical files.
Reruns derive remaining IDs from hash-checked files, never from a progress cursor.
No service, clock, policy, old report, or backup changes.
"""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from marketdata import aggtrades
from marketdata.binance import Fetcher, SourceError
from marketdata.domain import DataError, json_bytes, now
from marketdata.store import Store, atomic_write

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
OUT = Path(__file__).resolve().parent
RELEASE = Path('/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee')
LOW, HIGH = 3478886750, 3479150818
HISTORICAL = ('563e1622c2a947c2aa1f9fef319ed89f',
              'c36431bac84d4092b5f1ca33b4740af4',
              '24d467b9c2264c5da13f12ccd19c36a1')
POLICY = dict(first_id=LOW, last_id=HIGH, boundary_check_id=LOW - 1,
              min_request_interval_seconds=3.0, max_requests=270,
              max_elapsed_seconds=2400, aggregate_weight_pause_at=800,
              high_weight_pause_seconds=65, minimum_free_bytes=10 * 1024**3,
              hidden_retries=0, max_pages_per_collection=265)


def save(path, value):
    atomic_write(path, json_bytes(value))


def command(argv):
    proc = subprocess.run(argv, text=True, capture_output=True, timeout=30)
    return {'argv': argv, 'returncode': proc.returncode,
            'stdout': proc.stdout, 'stderr': proc.stderr}


def environment():
    result = {'observed_at': now(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'installed_aggtrades': aggtrades.__file__,
              'helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'disk_free_bytes': shutil.disk_usage(ROOT).free, 'protected_hashes': {}}
    paths = [ROOT / 'packages/marketdata/src/marketdata/aggtrades.py',
             Path(aggtrades.__file__), Path('/etc/marketdata/runtime.json'),
             Path('/etc/marketdata/clock-bootstrap.json'), Path('/etc/chrony/marketdata.conf')]
    for path in paths:
        result['protected_hashes'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    result['collectors'] = command(['systemctl', 'show', 'marketdata-collector@depth.service',
                                    'marketdata-collector@aggtrade.service', '-p', 'Id', '-p', 'ActiveState',
                                    '-p', 'SubState', '-p', 'MainPID', '-p', 'User', '-p', 'ExecMainStartTimestamp'])
    result['retirement'] = command(['systemctl', '--user', 'list-unit-files',
                                   'marketdata-checkpoint-retirement.*', '--no-pager'])
    result['chrony_tracking'] = command(['/usr/bin/chronyc', '-n', 'tracking'])
    result['chrony_sources'] = command(['/usr/bin/chronyc', '-n', 'sources'])
    for stream in ('aggtrade', 'depth'):
        result['clock_' + stream] = command([str(RELEASE / '.venv/bin/python'), '-I',
            str(RELEASE / 'integrations/runtime/marketdata_runtime.py'), 'status',
            '--profile', '/etc/marketdata/runtime.json', '--stream', stream])
    # These observations do not certify each receipt timestamp. In particular,
    # clock_usable=false is retained, never turned into success by this job.
    return result


def missing_intervals(ids, low=LOW, high=HIGH):
    missing, cursor = [], low
    for identity in sorted(set(ids)):
        if identity < low or identity > high:
            continue
        if identity > cursor:
            missing.append([cursor, identity - 1])
        cursor = identity + 1
    if cursor <= high:
        missing.append([cursor, high])
    return missing


def current_plan(store):
    records = [dict(r) for r in store.db.execute(
        'SELECT * FROM event_segments WHERE dataset=? AND instrument_id=? '
        'AND last_id>=? AND first_id<=? ORDER BY first_id,id',
        (aggtrades.DATASET, aggtrades.INSTRUMENT, LOW - 1, HIGH + 1))]
    known, duplicates = {}, 0
    for record in records:
        for row in store._revision_table(record).to_pylist():
            identity = row['agg_trade_id']
            if LOW - 1 <= identity <= HIGH + 1:
                if identity in known:
                    if not aggtrades.same_trade(known[identity], row):
                        raise DataError(f'Existing conflicting ID {identity}; stop for review')
                    duplicates += 1
                known[identity] = row
    if LOW - 1 not in known or HIGH + 1 not in known:
        raise DataError('Expected existing boundary records are absent')
    historical = {}
    for identity in HISTORICAL:
        row = store.db.execute('SELECT * FROM event_collections WHERE id=?', (identity,)).fetchone()
        if row is None or row['state'] == 'running':
            raise DataError(f'Historical report absent or still running: {identity}')
        historical[identity] = dict(row)
    gaps = missing_intervals(known)
    return {'observed_at': now(), 'policy': POLICY, 'remaining_intervals': gaps,
            'missing_count': sum(b-a+1 for a,b in gaps), 'duplicate_rows': duplicates,
            'boundary_rows': [known[LOW-1], known[HIGH+1]],
            'existing_segments': records, 'historical_collections': historical,
            'note': 'Boundary ID LOW-1 may be re-fetched for value comparison; only target gaps are repaired.'}


class BoundedFetcher(Fetcher):
    def __init__(self, log):
        super().__init__(retries=0)
        self.log = log
        self.started = time.monotonic()
        self.next_send = self.started
        self.sent = 0
        self.high_weight_pauses = 0
        self.weights = []
        self.last_clock = (time.time(), time.monotonic())

    def fetch(self, url, weight=1):
        parsed, query = urlparse(url), parse_qs(urlparse(url).query)
        if (parsed.scheme, parsed.netloc, parsed.path) != ('https', 'fapi.binance.com', '/fapi/v1/aggTrades'):
            raise DataError('Job is restricted to the approved public endpoint')
        if (set(query) != {'symbol', 'limit', 'fromId'} or query['symbol'] != ['BTCUSDT']
                or query['limit'] != ['1000'] or weight != 20
                or len(query['fromId']) != 1 or not LOW-1 <= int(query['fromId'][0]) <= HIGH):
            raise DataError('Request is outside the fixed symbol/ID/weight scope')
        delay = max(0, self.next_send - time.monotonic())
        if time.monotonic() + delay - self.started > POLICY['max_elapsed_seconds']:
            raise DataError('Job elapsed-time budget reached; inspect and resume persisted gaps')
        if self.sent >= POLICY['max_requests']:
            raise DataError('Job request budget reached')
        while time.monotonic() < self.next_send:
            time.sleep(max(0, min(1.0, self.next_send - time.monotonic())))
        if shutil.disk_usage(ROOT).free < POLICY['minimum_free_bytes']:
            raise DataError('Insufficient free space')
        wall, mono = time.time(), time.monotonic()
        discontinuity = (wall-self.last_clock[0]) - (mono-self.last_clock[1])
        if abs(discontinuity) > 0.25:
            raise DataError('Observed local clock discontinuity; stop without rewriting timestamps')
        self.last_clock = wall, mono
        self.sent += 1
        record = {'request': self.sent, 'url': url, 'requested_weight': weight,
                  'started_at': now(), 'started_monotonic': mono,
                  'pacing_wait_seconds': delay, 'wall_minus_monotonic_delta_seconds': discontinuity}
        try:
            response = super().fetch(url, weight)
        except SourceError as exc:
            record.update(error=str(exc), status=exc.status, headers=exc.headers,
                          finished_at=now(), elapsed_monotonic=time.monotonic()-mono)
            self.write_log(record)
            raise  # collect_rest persists the existing raw-error/cooldown evidence.
        weight_text = response.headers.get('x-mbx-used-weight-1m')
        used = int(weight_text) if weight_text is not None and weight_text.isdigit() else None
        if used is not None:
            self.weights.append(used)
        # Missing weight evidence pauses too; never creates permission for a burst.
        pause = used is None or used >= POLICY['aggregate_weight_pause_at']
        self.high_weight_pauses += int(pause)
        self.next_send = max(mono + POLICY['min_request_interval_seconds'],
                             time.monotonic() + (POLICY['high_weight_pause_seconds'] if pause else 0))
        record.update(status=200, received_at=response.received_at, headers=response.headers,
                      body_sha256=hashlib.sha256(response.body).hexdigest(), bytes=len(response.body),
                      elapsed_monotonic=time.monotonic()-mono, used_weight_1m=used,
                      pause_before_next=pause)
        self.write_log(record)
        return response

    def write_log(self, record):
        self.log.write(json_bytes(record).decode() + '\n')
        self.log.flush()
        os.fsync(self.log.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if not Path(aggtrades.__file__).resolve().is_relative_to(RELEASE):
        raise DataError('Use the fixed release Python with -I')
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'operator.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = Store(ROOT)
        try:
            plan = current_plan(store)
            attempt = now().strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
            folder = OUT / attempt
            folder.mkdir()
            save(folder / 'plan.json', plan)
            print(json_bytes({'attempt': attempt, 'remaining': plan['remaining_intervals'],
                              'missing': plan['missing_count'], 'mode': 'run' if args.run else 'plan'}).decode(), flush=True)
            if args.plan:
                return
            before = environment()
            save(folder / 'environment-before.json', before)
            if before['disk_free_bytes'] < POLICY['minimum_free_bytes']:
                raise DataError('Insufficient free space')
            if before['collectors']['returncode'] != 0:
                raise DataError('Cannot read existing collector state')
            receipt = {'started_at': now(), 'attempt': attempt, 'policy': POLICY, 'collections': []}
            with (folder / 'http.jsonl').open('x') as log:
                fetcher = BoundedFetcher(log)
                try:
                    for first, last in plan['remaining_intervals']:
                        report = aggtrades.collect_rest(store, first_id=first-1 if first == LOW else first,
                            last_id=last, fetcher=fetcher, max_pages=POLICY['max_pages_per_collection'],
                            progress=lambda p: print(json_bytes(p).decode(), flush=True) if p['pages'] % 10 == 0 else None)
                        receipt['collections'].append(report)
                        save(folder / 'receipt.json', receipt)
                        if not report['complete']:
                            raise DataError('REST returned an incomplete range; stop for inspection')
                    receipt['state'] = 'collected' if plan['missing_count'] else 'already_present_no_requests'
                except (Exception, KeyboardInterrupt) as exc:
                    receipt.update(state='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                                   error=f'{type(exc).__name__}: {exc}')
                    raise
                finally:
                    fetcher.close()
                    receipt.update(finished_at=now(), requests=fetcher.sent, bytes=fetcher.bytes,
                                   used_weight_min=min(fetcher.weights, default=None),
                                   used_weight_max=max(fetcher.weights, default=None),
                                   high_weight_pauses=fetcher.high_weight_pauses)
                    save(folder / 'receipt.json', receipt)
                    save(folder / 'environment-after.json', environment())
            print(json_bytes(receipt).decode(), flush=True)
        finally:
            store.close()


if __name__ == '__main__':
    main()
