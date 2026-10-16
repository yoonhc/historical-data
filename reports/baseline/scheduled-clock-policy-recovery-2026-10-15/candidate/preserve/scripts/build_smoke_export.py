"""Reproducible local validation of the fixed seven-day acquisition window."""

import argparse
import json
from datetime import timedelta
from pathlib import Path

from marketdata.cli import config_keys
from marketdata.domain import Key, json_bytes, now
from marketdata.store import Store, atomic_write
from marketdata_freqtrade.export import export_snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--start", default="2026-09-14")
    parser.add_argument("--end", default="2026-09-21")
    parser.add_argument("--output", type=Path, default=Path("data/exports/freqtrade/smoke-v2"))
    args = parser.parse_args()
    store = Store(args.root)
    try:
        with store.writer():
            keys = config_keys(Path(__file__).resolve().parents[1] / "configs/bootstrap.toml")
            coverage = [store.coverage(k, args.start, args.end) for k in keys]
            snapshot = store.create_snapshot(keys, args.start, args.end)
            assert store.create_snapshot(keys, args.start, args.end) == snapshot
            exported = export_snapshot(store, snapshot, args.output)
            assert (
                export_snapshot(store, snapshot, args.output)["export_id"] == exported["export_id"]
            )
            archive_key = Key("open_interest", interval="5m", series="archive")
            rest_key = Key("open_interest", interval="5m", series="rest")
            archive = {
                r["timestamp"]: r
                for r in store.read(archive_key, args.start, args.end, snapshot).to_pylist()
            }
            rest = store.read(rest_key, args.start, args.end, snapshot).to_pylist()
            parity = []
            for shift in (0, 300):
                pairs = [
                    (archive[r["timestamp"] - timedelta(seconds=shift)], r)
                    for r in rest
                    if r["timestamp"] - timedelta(seconds=shift) in archive
                ]
                exact = sum(
                    a["quantity"] == b["quantity"] and a["notional_value"] == b["notional_value"]
                    for a, b in pairs
                )
                parity.append(
                    {
                        "archive_to_rest_shift_seconds": shift,
                        "comparable": len(pairs),
                        "quantity_and_value_exact": exact,
                    }
                )
            report = {
                "checked_at": now(),
                "start": args.start,
                "end": args.end,
                "snapshot_id": snapshot,
                "export_id": exported["export_id"],
                "coverage": coverage,
                "oi_parity": parity,
                "parity_limit": "Retrospective overlap only; not a universal clock or publication guarantee",
                "export_files": len(exported["files"]),
                "repeated_export_identical": True,
                "bytes": {
                    group: sum(
                        p.stat().st_size
                        for p in (store.root / "data" / group).rglob("*")
                        if p.is_file()
                    )
                    for group in ("raw", "canonical", "exports")
                },
            }
            atomic_write(store.root / "reports/smoke.json", json_bytes(report))
            print(
                json.dumps(
                    {
                        "snapshot_id": snapshot,
                        "export_id": exported["export_id"],
                        "oi_parity": parity,
                        "export_files": report["export_files"],
                    }
                )
            )
    finally:
        store.close()


if __name__ == "__main__":
    main()
