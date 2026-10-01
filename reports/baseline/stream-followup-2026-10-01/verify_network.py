"""Verify stored trade backfills and record depth receive gaps across the network incident."""

import json
import os
import runpy
from datetime import datetime, timezone
from pathlib import Path

from marketdata.aggtrades import parse_trade, read_trades
from marketdata.domain import json_bytes, utc

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
RECOVERIES = ["4618dddfda1b43f59148e029cc6e7f56", "80a3a9c72d014b50bf7b207624fa7d1f"]
DEPTH_SESSIONS = [
    "55f762ff74df499685b4620f5e8f71e3",
    "3c45a2e8cc4544fba46bf6636f801e64",
    "761dbdba56584b6da6bb4fc05bfff2c7",
]


def save(name, value):
    (OUT / name).write_text(
        json.dumps(json.loads(json_bytes(value)), ensure_ascii=False, indent=2) + "\n"
    )


def main():
    os.nice(10)
    read_only_store = runpy.run_path(
        str(ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
    )["ReadOnlyStore"]
    reader = read_only_store(ROOT)
    start, end = utc("2026-10-01T06:40:00+00:00"), utc("2026-10-01T07:30:00+00:00")
    try:
        rows, combined = read_trades(reader, start, end)
        _, websocket = read_trades(reader, start, end, kind="websocket")
        assert combined["valid"] and not combined["id_gaps"] and not combined["conflicts"]
        save("aggtrade-network-continuity.json", {"combined": combined, "websocket": websocket})
        print(
            json_bytes(
                {
                    "combined_rows": len(rows),
                    "id_gaps": combined["id_gaps"],
                    "websocket_gaps": websocket["id_gaps"],
                }
            ).decode(),
            flush=True,
        )
        recoveries = []
        repaired_ids = set()
        for identity in RECOVERIES:
            report = json.loads(
                reader.db.execute(
                    "SELECT report FROM event_collections WHERE id=?", (identity,)
                ).fetchone()[0]
            )
            checked_ids, source_ids, times = [], set(), []
            for segment in reader.db.execute(
                "SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id", (identity,)
            ).fetchall():
                assert segment["kind"] == "rest"
                source_id = segment["source_id"]
                payload = json.loads(reader.raw(source_id)[0])
                source_ids.add(source_id)
                for row in reader._revision_table(segment).to_pylist():
                    parsed = parse_trade(
                        payload[row["source_row"]], source_id, row["source_row"], row["received_at"]
                    )
                    assert json_bytes(parsed) == json_bytes(row)
                    checked_ids.append(row["agg_trade_id"])
                    times.append(row["timestamp"])
            assert report["complete"] and report["state"] == "complete"
            assert checked_ids == list(range(min(checked_ids), max(checked_ids) + 1))
            repaired_ids.update(checked_ids)
            recoveries.append(
                {
                    "collection_id": identity,
                    "report": report,
                    "rows_checked": len(checked_ids),
                    "source_artifacts": len(source_ids),
                    "first_id": min(checked_ids),
                    "last_id": max(checked_ids),
                    "first_trade_time": min(times),
                    "last_trade_time": max(times),
                    "canonical_hash_and_raw_parity_verified": True,
                }
            )
        ws_gaps = {i for lo, hi in websocket["id_gaps"] for i in range(lo, hi + 1)}
        assert ws_gaps == repaired_ids
        save(
            "aggtrade-rest-recoveries.json",
            {
                "checked_at": datetime.now(timezone.utc),
                "recoveries": recoveries,
                "websocket_missing_ids": len(ws_gaps),
                "rest_recovered_ids": len(repaired_ids),
                "exactly_fills_websocket_id_gaps": True,
                "scope": "continuity between observed IDs in the fixed 50-minute window; no whole-day raw trade audit",
            },
        )
        previous, gaps, samples = None, [], []
        total = 0
        for identity in DEPTH_SESSIONS:
            report = json.loads(
                reader.db.execute(
                    "SELECT report FROM event_collections WHERE id=?", (identity,)
                ).fetchone()[0]
            )
            samples.append(
                {k: report[k] for k in ["id", "state", "started_at", "updated_at", "errors"]}
            )
            columns = ["received_at", "sequence", "epoch", "kind", "final_update_id"]
            for segment in reader.db.execute(
                "SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id", (identity,)
            ).fetchall():
                for row in reader._revision_table(segment, columns=columns).to_pylist():
                    if row["kind"] != "update":
                        continue
                    total += 1
                    current = {"collection_id": identity, **row}
                    if previous:
                        seconds = (row["received_at"] - previous["received_at"]).total_seconds()
                        if seconds > 10:
                            gaps.append({"before": previous, "after": current, "seconds": seconds})
                    previous = current
        save(
            "depth-network-gaps.json",
            {
                "checked_at": datetime.now(timezone.utc),
                "sessions": samples,
                "persisted_updates_examined": total,
                "received_gaps_over_10_seconds": gaps,
                "semantics": "application receive timestamps between stored updates, not exchange outage duration; physical socket boundaries remain unreconstructed",
                "canonical_hashes_verified": True,
            },
        )
        print(
            json_bytes(
                {
                    "rest_recovered_ids": len(repaired_ids),
                    "depth_receive_gaps_seconds": [g["seconds"] for g in gaps],
                }
            ).decode(),
            flush=True,
        )
    finally:
        reader.db.close()
        reader._files_db.close()


if __name__ == "__main__":
    main()
