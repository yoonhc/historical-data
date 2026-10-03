"""Audit new partial sessions with raw parity and exact original replay semantics.

Adapted from the reviewed 2026-09-29 analyzer. Adds explicit incomplete-top
failures and permits lagged anchors following same-socket coverage recovery.
Only this observation directory receives output; the live store is read-only.
"""

import csv
import gzip
import json
import os
import runpy
import sys
from collections import deque
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from marketdata import depth  # noqa: E402
from marketdata.domain import from_epoch, json_bytes, now, sha256, utc  # noqa: E402

OUT = Path(__file__).resolve().parent


def save(name, data):
    (OUT / name).write_text(
        json.dumps(json.loads(json_bytes(data)), ensure_ascii=False, indent=2) + "\n"
    )


def brief(row):
    return {
        k: row[k]
        for k in (
            "sequence",
            "epoch",
            "kind",
            "timestamp",
            "event_time",
            "received_at",
            "first_update_id",
            "final_update_id",
            "previous_update_id",
            "source_id",
            "source_row",
        )
    }


def metrics(session, row):
    book = session.book
    return {
        "sequence": row["sequence"],
        "kind": row["kind"],
        "received_at": row["received_at"],
        "timestamp": row["timestamp"],
        "bid_levels": len(book.bids),
        "ask_levels": len(book.asks),
        "best_bid": max(book.bids),
        "best_ask": min(book.asks),
        "bid_floor": book.bid_floor,
        "ask_ceiling": book.ask_ceiling,
        "bid_room_usdt": max(book.bids) - book.bid_floor,
        "ask_room_usdt": book.ask_ceiling - min(book.asks),
        "refreshes": session.refreshes,
        "pending_check_sequence": session.pending[1] if session.pending else None,
    }


def snapshot_meta(metadata_by_sequence, row):
    metadata = metadata_by_sequence[row["sequence"]]
    assert utc(metadata["received_at"]) == row["received_at"]
    return {
        **brief(row),
        "request_started_at": metadata["request_started_at"],
        "request_seconds": (
            row["received_at"] - utc(metadata["request_started_at"])
        ).total_seconds(),
    }


def raw_parity(row, payload):
    if row["kind"] == "update":
        envelope = payload[row["source_row"]]
        raw = json.loads(envelope["message"])
        assert (row["epoch"], row["sequence"], row["received_at"]) == (
            envelope["epoch"],
            envelope["sequence"],
            utc(envelope["received_at"]),
        )
        assert (row["first_update_id"], row["final_update_id"], row["previous_update_id"]) == (
            raw["U"],
            raw["u"],
            raw["pu"],
        )
        fields = ("b", "a")
    else:
        raw, fields = payload, ("bids", "asks")
        assert row["final_update_id"] == raw["lastUpdateId"]
        assert row["first_update_id"] is row["previous_update_id"] is None
    assert row["timestamp"] == from_epoch(raw["T"]) and row["event_time"] == from_epoch(raw["E"])
    for side, source in zip(("bids", "asks"), fields, strict=True):
        assert [(r["price"], r["quantity"]) for r in row[side]] == [
            (Decimal(p), Decimal(q)) for p, q in raw[source]
        ]
    return raw


