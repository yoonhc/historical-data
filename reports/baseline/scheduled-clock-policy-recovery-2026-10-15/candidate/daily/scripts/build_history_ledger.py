"""Audit the extended BTCUSDT history from each observed source start, without new snapshots."""

import argparse
from pathlib import Path

import pyarrow.compute as pc
from marketdata.domain import Key, json_bytes, now, utc
from marketdata.research import dataset_ledger
from marketdata.store import Store, atomic_write

RANGES = [
    (Key("trade_bars", market="spot"), "2019-01-01"),
    (Key("trade_bars"), "2019-09-08T17:57:00Z"),
    (Key("mark_price_bars"), "2019-12-23T11:58:00Z"),
    (Key("index_price_bars"), "2019-12-23T11:58:00Z"),
    (Key("funding_events", interval="event"), "2019-09-10T08:00:00Z"),
    (Key("open_interest", interval="5m", series="archive"), "2020-09-01"),
]


def build(store, end):
    report = {
        "checked_at": now(),
        "end_exclusive": end,
        "start_policy": "observed_source_availability_2026_09_23; spot_requested_2019_01_01",
        "datasets": {},
        "note": "Grid coverage does not prove full bar duration, OI semantics, or point-in-time availability.",
    }
    with store.db:
        store.db.execute("BEGIN")
        for key, start in RANGES:
            start = utc(start)
            ledger = dataset_ledger(store, key, start, end)
            flags = {"short_source_bar": [], "exact_source_duplicate": []}
            for record in store.revision_list(key, start, end):
                table = store._revision_table(record, columns=["timestamp", "quality_flags"])
                stamps = table["timestamp"]
                table = table.filter(pc.and_(pc.greater_equal(stamps, start), pc.less(stamps, end)))
                values = pc.list_flatten(table["quality_flags"])
                for flag in flags:
                    count = pc.sum(pc.equal(values, flag)).as_py() or 0
                    if count:
                        flags[flag].append({"day": record["day"], "rows": count})
            ledger["quality_flag_rows"] = {
                flag: {"total": sum(day["rows"] for day in days), "days": days}
                for flag, days in flags.items()
            }
            report["datasets"][key.id] = ledger
            print(
                json_bytes(
                    {
                        "key": key.id,
                        "rows": ledger["rows"],
                        "missing_rows": ledger["missing_rows"],
                        "blocked_days": len(ledger["blocked_days"]),
                        "complete": ledger["complete"],
                    }
                ).decode(),
                flush=True,
            )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--end", default="2026-09-22")
    parser.add_argument(
        "--output", type=Path, default=Path("reports/history-extension/ledger.json")
    )
    args = parser.parse_args()
    end = utc(args.end)
    if end <= max(utc(start) for _, start in RANGES) or end > now():
        parser.error("End must follow all source starts and cannot be in the future")
    store = Store(args.root)
    try:
        atomic_write(args.output, json_bytes(build(store, end)))
    finally:
        store.close()


if __name__ == "__main__":
    main()
