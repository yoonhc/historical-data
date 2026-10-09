"""Export the reviewed historical snapshot windows without blocking collection."""

import argparse
import json
import resource
import time
from pathlib import Path

from marketdata.domain import json_bytes
from marketdata.store import Store, atomic_write
from marketdata_freqtrade.window import export_window


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=root)
    parser.add_argument("--dataset", action="append", help="Default: all reviewed windows")
    args = parser.parse_args()
    plan = json.loads((root / "integrations/freqtrade/datasets.json").read_bytes())
    names = args.dataset or list(plan["datasets"])
    if set(names) - plan["datasets"].keys():
        parser.error("Unknown dataset")
    store = Store(args.root)
    results = []
    try:
        for name in names:
            spec = plan["datasets"][name]
            destination = args.root / "data/exports/freqtrade/research-v3" / name
            begin = time.monotonic()
            manifest = export_window(
                store,
                spec["snapshot_id"],
                destination,
                spec["start"],
                spec["end"],
                plan["timeframes"],
                progress=lambda event: print(json.dumps({"dataset": name, **event}), flush=True),
            )
            result = {
                "dataset": name,
                "export_id": manifest["export_id"],
                **spec,
                "source_snapshot_start": manifest["source_snapshot_start"],
                "source_snapshot_end": manifest["source_snapshot_end"],
                "files": len(manifest["files"]),
                "native_rows": {r["path"]: r["rows"] for r in manifest["files"] if r["native"]},
                "seconds": round(time.monotonic() - begin, 3),
                "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            }
            results.append(result)
            atomic_write(args.root / "reports/freqtrade-exports.json", json_bytes(results))
            print(json.dumps(result), flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    main()
