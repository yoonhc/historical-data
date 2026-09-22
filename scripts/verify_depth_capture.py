"""Check stored depth rows against original REST bytes and WebSocket messages."""

import argparse
import gzip
import json
from decimal import Decimal
from pathlib import Path

from marketdata.depth import DATASET, collection_record, replay
from marketdata.domain import from_epoch, json_bytes, now, utc
from marketdata.store import Store, atomic_write


def verify(store, collection_id=None):
    identity = collection_record(store, collection_id)["id"]
    replayed = replay(store, identity)
    sources, rows_checked, levels_checked = {}, 0, 0
    for segment in store.db.execute(
        "SELECT * FROM event_segments WHERE dataset=? AND collection_id=? ORDER BY first_id",
        (DATASET, identity),
    ):
        rows = store._revision_table(segment).to_pylist()
        for row in rows:
            source_id = row["source_id"]
            if source_id not in sources:
                blob, _ = store.raw(source_id)
                sources[source_id] = json.loads(
                    gzip.decompress(blob) if row["kind"] == "update" else blob
                )
            payload = sources[source_id]
            if row["kind"] == "update":
                original = payload[row["source_row"]]
                raw = json.loads(original["message"])
                assert row["epoch"] == original["epoch"]
                assert row["sequence"] == original["sequence"]
                assert row["received_at"] == utc(original["received_at"])
                assert row["first_update_id"] == raw["U"]
                assert row["previous_update_id"] == raw["pu"]
                assert row["final_update_id"] == raw["u"]
                sides = {"bids": "b", "asks": "a"}
            else:
                raw = payload
                assert row["final_update_id"] == raw["lastUpdateId"]
                assert row["first_update_id"] is None and row["previous_update_id"] is None
                metadata = [
                    json.loads(r["metadata"])
                    for r in store.db.execute(
                        "SELECT metadata FROM fetches WHERE artifact_id=?", (source_id,)
                    )
                ]
                assert any(
                    m.get("collection_id") == identity
                    and m.get("sequence") == row["sequence"]
                    and utc(m["received_at"]) == row["received_at"]
                    for m in metadata
                )
                sides = {"bids": "bids", "asks": "asks"}
            assert row["timestamp"] == from_epoch(raw["T"])
            assert row["event_time"] == from_epoch(raw["E"])
            for canonical, source in sides.items():
                pairs = [(r["price"], r["quantity"]) for r in row[canonical]]
                assert pairs == [(Decimal(p), Decimal(q)) for p, q in raw[source]]
                levels_checked += len(pairs)
            rows_checked += 1
    assert rows_checked == replayed["rows"]
    return {
        "checked_at": now(),
        "collection_id": identity,
        "source_artifacts": len(sources),
        "rows_checked": rows_checked,
        "price_quantity_pairs_checked": levels_checked,
        "raw_to_canonical_equal": True,
        "replay_valid": replayed["valid"],
        "replay_matches_live": replayed["replay_matches_live"],
        "verification_checks": replayed["verification_checks"],
        "verification_equal": replayed["verification_equal"],
        "verification_complete": replayed["verification_complete"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--collection-id")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    store = Store(args.root)
    try:
        result = verify(store, args.collection_id)
        atomic_write(args.report, json_bytes(result))
        print(json_bytes(result).decode())
        return 0 if result["replay_valid"] else 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
