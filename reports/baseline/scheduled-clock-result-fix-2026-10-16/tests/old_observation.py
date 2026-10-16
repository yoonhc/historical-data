"""Run the unchanged operating parent CLI on an isolated store, with fake I/O.

Invoked with the parent's Python. No service, time control, or real network use.
"""

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("job")
parser.add_argument("parent", type=Path)
parser.add_argument("target", type=Path)
parser.add_argument("at")
args = parser.parse_args()

if args.job == "oi-probe":
    from marketdata import cli, oi_probe
    from marketdata.binance import Response
    from marketdata.domain import utc

    at = utc(args.at)

    class Fetch:
        def fetch(self, url, weight):
            return Response(b"[]", url, at + timedelta(seconds=0.1), {})

    cli.probe_once = lambda store: oi_probe.probe_once(store, Fetch(), clock=lambda: at)
    sys.argv = ["marketdata", "--root", str(args.target), "oi-probe"]
    code = cli.main()
    print(json.dumps({"module": oi_probe.__file__, "exit_code": code}))
    raise SystemExit(code)
else:
    sys.path.insert(0, str(args.parent))
    from scripts import network_observer as observer

    observer.observe = lambda _: {
        "started_at": args.at,
        "finished_at": args.at,
        "status": "degraded",
        "failed_probes": ["dns"],
        "probes": {"dns": {"ok": False, "detail": "후속 관측"}},
    }
    sys.argv = ["network-observer", "--profile", str(args.target)]
    observer.main()
    print(json.dumps({"module": observer.__file__, "exit_code": 0}))
