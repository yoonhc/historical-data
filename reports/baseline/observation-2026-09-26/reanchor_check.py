"""Inspect the already-received bridge across the same-socket coverage reset.

This is an offline scenario, not a rewrite of the failed epoch or its quality.
Run from the repository root after audit.py.
"""

import importlib.util
import json
from pathlib import Path

from audit import OUT, ROOT, Reader, save
from marketdata import depth
from marketdata.domain import now, sha256

source = ROOT / "reports/baseline/depth-coverage-2026-09-25/analyze.py"
spec = importlib.util.spec_from_file_location("coverage_evidence", source)
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)
reader = Reader()
try:
    identity = "ed201401a9d0445f995aa12f3a8f3c49"
    report = json.loads(
        reader.db.execute(
            "SELECT report FROM event_collections WHERE id=?", (identity,)
        ).fetchone()[0]
    )
    previous, previous_inputs, _ = coverage.load_rows(reader.db, report, report["epochs"][4])
    rows, inputs, _ = coverage.load_rows(reader.db, report, report["epochs"][5])
    anchor = rows[0]
    history = [r for r in previous if r["kind"] == "update"][-128:]
    pairs = sum(len(r["bids"]) + len(r["asks"]) for r in history)
    while history and pairs > 50000:
        old = history.pop(0)
        pairs -= len(old["bids"]) + len(old["asks"])
    candidate = depth.Book(anchor)
    applied = []
    for update in history:
        assert update["received_at"] < anchor["received_at"]
        if candidate.apply(update) == "applied":
            applied.append(
                {
                    k: update[k]
                    for k in (
                        "sequence",
                        "first_update_id",
                        "final_update_id",
                        "previous_update_id",
                        "timestamp",
                        "received_at",
                        "source_id",
                        "source_row",
                    )
                }
            )
    assert candidate.synced and candidate.last == rows[1]["previous_update_id"]
    result = {
        "checked_at": now(),
        "collection_id": identity,
        "previous_epoch": 4,
        "new_epoch": 5,
        "history_events": len(history),
        "history_price_quantity_pairs": pairs,
        "history_event_limit": 128,
        "history_pair_limit": 50000,
        "anchor_id": anchor["final_update_id"],
        "anchor_received_at": anchor["received_at"],
        "past_updates_applied": applied,
        "aligned_before_first_new_epoch_update": True,
        "aligned_book": candidate.view(20),
        "subsequent_updates_applied": 0,
        "source_segments": previous_inputs + inputs,
        "script_sha256": sha256(Path(__file__).read_bytes()),
        "scope": "Only already-received same-socket events align the anchor; future events are applied afterward in sequence. No new independent top-20 validation.",
    }
    for update in rows[1:]:
        assert update["kind"] == "update" and update["received_at"] >= anchor["received_at"]
        try:
            candidate.apply(update)
        except depth.SyncLost as exc:
            result.update(subsequent_error=str(exc), error_sequence=update["sequence"])
            break
        result["subsequent_updates_applied"] += 1
    result["survives_recorded_lagged_epoch"] = result["subsequent_updates_applied"] == len(rows) - 1
    result["final_candidate_book"] = candidate.view(20)
    save("same-socket-reanchor.json", result, OUT)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "history_events",
                    "history_price_quantity_pairs",
                    "aligned_before_first_new_epoch_update",
                    "subsequent_updates_applied",
                    "survives_recorded_lagged_epoch",
                )
            }
        )
    )
finally:
    reader.db.close()
