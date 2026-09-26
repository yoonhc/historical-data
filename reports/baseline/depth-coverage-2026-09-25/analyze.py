"""Read-only replay of the three coverage failures in the fixed 24-hour audit.

Run from the repository root with .venv/bin/python and this script's path.
Writes only this report directory; never opens the catalog for writing.
"""

import csv
import gzip
import json
import sqlite3
import subprocess
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from marketdata import depth
from marketdata.domain import json_bytes, now, sha256, utc
from marketdata.fileio import read_file

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent
WINDOW = ROOT / "reports/baseline/observation-2026-09-25/collections-window.json"
ERROR = "Known snapshot price range exhausted"


def save(name, value):
    (OUT / name).write_text(
        json.dumps(json.loads(json_bytes(value)), ensure_ascii=False, indent=2) + "\n"
    )


def metrics(session, row):
    book = session.book
    bid, ask = max(book.bids), min(book.asks)
    return {
        "sequence": row["sequence"],
        "kind": row["kind"],
        "timestamp": row["timestamp"],
        "received_at": row["received_at"],
        "last_update_id": book.last,
        "bid_levels": len(book.bids),
        "ask_levels": len(book.asks),
        "best_bid": bid,
        "best_ask": ask,
        "bid_floor": book.bid_floor,
        "ask_ceiling": book.ask_ceiling,
        "bid_room_usdt": bid - book.bid_floor,
        "ask_room_usdt": book.ask_ceiling - ask,
        "bid_room_bps": (bid - book.bid_floor) / bid * 10000,
        "ask_room_bps": (book.ask_ceiling - ask) / ask * 10000,
        "bid_threshold": min(max(500, session.top * 10), book.snapshot_bid_levels // 2),
        "ask_threshold": min(max(500, session.top * 10), book.snapshot_ask_levels // 2),
        "pending_check_sequence": session.pending[1] if session.pending else None,
        "refreshes": session.refreshes,
    }


def load_rows(db, report, epoch):
    # SQL times are exchange times; widen both ends and verify exact epoch contents
    # using its anchor, contiguous sequence and matching recorded final state.
    start = utc(epoch["started_at"]) - timedelta(seconds=5)
    end = utc(epoch["ended_at"]) + timedelta(seconds=5)
    segments = db.execute(
        "SELECT * FROM event_segments WHERE collection_id=? AND first_time<=? "
        "AND last_time>=? ORDER BY first_id",
        (report["id"], end.isoformat(), start.isoformat()),
    ).fetchall()
    rows, sources, evidence = [], {}, []
    for segment in segments:
        blob = read_file(ROOT, db, segment["path"])
        assert sha256(blob) == segment["file_sha"]
        table = pq.read_table(pa.BufferReader(blob))
        assert table.num_rows == segment["row_count"]
        selected = [r for r in table.to_pylist() if r["epoch"] == epoch["epoch"]]
        if not selected:
            continue
        evidence.append(
            {k: segment[k] for k in ("id", "path", "file_sha", "source_id", "row_count")}
        )
        for row in selected:
            source_id = row["source_id"]
            if source_id not in sources:
                artifact = db.execute("SELECT * FROM artifacts WHERE id=?", (source_id,)).fetchone()
                raw = read_file(ROOT, db, artifact["path"])
                assert sha256(raw) == source_id
                sources[source_id] = json.loads(
                    gzip.decompress(raw) if artifact["kind"] == "depth_websocket" else raw
                )
            payload = sources[source_id]
            if row["kind"] == "update":
                message = payload[row["source_row"]]
                expected = depth.parse(
                    json.loads(message["message"]),
                    "update",
                    message["epoch"],
                    message["sequence"],
                    source_id,
                    row["source_row"],
                    message["received_at"],
                )
            else:
                expected = depth.parse(
                    payload,
                    row["kind"],
                    row["epoch"],
                    row["sequence"],
                    source_id,
                    0,
                    row["received_at"],
                )
            assert json_bytes(row) == json_bytes(expected)
            rows.append(row)
    assert rows and rows[0]["kind"] == "anchor"
    assert [r["sequence"] for r in rows] == list(
        range(rows[0]["sequence"], rows[-1]["sequence"] + 1)
    )
    return rows, evidence, len(sources)


def delayed_check_scenario(rows, index, session):
    """Use only receipts preceding this actual check, with the existing audit's caps.

    Future rows are applied only after aligning at the check's actual receipt.
    This is an offline scenario, not a change to the recorded live result.
    """
    row = rows[index]
    history = [r for r in rows[:index] if r["kind"] == "update"][-128:]
    level_count = sum(len(r["bids"]) + len(r["asks"]) for r in history)
    while history and level_count > 50000:
        old = history.pop(0)
        level_count -= len(old["bids"]) + len(old["asks"])
    result = {
        "snapshot_sequence": row["sequence"],
        "history_events": len(history),
        "history_price_quantity_pairs": level_count,
        "history_limit_events": 128,
        "history_limit_price_quantity_pairs": 50000,
        "snapshot_available_at": row["received_at"],
        "aligned_top_equal": False,
        "survives_recorded_failure": False,
    }
    try:
        candidate = depth.Book(row)
        applied = []
        for update in history:
            assert update["received_at"] <= row["received_at"]
            if candidate.apply(update) == "applied":
                applied.append(update["sequence"])
        a, b = session.book.view(session.top), candidate.view(session.top)
        result["aligned_top_equal"] = bool(
            candidate.synced
            and a["last_update_id"] == b["last_update_id"]
            and a["top_complete"]
            and b["top_complete"]
            and a["bids"] == b["bids"]
            and a["asks"] == b["asks"]
        )
        result["past_updates_applied"] = applied
        result["aligned_book"] = b
        if result["aligned_top_equal"]:
            future = rows[index + 1 :]
            for update in future:
                assert update["kind"] == "update"
                assert update["received_at"] >= row["received_at"]
                candidate.apply(update)
            result["subsequent_updates_applied"] = len(future)
            result["survives_recorded_failure"] = True
            result["book_at_recorded_failure"] = candidate.view(session.top)
    except depth.SyncLost as exc:
        result["reason"] = str(exc)
    return result


def analyze(db, report, epoch):
    rows, inputs, source_count = load_rows(db, report, epoch)
    session = depth.Session(report["top"], refresh_from_checks=report["refresh_from_checks"])
    trace, snapshots, delayed_scenarios = [], [], []
    for index, row in enumerate(rows):
        before = metrics(session, row) if session.book else None
        try:
            session.take(row)
        except depth.CoverageExhausted as exc:
            assert index == len(rows) - 1
            session.error = str(exc)
            failure = row
            changes = {}
            for side, inside in (
                ("bids", lambda p: p >= session.book.bid_floor),
                ("asks", lambda p: p <= session.book.ask_ceiling),
            ):
                previous = getattr(session.book, side)
                hypothetical = previous.copy()
                removed, outside_positive = [], []
                for level in row[side]:
                    price, quantity = level["price"], level["quantity"]
                    if not inside(price):
                        if quantity:
                            outside_positive.append(price)
                    elif quantity:
                        hypothetical[price] = quantity
                    else:
                        if price in hypothetical:
                            removed.append(price)
                        hypothetical.pop(price, None)
                changes[side] = {
                    "before_levels": len(previous),
                    "after_levels": len(hypothetical),
                    "existing_levels_removed": len(removed),
                    "outside_positive_levels": len(outside_positive),
                    "outside_positive_price_min": min(outside_positive, default=None),
                    "outside_positive_price_max": max(outside_positive, default=None),
                }
            break
        trace.append(metrics(session, row))
        if row["kind"] == "check" and row["final_update_id"] < session.book.last:
            delayed_scenarios.append(delayed_check_scenario(rows, index, session))
        if row["kind"] != "update":
            fetches = db.execute(
                "SELECT metadata FROM fetches WHERE artifact_id=? ORDER BY id", (row["source_id"],)
            ).fetchall()
            metadata = next(
                json.loads(f["metadata"])
                for f in fetches
                if json.loads(f["metadata"]).get("collection_id") == report["id"]
                and json.loads(f["metadata"]).get("sequence") == row["sequence"]
            )
            assert row["received_at"] == utc(metadata["received_at"])
            snapshots.append(
                {
                    "sequence": row["sequence"],
                    "kind": row["kind"],
                    "source_id": row["source_id"],
                    "timestamp": row["timestamp"],
                    "received_at": row["received_at"],
                    "request_started_at": utc(metadata["request_started_at"]),
                    "request_to_response_seconds": (
                        row["received_at"] - utc(metadata["request_started_at"])
                    ).total_seconds(),
                    "snapshot_last_update_id": row["final_update_id"],
                    "before": before,
                }
            )
    else:
        raise AssertionError("Expected coverage exhaustion did not reproduce")
    assert json_bytes(session.summary()) == json_bytes(epoch["final"])
    for snap in snapshots:
        snap["check_result"] = next(
            (c for c in session.checks if c["snapshot_sequence"] == snap["sequence"]), None
        )
    exhausted = next(s[:-1] for s, c in changes.items() if c["after_levels"] == 0)
    refreshed = [s for s in snapshots if (s["check_result"] or {}).get("refreshed")]
    last_refresh = refreshed[-1] if refreshed else snapshots[0]
    # Restrict warning analysis to the active last-refreshed range. Earlier ranges
    # can legitimately have different floors/ceilings and reset their level count.
    active = [p for p in trace if p["refreshes"] == session.refreshes and p["kind"] == "update"]
    crossings = {}
    for limit in (500, 250, 100, 20):
        hit = next((p for p in active if p[f"{exhausted}_levels"] < limit), None)
        crossings[str(limit)] = (
            None
            if hit is None
            else {
                "first_below": hit,
                "seconds_to_failure_received": (
                    failure["received_at"] - hit["received_at"]
                ).total_seconds(),
                "seconds_to_failure_exchange": (
                    failure["timestamp"] - hit["timestamp"]
                ).total_seconds(),
            }
        )
    recent = [
        p for p in trace if p["received_at"] >= failure["received_at"] - timedelta(seconds=60)
    ]
    trace_name = f"{report['id']}-epoch-{epoch['epoch']}-last-60s.csv"
    with (OUT / trace_name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=recent[0].keys())
        writer.writeheader()
        writer.writerows(recent)
    following = next((e for e in report["epochs"] if e["epoch"] == epoch["epoch"] + 1), None)
    result = {
        "collection_id": report["id"],
        "epoch": epoch["epoch"],
        "started_at": epoch["started_at"],
        "ended_at": epoch["ended_at"],
        "collection_state": report["state"],
        "epoch_error": session.error,
        "rows_replayed_and_raw_equal": len(rows),
        "raw_artifacts_verified": source_count,
        "replay_matches_live": True,
        "checks": session.checks,
        "failure": {
            k: failure[k]
            for k in (
                "sequence",
                "timestamp",
                "event_time",
                "received_at",
                "source_id",
                "source_row",
                "first_update_id",
                "final_update_id",
                "previous_update_id",
            )
        },
        "chain_contiguous_at_failure": failure["previous_update_id"] == session.book.last,
        "before_failure": trace[-1],
        "failure_changes": changes,
        "exhausted_side": exhausted,
        "last_successful_refresh": last_refresh,
        "recent_snapshots": snapshots[-5:],
        "seconds_since_last_refresh_response": (
            failure["received_at"] - last_refresh["received_at"]
        ).total_seconds(),
        "seconds_since_last_check_response": (
            failure["received_at"] - snapshots[-1]["received_at"]
        ).total_seconds(),
        "level_crossings_since_last_refresh": crossings,
        "delayed_check_offline_scenarios": delayed_scenarios,
        "trace": trace_name,
        "following_epoch": None
        if following is None
        else {
            k: following.get(k) for k in ("epoch", "reason", "started_at", "ended_at", "end_reason")
        },
        "source_segments": inputs,
    }
    print(
        json_bytes(
            {
                k: result[k]
                for k in (
                    "collection_id",
                    "epoch",
                    "rows_replayed_and_raw_equal",
                    "replay_matches_live",
                    "exhausted_side",
                    "seconds_since_last_check_response",
                )
            }
        ).decode(),
        flush=True,
    )
    return result


def main():
    db = sqlite3.connect((ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        targets = [
            (c["collection_id"], e["epoch"])
            for c in json.loads(WINDOW.read_text())
            for e in c.get("epochs", [])
            if e.get("error") == ERROR
        ]
        assert len(targets) == 3
        cases = []
        for identity, epoch_id in targets:
            record = db.execute(
                "SELECT * FROM event_collections WHERE id=?", (identity,)
            ).fetchone()
            assert record["state"] != "running"
            report = json.loads(record["report"])
            epoch = next(e for e in report["epochs"] if e["epoch"] == epoch_id)
            cases.append(analyze(db, report, epoch))
        source_paths = [
            Path(__file__).relative_to(ROOT),
            WINDOW.relative_to(ROOT),
            Path("packages/marketdata/src/marketdata/depth.py"),
            Path("packages/marketdata/src/marketdata/depth_stream.py"),
        ]
        save(
            "analysis.json",
            {
                "checked_at": now(),
                "git_head": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True
                ).strip(),
                "source_sha256": {str(p): sha256((ROOT / p).read_bytes()) for p in source_paths},
                "scope": "Only the three failed epochs; all selected rows compared with raw payloads.",
                "timing_limit": "Application recv/REST response wall-clock times; monotonic scheduler gates were not recorded.",
                "counterfactual_limit": "No hypothetical REST snapshots or lookahead reconstruction used.",
                "cases": cases,
            },
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
