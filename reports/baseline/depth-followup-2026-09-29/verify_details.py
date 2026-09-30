"""Independently reduce actual recovery inputs and verify bootstrap bytes.

Run after analyze.py. Reads the closed checkpoint and the live catalog read-only;
writes only this evidence directory. The raw reducer doesn't call Book.apply.
"""

import gzip
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from marketdata import depth  # noqa: E402
from marketdata.domain import json_bytes, now, sha256, utc  # noqa: E402

from scripts.restic_trial import ArchiveReader  # noqa: E402

OUT = Path(__file__).resolve().parent


def rows_between(reader, cid, first, last):
    rows = []
    for seg in reader.db.execute(
        "SELECT * FROM event_segments WHERE collection_id=? AND first_id<=? AND last_id>=? ORDER BY first_id",
        (cid, last, first),
    ):
        rows.extend(
            r for r in reader._revision_table(seg).to_pylist() if first <= r["sequence"] <= last
        )
    assert [r["sequence"] for r in rows] == list(range(first, last + 1))
    return rows


def payload(reader, row):
    blob, _ = reader.raw(row["source_id"])
    if row["kind"] != "update":
        return json.loads(blob)
    return json.loads(json.loads(gzip.decompress(blob))[row["source_row"]]["message"])


def reduce_raw(snapshot, updates):
    books = {side: {Decimal(p): Decimal(q) for p, q in snapshot[side]} for side in ("bids", "asks")}
    floor, ceiling = min(books["bids"]), max(books["asks"])
    last, bridged, previous, applied = snapshot["lastUpdateId"], False, None, []
    for sequence, event in updates:
        if not bridged:
            if event["u"] < last:
                continue
            assert event["U"] <= last <= event["u"]
        elif event == previous:
            continue
        else:
            assert event["u"] > last and event["pu"] == last
        for side, field in (("bids", "b"), ("asks", "a")):
            for price, quantity in event[field]:
                price, quantity = Decimal(price), Decimal(quantity)
                if quantity:
                    books[side][price] = quantity
                else:
                    books[side].pop(price, None)
        last, bridged, previous = event["u"], True, event
        applied.append(sequence)
    assert bridged
    return (
        {p: q for p, q in books["bids"].items() if p >= floor},
        {p: q for p, q in books["asks"].items() if p <= ceiling},
        last,
        applied,
    )


def main():
    audit = json.loads((OUT / "audit.json").read_bytes())
    reader = ArchiveReader(audit["archive"])
    bootstraps, recoveries, unchanged = [], [], []
    live = sqlite3.connect((ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro", uri=True)
    try:
        for case in audit["cases"]:
            cid = case["collection_id"]
            original = live.execute(
                "SELECT report FROM event_collections WHERE id=?", (cid,)
            ).fetchone()[0]
            assert sha256(original.encode()) == case["original_report_sha256"]
            unchanged.append(cid)
            report = json.loads(original)
            for failure in case["failures"]:
                if failure["failure_type"] == "SnapshotBehind":
                    first = failure["epoch_details"]["first_update"]
                    prefix = json.loads(
                        gzip.decompress(reader.raw(failure["bootstrap"]["source_id"])[0])
                    )
                    journal = json.loads(gzip.decompress(reader.raw(first["source_id"])[0]))[
                        first["source_row"]
                    ]
                    assert len(prefix) == 1
                    assert prefix[0]["message"] == journal["message"]
                    assert utc(prefix[0]["received_at"]) == utc(journal["received_at"])
                    anchor_id = failure["anchor"]["final_update_id"]
                    previous_id = failure["epoch_details"]["previous_epoch_last_update"][
                        "final_update_id"
                    ]
                    assert previous_id < anchor_id < first["first_update_id"]
                    assert first["previous_update_id"] > anchor_id
                    bootstraps.append(
                        {
                            "collection_id": cid,
                            "epoch": failure["epoch"],
                            "bootstrap_message_bytes_equal_journal": True,
                            "receipt_time_equal": True,
                            "previous_socket_final_id": previous_id,
                            "snapshot_id": anchor_id,
                            "first_new_socket_U": first["first_update_id"],
                            "first_new_socket_pu": first["previous_update_id"],
                            "bridge_absent_between_captured_connections": True,
                        }
                    )
                else:
                    anchor = failure["next_epoch"]["anchor"]
                    last = failure["failure"]["sequence"]
                    prefix = [
                        r
                        for r in rows_between(reader, cid, last - 199, last)
                        if r["kind"] == "update"
                    ]
                    assert len(prefix) >= 128 and all(
                        r["epoch"] == failure["epoch"] for r in prefix
                    )
                    history = depth.History()
                    for row in prefix:
                        history.append(row)
                    snapshot = rows_between(reader, cid, anchor["sequence"], anchor["sequence"])[0]
                    candidate, proof = history.align(snapshot)
                    expected = report["epochs"][anchor["epoch"]]["final"]["anchor_history"]
                    assert candidate is not None and proof == expected and proof["aligned"]
                    by_sequence = {r["sequence"]: r for r in prefix}
                    raw_updates = [
                        (row["sequence"], payload(reader, by_sequence[row["sequence"]]))
                        for row, _, _ in history.events
                    ]
                    bids, asks, update_id, applied = reduce_raw(
                        payload(reader, snapshot), raw_updates
                    )
                    assert (candidate.bids, candidate.asks, candidate.last) == (
                        bids,
                        asks,
                        update_id,
                    )
                    assert (len(applied), applied[0], applied[-1]) == (
                        proof["applied"],
                        proof["first_sequence"],
                        proof["last_sequence"],
                    )
                    assert applied[-1] == last
                    assert all(
                        row["sequence"] < snapshot["sequence"]
                        and row["received_at"] < snapshot["received_at"]
                        for row, _, _ in history.events
                    )
                    recoveries.append(
                        {
                            "collection_id": cid,
                            "failed_epoch": failure["epoch"],
                            "new_epoch": anchor["epoch"],
                            "snapshot_sequence": anchor["sequence"],
                            "recorded_anchor_history": proof,
                            "independent_raw_reduction_matches": True,
                            "known_bid_levels": len(bids),
                            "known_ask_levels": len(asks),
                            "last_update_id": update_id,
                            "applied_sequences": applied,
                            "failed_coverage_event_included": True,
                            "all_used_events_received_before_new_anchor": True,
                        }
                    )
    finally:
        live.close()
        reader.close()
    result = {
        "checked_at": now(),
        "bootstrap_byte_comparisons": bootstraps,
        "independent_same_socket_recoveries": recoveries,
        "live_report_hashes_unchanged": unchanged,
        "scope": "exact bootstrap bytes; missing bridge at captured connection boundary; independent raw reducer for two actual same-socket recovery candidates; no historical quality changes",
    }
    (OUT / "detail-verification.json").write_text(
        json.dumps(json.loads(json_bytes(result)), indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "bootstrap_comparisons": len(bootstraps),
                "independent_recoveries": len(recoveries),
                "unchanged_live_reports": len(unchanged),
                "applied_sequences": [r["applied_sequences"] for r in recoveries],
            }
        )
    )


if __name__ == "__main__":
    main()