def audit(reader, cid):
    text = depth.collection_record(reader, cid)["report"]
    report = json.loads(text)
    assert report["state"] == "partial" and not report["complete"]
    sessions, epochs, failures, snapshots = {}, {}, [], {}
    sequence, pairs, updates, chain_links, unreconstructed = 0, 0, 0, 0, 0
    latest_raw, last_update = None, None
    trace, sources, inputs = deque(maxlen=650), set(), []
    segments = list(
        reader.db.execute(
            "SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id", (cid,)
        )
    )
    metadata_by_sequence = {}
    for fetched in reader.db.execute(
        "SELECT metadata FROM fetches WHERE json_extract(metadata,'$.collection_id')=?", (cid,)
    ):
        item = json.loads(fetched[0])
        if "sequence" in item and item.get("kind") in ("anchor", "check"):
            metadata_by_sequence[item["sequence"]] = item
    for segment in segments:
        table = reader._revision_table(segment)
        assert table.num_rows == segment["row_count"]
        inputs.append({k: segment[k] for k in ("id", "path", "file_sha", "source_id", "row_count")})
        payloads = {}
        for row in table.to_pylist():
            assert row["sequence"] == sequence
            sequence += 1
            ep = row["epoch"]
            if ep not in sessions:
                assert ep == len(sessions)
                original_epoch = report["epochs"][ep]
                sessions[ep] = depth.new_session(
                    report, original_epoch["reason"], sessions.get(ep - 1)
                )
                epochs[ep] = {
                    "epoch": ep,
                    "reason": original_epoch["reason"],
                    "started_at": original_epoch["started_at"],
                    "ended_at": original_epoch["ended_at"],
                    "end_reason": original_epoch["end_reason"],
                    "previous_epoch_last_update": brief(last_update) if last_update else None,
                    "updates": 0,
                    "unreconstructed": 0,
                }
                if original_epoch["reason"] not in depth.SAME_SOCKET_REASONS:
                    latest_raw = None
                trace.clear()
            active, details = sessions[ep], epochs[ep]
            source = row["source_id"]
            if source not in payloads:
                blob, _ = reader.raw(source)
                payloads[source] = json.loads(
                    gzip.decompress(blob) if row["kind"] == "update" else blob
                )
                sources.add(source)
            raw = raw_parity(row, payloads[source])
            pairs += len(row["bids"]) + len(row["asks"])
            if row["kind"] == "update":
                updates += 1
                details["updates"] += 1
                details.setdefault("first_update", brief(row))
                details["last_update"] = brief(row)
                if latest_raw is not None:
                    assert raw == latest_raw or (
                        raw["u"] > latest_raw["u"] and raw["pu"] == latest_raw["u"]
                    )
                    chain_links += 1
                latest_raw, last_update = raw, row
            else:
                snapshots[row["sequence"]] = snapshot_meta(metadata_by_sequence, row)
                if row["kind"] == "anchor":
                    details["anchor"] = snapshots[row["sequence"]]
                    details["last_refreshed_snapshot"] = row["sequence"]
            if active.error:
                assert row["kind"] == "update" and isinstance(active.failure, depth.SnapshotBehind)
                active.remember(row)
                unreconstructed += 1
                details["unreconstructed"] += 1
                continue
            before = metrics(active, row) if active.book else None
            # Copy the old full known book only for the recorded top-shortfall event.
            # The independent absolute-quantity reduction below must begin before apply().
            original_epoch = report["epochs"][ep]
            is_shortfall = (
                original_epoch["end_reason"] == "incomplete_top"
                and row["kind"] == "update"
                and row["final_update_id"] == original_epoch["final"]["book"]["last_update_id"]
            )
            before_levels = (
                {side: getattr(active.book, side).copy() for side in ("bids", "asks")}
                if is_shortfall
                else None
            )
            refreshes = active.refreshes
            try:
                active.take(row)
            except depth.SyncLost as exc:
                active.error, active.failure = str(exc), exc
                case = {
                    "epoch": ep,
                    "reason": details["reason"],
                    "failure_type": type(exc).__name__,
                    "error": str(exc),
                    "failure": brief(row),
                    "before": before,
                    "anchor": details["anchor"],
                    "last_refreshed_snapshot": snapshots[details["last_refreshed_snapshot"]],
                }
                if isinstance(exc, depth.SnapshotBehind):
                    assert not active.book.synced and raw["U"] > active.book.last
                    unreconstructed += 1
                    details["unreconstructed"] += 1
                    case["anchor_history"] = active.anchor_history
                elif isinstance(exc, depth.CoverageExhausted):
                    # Apply the actual raw absolute quantities independently of Book.apply.
                    # This verifies which known side becomes empty at the failing event.
                    changes = {}
                    for side, field in (("bids", "b"), ("asks", "a")):
                        values = getattr(active.book, side).copy()
                        for price, quantity in raw[field]:
                            price, quantity = Decimal(price), Decimal(quantity)
                            if quantity:
                                values[price] = quantity
                            else:
                                values.pop(price, None)
                        inside = (
                            (lambda p: p >= active.book.bid_floor)
                            if side == "bids"
                            else (lambda p: p <= active.book.ask_ceiling)
                        )
                        known = {p: q for p, q in values.items() if inside(p)}
                        changes[side] = {
                            "before": len(getattr(active.book, side)),
                            "after_in_range": len(known),
                            "outside_positive": sum(not inside(p) for p in values),
                        }
                    assert any(v["after_in_range"] == 0 for v in changes.values())
                    case["independent_raw_change"] = changes
                    case["seconds_since_last_refresh_response"] = (
                        row["received_at"] - utc(case["last_refreshed_snapshot"]["received_at"])
                    ).total_seconds()
                    case["recent_checks"] = active.checks[-4:]
                    path = OUT / f"{cid}-epoch-{ep}-trace.csv"
                    with path.open("w") as stream:
                        writer = csv.DictWriter(stream, fieldnames=list(trace[0]))
                        writer.writeheader()
                        writer.writerows(trace)
                    case["trace"] = path.name
                    exhausted = next(
                        side[:-1] for side, v in changes.items() if not v["after_in_range"]
                    )
                    case["thresholds_in_current_range"] = {}
                    for limit in (500, 250, 100, 20):
                        crossing = next(
                            (
                                t
                                for t in trace
                                if t["refreshes"] == active.refreshes
                                and t[f"{exhausted}_levels"] < limit
                            ),
                            None,
                        )
                        case["thresholds_in_current_range"][str(limit)] = (
                            None
                            if crossing is None
                            else {
                                "first_observed_below": crossing,
                                "seconds_before_failure": (
                                    row["received_at"] - crossing["received_at"]
                                ).total_seconds(),
                            }
                        )
                else:
                    raise
                failures.append(case)
                continue
            if active.refreshes > refreshes:
                details["last_refreshed_snapshot"] = active.checks[-1]["snapshot_sequence"]
            if (
                active.book.synced
                and min(len(active.book.bids), len(active.book.asks)) < report["top"]
            ):
                assert is_shortfall and before_levels is not None
                changes = {}
                for side, field in (("bids", "b"), ("asks", "a")):
                    values = before_levels[side].copy()
                    for price, quantity in raw[field]:
                        price, quantity = Decimal(price), Decimal(quantity)
                        if quantity:
                            values[price] = quantity
                        else:
                            values.pop(price, None)
                    inside = (
                        (lambda p: p >= active.book.bid_floor)
                        if side == "bids"
                        else (lambda p: p <= active.book.ask_ceiling)
                    )
                    known = {p: q for p, q in values.items() if inside(p)}
                    assert known == getattr(active.book, side)
                    changes[side] = {
                        "before": len(before_levels[side]),
                        "after_in_range": len(known),
                    }
                case = {
                    "epoch": ep,
                    "reason": details["reason"],
                    "failure_type": "IncompleteTop",
                    "error": None,
                    "failure": brief(row),
                    "before": before,
                    "after": metrics(active, row),
                    "anchor": details["anchor"],
                    "last_refreshed_snapshot": snapshots[details["last_refreshed_snapshot"]],
                    "independent_raw_change": changes,
                    "live_end_reason": original_epoch["end_reason"],
                }
                case["seconds_since_last_refresh_response"] = (
                    row["received_at"] - utc(case["last_refreshed_snapshot"]["received_at"])
                ).total_seconds()
                path = OUT / f"{cid}-epoch-{ep}-trace.csv"
                with path.open("w") as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(trace[0]))
                    writer.writeheader()
                    writer.writerows(trace)
                case["trace"] = path.name
                failures.append(case)
            if active.book.synced and "first_synchronized_row" not in details:
                details["first_synchronized_row"] = brief(row)
            if active.book and active.book.synced:
                trace.append(metrics(active, row))
        if len(inputs) % 150 == 0:
            print(json.dumps({"collection": cid, "rows_checked": sequence}), flush=True)
    summaries = {ep: s.summary() for ep, s in sessions.items()}
    assert json_bytes(summaries) == json_bytes({e["epoch"]: e["final"] for e in report["epochs"]})
    assert updates == report["messages"] == report["persisted_messages"]
    assert unreconstructed == report["unreconstructed_messages"]
    bootstraps = {}
    for f in reader.db.execute(
        "SELECT f.artifact_id,f.metadata FROM fetches f JOIN artifacts a ON a.id=f.artifact_id "
        "WHERE a.kind='depth_bootstrap' AND json_extract(f.metadata,'$.collection_id')=?",
        (cid,),
    ):
        metadata = json.loads(f["metadata"])
        payload = json.loads(gzip.decompress(reader.raw(f["artifact_id"])[0]))
        first = json.loads(payload[0]["message"])
        ep = metadata["epoch"]
        update = epochs[ep]["first_update"]
        assert (
            update["first_update_id"],
            update["final_update_id"],
            update["previous_update_id"],
        ) == (first["U"], first["u"], first["pu"])
        assert utc(update["received_at"]) == utc(payload[0]["received_at"])
        assert utc(payload[0]["received_at"]) < utc(epochs[ep]["anchor"]["request_started_at"])
        bootstraps[ep] = {
            "source_id": f["artifact_id"],
            "messages": len(payload),
            "first_received_at": payload[0]["received_at"],
            "matches_first_journal_update": True,
            "received_before_rest_request": True,
        }
    for case in failures:
        ep = case["epoch"]
        case["epoch_details"] = epochs[ep]
        case["next_epoch"] = epochs.get(ep + 1)
        if case["failure_type"] == "SnapshotBehind":
            case["bootstrap"] = bootstraps.get(ep)
            if case["bootstrap"] is None:
                assert epochs[ep]["reason"] in depth.SAME_SOCKET_REASONS
                case["bootstrap_note"] = (
                    "Same physical socket; no new connection bootstrap is expected"
                )
            case["first_update_minus_snapshot_transaction_seconds"] = (
                utc(epochs[ep]["first_update"]["timestamp"]) - utc(case["anchor"]["timestamp"])
            ).total_seconds()
            case["unsynchronized_receipt_span_seconds"] = (
                utc(epochs[ep]["last_update"]["received_at"])
                - utc(epochs[ep]["first_update"]["received_at"])
            ).total_seconds()
            recovered = epochs[ep + 1]["first_synchronized_row"]
            case["first_receipt_to_resynchronized_receipt_seconds"] = (
                utc(recovered["received_at"]) - utc(epochs[ep]["first_update"]["received_at"])
            ).total_seconds()
        for later in range(ep + 1, len(epochs)):
            if epochs[later]["reason"] not in depth.SAME_SOCKET_REASONS:
                break
            recovered = epochs[later].get("first_synchronized_row")
            if recovered:
                case["same_socket_recovered_epoch"] = later
                case["same_socket_first_synchronized_row"] = recovered
                case["failure_receipt_to_recovered_row_seconds"] = (
                    utc(recovered["received_at"]) - utc(case["failure"]["received_at"])
                ).total_seconds()
                break
    assert len(failures) == report["sync_losses"]
    assert (
        sum(c["failure_type"] == "IncompleteTop" for c in failures)
        == report["coverage_top_shortfalls"]
    )
    result = {
        "collection_id": cid,
        "original_report_sha256": sha256(text.encode()),
        "state": report["state"],
        "valid": False,
        "continuous": False,
        "replay_matches_live": True,
        "rows_checked": sequence,
        "price_quantity_pairs_checked": pairs,
        "raw_artifacts_checked": len(sources),
        "raw_to_canonical_equal": True,
        "updates_received_and_persisted": updates,
        "unreconstructed_messages": unreconstructed,
        "same_socket_chain_links_checked": chain_links,
        "same_socket_chain_errors": 0,
        "verification_checks": sum(len(s.checks) for s in sessions.values()),
        "verification_equal": sum(bool(c["equal"]) for s in sessions.values() for c in s.checks),
        "failures": failures,
    }
    save(f"{cid}.json", result)
    save(f"{cid}-inputs.json", inputs)
    print(
        json.dumps(
            {
                "collection": cid,
                "complete": True,
                "rows": sequence,
                "pairs": pairs,
                "failures": len(failures),
            }
        ),
        flush=True,
    )
    return result


def main():
    os.nice(10)
    selection = json.loads((OUT / "audit.json").read_bytes())
    ids = selection["new_partial_collection_ids"]
    assert ids == ["29549c4ed9a24cd19038a27e46dfe0c3", "fd0595a1dbca496686583f024ab837ac"]
    reader_type = runpy.run_path(
        str(ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
    )["ReadOnlyStore"]
    reader = reader_type(ROOT)
    result = {"started_at": now(), "selection": ids, "complete": False, "cases": []}
    save("new-failures-verification.json", result)
    try:
        for cid in ids:
            result["cases"].append(audit(reader, cid))
            save("new-failures-verification.json", result)
    finally:
        reader.close()
    result.update(complete=True, finished_at=now())
    save("new-failures-verification.json", result)


if __name__ == "__main__":
    main()
