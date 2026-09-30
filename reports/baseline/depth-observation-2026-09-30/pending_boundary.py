"""Explain the one pending REST check without changing its live verdict."""

import gzip
import json
import runpy
from decimal import Decimal
from pathlib import Path

from marketdata import depth
from marketdata.domain import json_bytes

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
ReadOnlyStore = runpy.run_path(
    str(ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
)["ReadOnlyStore"]


def main():
    identity = "ca1c788ee26e40d0aa072c9394b96321"
    reader = ReadOnlyStore(ROOT)
    try:
        report = json.loads(depth.collection_record(reader, identity)["report"])
        replayed = depth.replay(reader, identity)
        (OUT / "pending-session-replay.json").write_bytes(json_bytes(replayed))
        epoch = report["epochs"][-1]
        sequence = epoch["final"]["pending_check_sequence"]
        tail = []
        for segment in reader.db.execute(
            "SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id DESC LIMIT 3",
            (identity,),
        ).fetchall():
            tail.extend(reader._revision_table(segment).to_pylist())
        tail.sort(key=lambda row: row["sequence"])
        pending = next(row for row in tail if row["sequence"] == sequence)
        update = max((row for row in tail if row["kind"] == "update"), key=lambda r: r["sequence"])
        assert pending == tail[-1] and pending["kind"] == "check"
        assert pending["epoch"] == update["epoch"] == epoch["epoch"]
        assert update["final_update_id"] == epoch["final"]["book"]["last_update_id"]
        assert pending["final_update_id"] > update["final_update_id"]
        pairs = 0
        for row in (update, pending):
            blob, _ = reader.raw(row["source_id"])
            if row["kind"] == "update":
                envelope = json.loads(gzip.decompress(blob))[row["source_row"]]
                original = json.loads(envelope["message"])
                assert original["U"] == row["first_update_id"]
                assert original["pu"] == row["previous_update_id"]
                assert original["u"] == row["final_update_id"]
                sides = (("bids", "b"), ("asks", "a"))
            else:
                original = json.loads(blob)
                assert original["lastUpdateId"] == row["final_update_id"]
                sides = (("bids", "bids"), ("asks", "asks"))
            for target, source in sides:
                assert [(v["price"], v["quantity"]) for v in row[target]] == [
                    (Decimal(p), Decimal(q)) for p, q in original[source]
                ]
                pairs += len(row[target])
        metadata = [
            json.loads(r[0])
            for r in reader.db.execute(
                "SELECT metadata FROM fetches WHERE artifact_id=?", (pending["source_id"],)
            )
        ]
        matching = next(
            m
            for m in metadata
            if m.get("collection_id") == identity and m.get("sequence") == sequence
        )
        assert replayed["valid"] and replayed["replay_matches_live"]
        assert replayed["verification_pending"] == 1 and not replayed["verification_complete"]
        result = {
            "collection_id": identity,
            "epoch": epoch["epoch"],
            "end_reason": epoch["end_reason"],
            "epoch_ended_at": epoch["ended_at"],
            "last_update": {
                k: update[k] for k in ("sequence", "received_at", "final_update_id", "source_id")
            },
            "pending_snapshot": {
                k: pending[k] for k in ("sequence", "received_at", "final_update_id", "source_id")
            },
            "snapshot_request": matching,
            "following_rows_in_this_collection": sum(r["sequence"] > sequence for r in tail),
            "pending_is_final_collection_row": True,
            "tail_raw_pairs_equal": pairs,
            "replay_matches_live": True,
            "replay_valid": True,
            "verification_pending": 1,
            "verification_complete": False,
            "interpretation": "Final REST check is ahead of the last received update; collection duration ended with no following update to align it. Preserve it as unverified; do not bridge the next physical socket or relabel it as a match.",
            "live_record_changed": False,
        }
        (OUT / "pending-boundary.json").write_bytes(json_bytes(result))
        print(json_bytes(result).decode())
    finally:
        reader.db.close()
        reader._files_db.close()


if __name__ == "__main__":
    main()
