"""Read-only 24-hour depth report audit and explicit complete-session samples."""

import hashlib
import json
import runpy
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marketdata import depth
from marketdata.domain import json_bytes

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from scripts.verify_depth_capture import verify  # noqa: E402

ReadOnlyStore = runpy.run_path(
    str(ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
)["ReadOnlyStore"]


def save(name, value):
    (OUT / name).write_text(
        json.dumps(json.loads(json_bytes(value)), ensure_ascii=False, indent=2) + "\n"
    )


def main():
    activation = json.loads((OUT.parent / "depth-retry-2026-09-29/activation.json").read_bytes())
    release = json.loads((OUT.parent / "depth-retry-2026-09-29/release.json").read_bytes())
    reader = ReadOnlyStore(ROOT)
    try:
        reader.db.execute("BEGIN")
        first = reader.db.execute(
            "SELECT started_at FROM event_collections WHERE id=?",
            (activation["new_collection_id"],),
        ).fetchone()[0]
        start = datetime.fromisoformat(first)
        cutoff = start + timedelta(days=1)
        rows = reader.db.execute(
            "SELECT * FROM event_collections WHERE dataset='book_depth' AND kind='websocket' "
            "AND started_at>=? AND started_at<? ORDER BY started_at",
            (first, cutoff.isoformat()),
        ).fetchall()
        reports = [json.loads(row["report"]) for row in rows]
        save("session-reports.json", reports)
        index, epoch_boundaries, previous = [], [], None
        for row, report in zip(rows, reports, strict=True):
            segments = reader.db.execute(
                "SELECT COUNT(*),SUM(row_count),MIN(first_id),MAX(last_id) "
                "FROM event_segments WHERE collection_id=?",
                (row["id"],),
            ).fetchone()
            checks = [c for e in report["epochs"] for c in e["final"]["checks"]]
            expected_rows = report["persisted_messages"] + report["snapshots"]
            item = {
                **{
                    k: report.get(k)
                    for k in (
                        "id",
                        "state",
                        "started_at",
                        "updated_at",
                        "messages",
                        "persisted_messages",
                        "snapshots",
                        "connections",
                        "connection_errors",
                        "sync_losses",
                        "coverage_resets",
                        "lagged_snapshots",
                        "unreconstructed_messages",
                        "lagged_retry_requests",
                        "lagged_retry_policy",
                        "lagged_retry_delays_seconds",
                        "lagged_retry_budget",
                    )
                },
                "segments": segments[0],
                "canonical_rows": segments[1],
                "first_sequence": segments[2],
                "last_sequence": segments[3],
                "catalog_rows_match_report": segments[1] == expected_rows,
                "messages_equal_persisted": report["messages"] == report["persisted_messages"],
                "verification_checks": len(checks),
                "verification_equal": sum(c["equal"] is True for c in checks),
                "verification_uncomparable": sum(not c["comparable"] for c in checks),
                "verification_pending": sum(
                    e["final"]["pending_check_sequence"] is not None for e in report["epochs"]
                ),
                "all_epoch_books_synced": all(
                    e["final"]["book"]["synced"] for e in report["epochs"]
                ),
                "epoch_errors": [
                    e["final"]["error"] for e in report["epochs"] if e["final"]["error"]
                ],
            }
            index.append(item)
            for e in report["epochs"]:
                current = {
                    "collection_id": report["id"],
                    "epoch": e["epoch"],
                    "started_at": e["started_at"],
                    "ended_at": e.get("ended_at"),
                    "reason": e["reason"],
                    "end_reason": e.get("end_reason"),
                }
                if previous:
                    epoch_boundaries.append(
                        {
                            "from": previous,
                            "to": current,
                            "seconds_between_epoch_end_and_start": (
                                datetime.fromisoformat(current["started_at"])
                                - datetime.fromisoformat(previous["ended_at"])
                            ).total_seconds(),
                            "scope": "reported epoch boundaries; not wire-arrival timestamps or repaired order books",
                        }
                    )
                previous = current
        agg = reader.db.execute(
            "SELECT report,state FROM event_collections WHERE dataset='agg_trades' AND kind='websocket' "
            "AND started_at>=? AND started_at<? ORDER BY started_at",
            (first, cutoff.isoformat()),
        ).fetchall()
        agg_reports = [json.loads(r["report"]) for r in agg]
        reader.db.execute("COMMIT")
        save("session-index.json", index)
        save("epoch-boundaries.json", epoch_boundaries)
        save("aggtrade-reports.json", agg_reports)
        assert len(index) == 24, "Unexpected session count: inspect before claiming a full day"
        assert all(i["state"] == "complete" for i in index)
        assert all(i["catalog_rows_match_report"] and i["messages_equal_persisted"] for i in index)
        assert all(i["lagged_retry_policy"] == "same_socket_backoff_v1" for i in index)
        sums = {
            k: sum(i[k] for i in index)
            for k in (
                "messages",
                "persisted_messages",
                "snapshots",
                "canonical_rows",
                "connections",
                "connection_errors",
                "sync_losses",
                "coverage_resets",
                "lagged_snapshots",
                "unreconstructed_messages",
                "lagged_retry_requests",
                "verification_checks",
                "verification_equal",
                "verification_uncomparable",
                "verification_pending",
            )
        }
        daily = json.loads((ROOT / "state/maintenance-daily-latest.json").read_bytes())
        daily_start = datetime.fromisoformat(daily["started_at"])
        daily_end = datetime.fromisoformat(daily["finished_at"])
        during_backup = [
            i["id"]
            for i in index
            if datetime.fromisoformat(i["started_at"]) <= daily_start
            and datetime.fromisoformat(i["updated_at"]) >= daily_end
        ]
        assert len(during_backup) == 1
        samples = list(dict.fromkeys(during_backup + [index[-1]["id"]]))
        protected = {
            p: hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
            for p, h in release["protected_input_hashes_unchanged"].items()
        }
        source = ROOT / "packages/marketdata/src/marketdata/depth_stream.py"
        summary = {
            "recorded_at": datetime.now(timezone.utc),
            "requested_window_start": start,
            "requested_window_end_exclusive": cutoff,
            "selection": "whole completed sessions starting within the first 24 hours of activation; last session retained to its actual end",
            "actual_last_session_end": index[-1]["updated_at"],
            "states": dict(Counter(i["state"] for i in index)),
            "totals": sums,
            "all_epoch_books_synced": all(i["all_epoch_books_synced"] for i in index),
            "epoch_errors": [e for i in index for e in i["epoch_errors"]],
            "catalog_rows_all_match_reports": all(i["catalog_rows_match_report"] for i in index),
            "sample_collection_ids": samples,
            "sample_selection": "one complete session covering today's daily work, and the latest complete session",
            "previous_first_session_proof": "../depth-retry-2026-09-29/operating-check.json",
            "source_matches_deployment": hashlib.sha256(source.read_bytes()).hexdigest()
            == activation["source_sha256"],
            "protected_hash_matches": protected,
            "aggtrade_report_states": dict(Counter(r["state"] for r in agg)),
            "aggtrade_report_connection_errors": sum(
                r.get("connection_errors", 0) for r in agg_reports
            ),
            "aggtrade_report_unrepaired_gaps": sum(
                r.get("unrepaired_gaps", 0) for r in agg_reports
            ),
            "raw_parity_scope": "the explicitly selected samples only; no claim of complete raw parity over all 24 sessions",
            "retry_observed": bool(sums["lagged_retry_requests"]),
            "sample_verification": "in_progress",
        }
        save("audit.json", summary)
        print(json.dumps({"totals": sums, "samples": samples}), flush=True)
        verified = []
        for identity in samples:
            print("replay " + identity, flush=True)
            replayed = depth.replay(reader, identity)
            save(identity + "-replay.json", replayed)
            print("raw parity " + identity, flush=True)
            parity = verify(reader, identity, replayed=replayed)
            save(identity + "-parity.json", parity)
            assert replayed["valid"] and replayed["replay_matches_live"]
            assert parity["raw_to_canonical_equal"] and parity["all_snapshot_checks_verified"]
            verified.append(parity)
            print(json.dumps(parity, default=str), flush=True)
        summary.update(
            sample_verification="passed", samples=verified, finished_at=datetime.now(timezone.utc)
        )
        save("audit.json", summary)
    finally:
        reader.db.close()
        reader._files_db.close()


if __name__ == "__main__":
    main()
