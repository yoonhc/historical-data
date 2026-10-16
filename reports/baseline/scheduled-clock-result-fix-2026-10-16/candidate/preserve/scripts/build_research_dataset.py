"""Build a fixed historical coverage report and optionally publish complete snapshots."""

import argparse
import time
from pathlib import Path

from marketdata.cli import config_keys
from marketdata.domain import DAY, json_bytes
from marketdata.research import research_report
from marketdata.store import Store, atomic_write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--config", type=Path, default=Path("configs/research-archive.toml"))
    p.add_argument("--start", default="2023-09-22")
    p.add_argument("--end", default="2026-09-22")
    p.add_argument("--output", type=Path, default=Path("reports/research-2023_2026.json"))
    p.add_argument("--snapshots", action="store_true")
    p.add_argument("--min-snapshot-days", type=int, default=7)
    args = p.parse_args()
    if args.min_snapshot_days < 1:
        p.error("--min-snapshot-days must be positive")
    keys = config_keys(args.config)
    store = Store(args.root)
    try:
        report = research_report(store, keys, args.start, args.end)
        atomic_write(args.output, json_bytes(report))
        if args.snapshots:
            by_id = {key.id: key for key in keys}
            snapshots = {}
            for name, profile in report["profiles"].items():
                profile["snapshots"] = []
                for begin, end in profile["contiguous_ranges"]:
                    if end - begin < args.min_snapshot_days * DAY:
                        continue
                    members = [by_id[k] for k in profile["keys"]]
                    token = (tuple(profile["keys"]), begin, end)
                    if token not in snapshots:
                        with store.writer(timeout=60):
                            snapshots[token] = store.create_snapshot(members, begin, end)
                        time.sleep(1)
                    profile["snapshots"].append(
                        {"start": begin, "end": end, "snapshot_id": snapshots[token]}
                    )
                    atomic_write(args.output, json_bytes(report))
                    print(
                        json_bytes({"profile": name, **profile["snapshots"][-1]}).decode(),
                        flush=True,
                    )
            report["snapshot_min_days"] = args.min_snapshot_days
            atomic_write(args.output, json_bytes(report))
        print(
            json_bytes(
                {
                    "report": str(args.output),
                    "profiles": {
                        name: {
                            "complete": p["complete"],
                            "ranges": len(p["contiguous_ranges"]),
                            "snapshots": len(p.get("snapshots", [])),
                        }
                        for name, p in report["profiles"].items()
                    },
                }
            ).decode()
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
