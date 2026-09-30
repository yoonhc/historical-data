"""Read-only audit of three closed depth sessions, using a fixed daily checkpoint.

Run from the repository root. Does not write the live or backup catalogs, start
collectors, change quality records, or relax any replay policy. Only this report
directory receives output. Raw payloads remain in the checkpoint.
"""

import csv
import gzip
import json
import os
import sys
from collections import deque
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from marketdata import depth  # noqa: E402
from marketdata.domain import from_epoch, json_bytes, now, sha256, utc  # noqa: E402

from scripts.restic_trial import ArchiveReader, sample_streams  # noqa: E402

OUT = Path(__file__).resolve().parent
ARCHIVE = Path(
    "/mnt/backup/marketdata-historical/automatic/2026-09-29/0d84de71d71643a38d0250e061cc68e8"
)
COLLECTIONS = [
    "d59f0bdbd3db4c1ba6ba54f0b1bee4d8",
    "884a5a24e6e8431ea04f71d3950a731d",
    "d915979fa3f54359bc53492103b89932",
]


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


def snapshot_meta(reader, cid, row):
    matches = [
        json.loads(f[0])
        for f in reader.db.execute(
            "SELECT metadata FROM fetches WHERE artifact_id=?", (row["source_id"],)
        )
    ]
    metadata = next(
        m for m in matches if m.get("collection_id") == cid and m.get("sequence") == row["sequence"]
    )
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
                snapshots[row["sequence"]] = snapshot_meta(reader, cid, row)
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
            case["bootstrap"] = bootstraps[ep]
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
    started = now()
    manifest = (ARCHIVE / "backup.json").read_bytes()
    assert sha256(manifest) == "2717547ce2cf187252a2ee8ab93e70ec7d7d16019f6798af420889f02996dd1a"
    assert json.loads(manifest)["source_root"] == str(ROOT)
    files = (
        "packages/marketdata/src/marketdata/depth.py",
        "packages/marketdata/src/marketdata/depth_stream.py",
        "scripts/verify_depth_capture.py",
    )
    source_hashes = {p: sha256((ROOT / p).read_bytes()) for p in files}
    before_streams = sample_streams(ROOT)
    reader = ArchiveReader(ARCHIVE)
    try:
        cases = [audit(reader, cid) for cid in COLLECTIONS]
    finally:
        reader.close()
    assert sha256((ARCHIVE / "backup.json").read_bytes()) == sha256(manifest)
    assert source_hashes == {p: sha256((ROOT / p).read_bytes()) for p in files}
    result = {
        "started_at": started,
        "finished_at": now(),
        "archive": str(ARCHIVE),
        "backup_id": json.loads(manifest)["backup_id"],
        "manifest_sha256": sha256(manifest),
        "source_hashes": source_hashes,
        "source_code_unchanged": True,
        "streams_before": before_streams,
        "streams_after": sample_streams(ROOT),
        "cases": cases,
        "report_hashes": {c["collection_id"]: c["original_report_sha256"] for c in cases},
        "collection_reports_unchanged": True,
        "services_restarted": False,
        "scope": "three closed sessions; per-socket chain checks exclude reconnect gaps; raw and canonical parity plus policy-preserving replay; independent raw reducer only at two coverage failures",
    }
    save("audit.json", result)
    print(
        json.dumps(
            {
                "finished": True,
                "sessions": len(cases),
                "rows": sum(c["rows_checked"] for c in cases),
                "pairs": sum(c["price_quantity_pairs_checked"] for c in cases),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
