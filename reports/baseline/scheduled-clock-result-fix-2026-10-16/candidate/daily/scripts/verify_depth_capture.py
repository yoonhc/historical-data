"""Check stored depth rows against original REST bytes and WebSocket messages."""

import argparse
import gzip
import json
from collections import deque
from decimal import Decimal
from pathlib import Path

from marketdata.depth import DATASET, Book, Session, collection_record, replay
from marketdata.domain import DataError, from_epoch, json_bytes, now, utc
from marketdata.store import Store, atomic_write


class DelayedChecks:
    """Verify lagging REST responses against prior captured updates without changing live results."""

    def __init__(self, report):
        self.report = report
        self.epoch = None
        self.session = None
        self.history = deque()
        self.level_count = 0
        self.results = []

    def take(self, row):
        if self.epoch != row["epoch"]:
            self.epoch = row["epoch"]
            self.session = Session(
                self.report["top"],
                refresh_from_checks=self.report.get("refresh_from_checks", False),
            )
            self.history.clear()
            self.level_count = 0
        session = self.session
        if session.error:
            return
        if row["kind"] == "check" and session.book and row["final_update_id"] < session.book.last:
            result = {
                "epoch": self.epoch,
                "snapshot_sequence": row["sequence"],
                "snapshot_update_id": row["final_update_id"],
                "target_update_id": session.book.last,
                "history_events": len(self.history),
                "comparable": False,
                "equal": False,
            }
            try:
                candidate = Book(row)
                for update in self.history:
                    candidate.apply(update)
                a, b = session.book.view(self.report["top"]), candidate.view(self.report["top"])
                result["comparable"] = (
                    candidate.synced
                    and candidate.last == session.book.last
                    and a["top_complete"]
                    and b["top_complete"]
                )
                result["equal"] = (
                    result["comparable"] and a["bids"] == b["bids"] and a["asks"] == b["asks"]
                )
            except DataError as exc:
                result["reason"] = str(exc)
            self.results.append(result)
        before = session.counts["applied"]
        try:
            session.take(row)
        except (DataError, KeyError) as exc:
            session.error = str(exc)
        if row["kind"] == "update" and session.counts["applied"] > before:
            self.history.append(row)
            self.level_count += len(row["bids"]) + len(row["asks"])
            while len(self.history) > 128 or self.level_count > 50000:
                old = self.history.popleft()
                self.level_count -= len(old["bids"]) + len(old["asks"])


def verify(store, collection_id=None, *, replayed=None):
    identity = collection_record(store, collection_id)["id"]
    replayed = replayed if replayed is not None else replay(store, identity)
    report = json.loads(collection_record(store, identity)["report"])
    # New history-aware captures already attempted bounded alignment in the
    # production state machine. Extra legacy-only reconstruction must not alter
    # their interpretation or cross their recorded connection boundaries.
    delayed = (
        DelayedChecks(report)
        if replayed["verification_uncomparable"] and not report.get("history_policy")
        else None
    )
    source_ids, rows_checked, levels_checked = set(), 0, 0
    for segment in store.db.execute(
        "SELECT * FROM event_segments WHERE dataset=? AND collection_id=? ORDER BY first_id",
        (DATASET, identity),
    ):
        rows = store._revision_table(segment).to_pylist()
        sources = {}
        for row in rows:
            source_id = row["source_id"]
            if source_id not in sources:
                blob, _ = store.raw(source_id)
                sources[source_id] = json.loads(
                    gzip.decompress(blob) if row["kind"] == "update" else blob
                )
                source_ids.add(source_id)
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
            if delayed:
                delayed.take(row)
    assert rows_checked == replayed["rows"]
    delayed_results = delayed.results if delayed else []
    resolved = {r["snapshot_sequence"] for r in delayed_results if r["equal"]}
    checks = [c for e in replayed["epochs"].values() for c in e["checks"]]
    all_verified = (
        bool(checks)
        and not replayed["verification_pending"]
        and all(c["equal"] or c["snapshot_sequence"] in resolved for c in checks)
    )
    return {
        "checked_at": now(),
        "collection_id": identity,
        "source_artifacts": len(source_ids),
        "rows_checked": rows_checked,
        "price_quantity_pairs_checked": levels_checked,
        "raw_to_canonical_equal": True,
        "replay_valid": replayed["valid"],
        "replay_matches_live": replayed["replay_matches_live"],
        "verification_checks": replayed["verification_checks"],
        "verification_equal": replayed["verification_equal"],
        "verification_complete": replayed["verification_complete"],
        "delayed_snapshot_checks": delayed_results,
        "all_snapshot_checks_verified": all_verified,
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
