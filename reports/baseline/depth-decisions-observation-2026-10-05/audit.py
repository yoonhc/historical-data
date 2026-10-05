"""Freeze completed captures after decision logging; read the live catalog with WAL."""

import hashlib
import json
import runpy
from collections import Counter
from datetime import timedelta
from pathlib import Path

from marketdata.domain import json_bytes, now, utc

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
ReadOnlyStore = runpy.run_path(
    str(OUT.parent / "depth-retry-2026-09-29/check_operating_capture.py")
)["ReadOnlyStore"]


def save(name, value):
    with (OUT / name).open("xb") as handle:
        handle.write(json_bytes(value))


def summarize(index):
    numeric = [k for k, v in index[0].items() if type(v) is int]
    return {
        "sessions": len(index),
        "states": dict(Counter(r["state"] for r in index)),
        "first_started_at": index[0]["started_at"],
        "last_finished_at": index[-1]["updated_at"],
        "totals": {k: sum(r[k] for r in index) for k in numeric},
        "primary_reason_counts": dict(sum((Counter(r["primary"]) for r in index), Counter())),
        "blocker_counts": dict(sum((Counter(r["blockers"]) for r in index), Counter())),
        "sessions_with_deferrals": sum(r["deferred_evaluations"] > 0 for r in index),
    }


def main():
    captured = now()
    reader = ReadOnlyStore(ROOT)
    try:
        reader.db.execute("BEGIN")
        rows = reader.db.execute(
            "SELECT * FROM event_collections WHERE dataset='book_depth' AND kind='websocket' "
            "AND started_at>=? AND started_at<? ORDER BY started_at",
            ("2026-10-03T02:17:30.060370+00:00", captured.isoformat()),
        ).fetchall()
        closed = [r for r in rows if r["state"] != "running"]
        reports = [json.loads(r["report"]) for r in closed]
        assert reports[0]["id"] == "6d39cd17a2604b97a53e2932ac829e19"
        assert len(reports) == 45, "Review any changed selection before extending this audit"
        start = utc(reports[0]["started_at"])
        index = []
        for row, report in zip(closed, reports, strict=True):
            log = report["refresh_decisions"]
            assert log["policy"] == "coverage_decisions_v1"
            assert sum(log["primary_reason_counts"].values()) == log["deferred_evaluations"]
            assert report["messages"] == report["persisted_messages"]
            assert report["state"] == row["state"]
            segments, count = reader.db.execute(
                "SELECT COUNT(*),SUM(row_count) FROM event_segments WHERE collection_id=?",
                (report["id"],),
            ).fetchone()
            assert count == report["messages"] + report["snapshots"]
            checks = [c for e in report["epochs"] for c in e["final"]["checks"]]
            index.append(
                {
                    **{k: report[k] for k in ("id", "started_at", "updated_at", "state")},
                    **{
                        k: report[k]
                        for k in (
                            "messages",
                            "persisted_messages",
                            "snapshots",
                            "connections",
                            "connection_errors",
                            "sync_losses",
                            "coverage_resets",
                            "coverage_top_shortfalls",
                            "lagged_snapshots",
                            "lagged_retry_requests",
                            "unreconstructed_messages",
                            "coverage_check_requests",
                            "periodic_check_requests",
                        )
                    },
                    "segments": segments,
                    "canonical_rows": count,
                    "verification_checks": len(checks),
                    "verification_equal": sum(c["equal"] is True for c in checks),
                    "verification_uncomparable": sum(not c["comparable"] for c in checks),
                    "verification_mismatch": sum(
                        c["comparable"] and not c["equal"] for c in checks
                    ),
                    "verification_pending": sum(
                        e["final"]["pending_check_sequence"] is not None for e in report["epochs"]
                    ),
                    "evaluations": log["evaluations"],
                    "deferred_evaluations": log["deferred_evaluations"],
                    "primary": log["primary_reason_counts"],
                    "blockers": log["blocker_counts"],
                    "catalog_report_sha256": hashlib.sha256(row["report"].encode()).hexdigest(),
                }
            )
        save("book_depth-reports.json", reports)
        save("book_depth-index.json", index)
        save(
            "running-excluded.json",
            [json.loads(r["report"]) for r in rows if r["state"] == "running"],
        )
        reader.db.execute("COMMIT")
    finally:
        reader.close()
    first_day = [r for r in index if utc(r["started_at"]) < start + timedelta(days=1)]
    assert len(first_day) == 24
    release = json.loads((OUT.parent / "depth-decisions-2026-10-03/release.json").read_bytes())
    code = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in release["files"]}
    assert all(code[p] == info["new_sha256"] for p, info in release["files"].items())
    network = []
    for path in sorted((ROOT / "reports/network-observer").glob("????-??-??.jsonl")):
        for number, line in enumerate(path.read_bytes().splitlines(), 1):
            r = json.loads(line)
            if start <= utc(r["started_at"]) < utc(index[-1]["updated_at"]):
                network.append(
                    {
                        "started_at": r["started_at"],
                        "finished_at": r["finished_at"],
                        "status": r["status"],
                        "failed_probes": r["failed_probes"],
                        "source": str(path.relative_to(ROOT)),
                        "line": number,
                        "sha256": hashlib.sha256(line).hexdigest(),
                    }
                )
    network.sort(key=lambda r: r["started_at"])
    save("network-samples.json", network)
    result = {
        "captured_at": captured,
        "selection": "All 45 closed captures after activation, fixed at this observation; running excluded",
        "first_24h_start_window": {
            "start": start,
            "end_exclusive": start + timedelta(days=1),
            "summary": summarize(first_day),
        },
        "all_closed": summarize(index),
        "later_closed": summarize(index[len(first_day) :]),
        "partial_ids": [r["id"] for r in index if r["state"] != "complete"],
        "deferral_ids": [r["id"] for r in index if r["deferred_evaluations"]],
        "network": {
            "samples": len(network),
            "states": dict(Counter(r["status"] for r in network)),
            "failures": [r for r in network if r["status"] != "ok" or r["failed_probes"]],
        },
        "depth_source_sha256": code,
        "deep_checks": "pending",
        "catalog_mode": "mode=ro, live WAL, short consistent metadata transaction",
    }
    save("audit.json", result)
    print(
        json_bytes(
            {
                k: result[k]
                for k in ("all_closed", "first_24h_start_window", "partial_ids", "network")
            }
        ).decode(),
        flush=True,
    )


if __name__ == "__main__":
    main()
