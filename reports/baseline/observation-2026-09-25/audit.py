import datetime as dt
import json
from pathlib import Path
import time

from marketdata.aggtrades import read_trades
from marketdata.depth import replay
from marketdata.domain import json_bytes
from marketdata.store import Store

root = Path.cwd()
out = root / 'reports/observation-2026-09-25'
start = dt.datetime.fromisoformat('2026-09-23T23:30:00+00:00')
end = start + dt.timedelta(days=1)
store = Store(root)
reports, boundaries = [], []
last = None
for i in range(24):
    left = start + dt.timedelta(hours=i)
    rows, report = read_trades(store, left, left + dt.timedelta(hours=1))
    report['first_timestamp'] = rows[0]['timestamp'] if rows else None
    report['last_timestamp'] = rows[-1]['timestamp'] if rows else None
    if last and rows:
        boundaries.append({'previous_last_id': last['agg_trade_id'], 'next_first_id': rows[0]['agg_trade_id'], 'contiguous': rows[0]['agg_trade_id'] == last['agg_trade_id'] + 1, 'time_ordered': rows[0]['timestamp'] >= last['timestamp']})
    if rows:
        last = rows[-1]
    (out / f'aggtrade-hour-{i:02}.json').write_bytes(json_bytes(report))
    reports.append({k:v for k,v in report.items() if k != 'segments'})
    print(json.dumps({'hour':i, 'rows':report['rows'], 'valid':report['valid']}), flush=True)
    del rows
summary = {'start':start, 'end':end, 'hours':reports, 'boundaries':boundaries, 'rows':sum(r['rows'] for r in reports), 'valid':all(r['valid'] for r in reports) and all(b['contiguous'] and b['time_ordered'] for b in boundaries), 'semantics':'continuity between observed IDs; exact time-window edge completeness not externally proved'}
(out / 'aggtrade-audit.json').write_bytes(json_bytes(summary))
print(json.dumps({'aggtrade_rows':summary['rows'],'valid':summary['valid']}), flush=True)
selected = ['5039a296a6164759a0c5fe9ae9c21390', '8603679e4a404900b3462c1accc18291', 'c4f7372307de4ab1be3ebfdf693020f5']
depth_results = []
for identity in selected:
    begun = time.monotonic()
    result = replay(store, identity)
    (out / f'depth-{identity}.json').write_bytes(json_bytes(result))
    reduced = {k:v for k,v in result.items() if k not in ['epochs', 'segments']}
    reduced['elapsed_seconds'] = time.monotonic() - begun
    depth_results.append(reduced)
    print(json.dumps(reduced), flush=True)
(out / 'depth-replay-summary.json').write_bytes(json_bytes(depth_results))
store.close()
