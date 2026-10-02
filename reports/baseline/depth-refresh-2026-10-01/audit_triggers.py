"""Inspect already verified historical traces; do not invent missing REST responses."""

import argparse
import csv
import json
import os
import runpy
from decimal import Decimal
from pathlib import Path

from marketdata import depth
from marketdata.depth_refresh import LEVEL_FRACTION, MIN_INTERVAL, PRICE_FRACTION, metadata
from marketdata.domain import json_bytes, now, sha256, utc

IDS = [
    "079691756a6e496c909e9019ef4d34d8",
    "517f5709097f4383889425c292347475",
    "ac31dd2ebf954619911b26fd004e3309",
]
OUT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.source_root.resolve()
    os.nice(10)
    reader_type = runpy.run_path(
        str(root / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
    )["ReadOnlyStore"]
    reader = reader_type(root)
    evidence = root / "reports/baseline/stream-followup-2026-10-01"
    result = {
        "started_at": now(),
        "policy": metadata(),
        "cases": [],
        "replays": [],
        "scope": "threshold signals on preserved old-book paths after the last adopted REST response; assumes extra quota available and uses receipt wall time; no counterfactual REST or new-book trajectory, flush delay, or quota simulation; not proof of preventing these historical failures",
    }
    try:
        for identity in IDS:
            report = json.loads((evidence / f"{identity}.json").read_bytes())
            for failure in report["failures"]:
                if failure["failure_type"] != "CoverageExhausted":
                    continue
                anchor = failure["last_refreshed_snapshot"]
                raw, _ = reader.raw(anchor["source_id"])
                assert sha256(raw) == anchor["source_id"]
                snapshot = json.loads(raw)
                spans = {
                    side: max(Decimal(p) for p, _ in snapshot[side])
                    - min(Decimal(p) for p, _ in snapshot[side])
                    for side in ("bids", "asks")
                }
                trace_path = evidence / f"{identity}-epoch-{failure['epoch']}-trace.csv"
                with trace_path.open() as handle:
                    trace = list(csv.DictReader(handle))
                start, finish = utc(anchor["received_at"]), utc(failure["failure"]["received_at"])
                candidates = []
                for row in trace:
                    age = (utc(row["received_at"]) - start).total_seconds()
                    if row["kind"] != "update" or int(row["sequence"]) <= anchor["sequence"]:
                        continue
                    if age < MIN_INTERVAL or row["pending_check_sequence"]:
                        continue
                    low = [
                        side
                        for side, field in (("bids", "bid"), ("asks", "ask"))
                        if int(row[field + "_levels"]) < int(len(snapshot[side]) * LEVEL_FRACTION)
                        or Decimal(row[field + "_room_usdt"]) < spans[side] * PRICE_FRACTION
                    ]
                    if low:
                        candidates.append(
                            {
                                "sequence": int(row["sequence"]),
                                "received_at": row["received_at"],
                                "low_sides": low,
                                "seconds_after_response": age,
                                "seconds_before_exhaustion": (
                                    finish - utc(row["received_at"])
                                ).total_seconds(),
                            }
                        )
                result["cases"].append(
                    {
                        "collection_id": identity,
                        "epoch": failure["epoch"],
                        "snapshot_source_id": anchor["source_id"],
                        "trace_sha256": sha256(trace_path.read_bytes()),
                        "exhaustion_at": finish,
                        "first_signal": candidates[0] if candidates else None,
                    }
                )
            replay = depth.replay(reader, identity)
            assert replay["replay_matches_live"] and not replay["valid"]
            result["replays"].append(
                {
                    "collection_id": identity,
                    "rows": replay["rows"],
                    "replay_matches_live": replay["replay_matches_live"],
                    "valid": replay["valid"],
                }
            )
            print(json.dumps(result["replays"][-1]), flush=True)
        result["finished_at"] = now()
        (OUT / "historical-triggers.json").write_bytes(json_bytes(result))
    finally:
        reader.db.close()
        reader._files_db.close()


if __name__ == "__main__":
    main()
