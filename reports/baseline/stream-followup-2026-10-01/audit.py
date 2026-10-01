"""Read-only report/catalog audit since the preceding completed stream samples."""

import hashlib
import json
import runpy
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from marketdata.domain import json_bytes, utc

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def save(name, value):
    (OUT / name).write_text(
        json.dumps(json.loads(json_bytes(value)), ensure_ascii=False, indent=2) + "\n"
    )


def main():
    read_only_store = runpy.run_path(
        str(ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
    )["ReadOnlyStore"]
    previous = OUT.parent / "depth-observation-2026-09-30"
    starts = {
        "book_depth": json.loads((previous / "audit.json").read_bytes())["actual_last_session_end"],
        "agg_trades": max(
            r["updated_at"] for r in json.loads((previous / "aggtrade-reports.json").read_bytes())
        ),
    }
    cutoff = datetime.now(timezone.utc).isoformat()
    reader = read_only_store(ROOT)
    summaries = {}
    try:
        reader.db.execute("BEGIN")
        for dataset, start in starts.items():
            records = reader.db.execute(
                "SELECT * FROM event_collections WHERE dataset=? AND kind='websocket' "
                "AND started_at>=? AND started_at<? ORDER BY started_at",
                (dataset, start, cutoff),
            ).fetchall()
            reports = [json.loads(r["report"]) for r in records]
            save(dataset + "-reports.json", reports)
            index = []
            for r in reports:
                if r["state"] == "running":
                    continue
                count, rows = reader.db.execute(
                    "SELECT COUNT(*),COALESCE(SUM(row_count),0) FROM event_segments "
                    "WHERE collection_id=?",
                    (r["id"],),
                ).fetchone()
                checks = [c for e in r.get("epochs", []) for c in e["final"]["checks"]]
                item = {
                    k: r.get(k, 0)
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
                        "detected_gaps",
                        "repaired_gaps",
                        "unrepaired_gaps",
                    )
                }
                item.update(
                    segments=count,
                    canonical_rows=rows,
                    catalog_rows_match_report=rows
                    == r["persisted_messages"] + r.get("snapshots", 0),
                    messages_equal_persisted=r["messages"] == r["persisted_messages"],
                    verification_checks=len(checks),
                    verification_equal=sum(c["equal"] is True for c in checks),
                    verification_uncomparable=sum(not c["comparable"] for c in checks),
                    comparable_mismatches=sum(c["comparable"] and not c["equal"] for c in checks),
                    verification_pending=sum(
                        e["final"]["pending_check_sequence"] is not None
                        for e in r.get("epochs", [])
                    ),
                )
                assert item["catalog_rows_match_report"] and item["messages_equal_persisted"]
                index.append(item)
            save(dataset + "-index.json", index)
            keys = [k for k, v in index[0].items() if type(v) is int]
            summaries[dataset] = {
                "start_inclusive": start,
                "cutoff_exclusive": cutoff,
                "selection": "whole closed sessions starting after the last previously audited session; running session excluded from totals",
                "states": dict(Counter(r["state"] for r in index)),
                "running_excluded": [r["id"] for r in reports if r["state"] == "running"],
                "actual_last_session_end": max(r["updated_at"] for r in index),
                "totals": {k: sum(r[k] for r in index) for k in keys},
                "all_catalog_counts_match": True,
                "report_errors": [
                    {
                        "collection_id": r["id"],
                        "errors": r.get("errors", r.get("recent_errors", [])),
                    }
                    for r in reports
                    if r.get("errors", r.get("recent_errors", []))
                ],
            }
        reader.db.execute("COMMIT")
    finally:
        reader.db.close()
        reader._files_db.close()
    release = json.loads((OUT.parent / "depth-retry-2026-09-29/release.json").read_bytes())
    activation = json.loads((OUT.parent / "depth-retry-2026-09-29/activation.json").read_bytes())
    protected = {
        p: hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
        for p, h in release["protected_input_hashes_unchanged"].items()
    }
    source_matches = (
        hashlib.sha256(
            (ROOT / "packages/marketdata/src/marketdata/depth_stream.py").read_bytes()
        ).hexdigest()
        == activation["source_sha256"]
    )
    assert all(protected.values()) and source_matches
    current = {}
    for name in ["depth", "aggtrade"]:
        report = json.loads((ROOT / f"state/{name}-latest.json").read_bytes())
        checked = datetime.now(timezone.utc)
        current[name] = {
            k: report.get(k)
            for k in (
                "id",
                "state",
                "started_at",
                "updated_at",
                "last_received_at",
                "synchronized",
                "messages",
                "persisted_messages",
                "connection_errors",
                "sync_losses",
                "unreconstructed_messages",
                "unrepaired_gaps",
                "lagged_retry_requests",
                "errors",
                "recent_errors",
            )
        }
        current[name].update(
            checked_at=checked,
            receive_age_seconds=(checked - utc(report["last_received_at"])).total_seconds(),
        )
    save("current-streams.json", current)
    result = {
        "recorded_at": datetime.now(timezone.utc),
        "streams": summaries,
        "current": current,
        "protected_hash_matches": protected,
        "source_matches_deployment": source_matches,
        "raw_parity_scope": "separate explicit failure samples; not all sessions",
        "forced_restart_or_source_change": False,
    }
    save("audit.json", result)
    print(
        json_bytes(
            {k: {"states": v["states"], "totals": v["totals"]} for k, v in summaries.items()}
        ).decode()
    )


if __name__ == "__main__":
    main()
