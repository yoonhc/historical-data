"""Freeze the first actual day of bounded refresh reports; never write the live catalog."""

import hashlib
import json
import runpy
from collections import Counter
from datetime import timedelta
from pathlib import Path

from marketdata.domain import json_bytes, now, utc

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
READER = ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py"
ReadOnlyStore = runpy.run_path(str(READER))["ReadOnlyStore"]


def save(name, value):
    (OUT / name).write_bytes(json_bytes(value))


def index_report(reader, row):
    r = json.loads(row["report"])
    segments = reader.db.execute(
        "SELECT COUNT(*),COALESCE(SUM(row_count),0),MIN(first_id),MAX(last_id) "
        "FROM event_segments WHERE collection_id=?",
        (r["id"],),
    ).fetchone()
    checks = [c for e in r.get("epochs", []) for c in e.get("final", {}).get("checks", [])]
    result = {
        **{k: r.get(k) for k in ("id", "state", "started_at", "updated_at")},
        **{
            k: r.get(k, 0)
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
                "detected_gaps",
                "repaired_gaps",
                "unrepaired_gaps",
            )
        },
        "report_sha256": hashlib.sha256(row["report"].encode()).hexdigest(),
        "segments": segments[0],
        "canonical_rows": segments[1],
        "first_sequence_or_id": segments[2],
        "last_sequence_or_id": segments[3],
        "catalog_matches_report": segments[1] == r["persisted_messages"] + r.get("snapshots", 0),
        "messages_match_storage": r["messages"] == r["persisted_messages"],
        "verification_checks": len(checks),
        "verification_equal": sum(c["equal"] is True for c in checks),
        "verification_uncomparable": sum(not c["comparable"] for c in checks),
        "verification_mismatch": sum(c["comparable"] and not c["equal"] for c in checks),
        "verification_pending": sum(
            e.get("final", {}).get("pending_check_sequence") is not None
            for e in r.get("epochs", [])
        ),
    }
    assert r["state"] == row["state"] and r["state"] != "running"
    assert result["catalog_matches_report"] and result["messages_match_storage"]
    if row["dataset"] == "book_depth":
        assert r["coverage_refresh"]["policy"] == "bounded_headroom_v1"
        assert "refresh_decisions" not in r
    return result


def summarize(index):
    keys = [
        k
        for k, v in index[0].items()
        if type(v) is int and k not in ("first_sequence_or_id", "last_sequence_or_id")
    ]
    return {
        "sessions": len(index),
        "states": dict(Counter(r["state"] for r in index)),
        "first_started_at": index[0]["started_at"],
        "last_finished_at": index[-1]["updated_at"],
        "totals": {k: sum(r[k] for r in index) for k in keys},
        "all_catalog_counts_match": all(r["catalog_matches_report"] for r in index),
        "all_messages_preserved": all(r["messages_match_storage"] for r in index),
    }


def main():
    assert not (OUT / "audit.json").exists(), "This frozen observation already exists"
    activation = json.loads(
        (OUT.parent / "depth-refresh-2026-10-01/activation-completed.json").read_bytes()
    )
    start = utc(activation["last_sample"]["started_at"])
    stop = start + timedelta(days=1)
    captured_at = now()
    assert captured_at > stop
    reader = ReadOnlyStore(ROOT)
    all_reports, summaries, indices, following = {}, {}, {}, {}
    try:
        reader.db.execute("BEGIN")
        for dataset in ("book_depth", "agg_trades"):
            rows = reader.db.execute(
                "SELECT * FROM event_collections WHERE dataset=? AND kind='websocket' "
                "AND started_at>=? AND started_at<? ORDER BY started_at",
                (dataset, start.isoformat(), captured_at.isoformat()),
            ).fetchall()
            selected = [r for r in rows if utc(r["started_at"]) < stop]
            assert len(selected) == 24 and all(r["state"] != "running" for r in selected)
            reports = [json.loads(r["report"]) for r in selected]
            index = [index_report(reader, r) for r in selected]
            all_reports[dataset], indices[dataset] = reports, index
            summaries[dataset] = summarize(index)
            save(dataset + "-reports.json", reports)
            save(dataset + "-index.json", index)
            later = [r for r in rows if utc(r["started_at"]) >= stop]
            following[dataset] = {
                "closed": [index_report(reader, r) for r in later if r["state"] != "running"],
                "running_excluded": [r["id"] for r in later if r["state"] == "running"],
            }
        reader.db.execute("COMMIT")
    finally:
        reader.close()

    network = []
    for p in sorted((ROOT / "reports/network-observer").glob("????-??-??.jsonl")):
        for lineno, line in enumerate(p.read_bytes().splitlines(), 1):
            r = json.loads(line)
            if not start <= utc(r["started_at"]) < stop:
                continue
            network.append(
                {
                    "started_at": r["started_at"],
                    "finished_at": r["finished_at"],
                    "status": r["status"],
                    "failed_probes": r["failed_probes"],
                    "source": str(p.relative_to(ROOT)),
                    "line": lineno,
                    "line_sha256": hashlib.sha256(line).hexdigest(),
                    "invocation_id": r["context"].get("invocation_id"),
                    "probe_ok": {k: v.get("ok") for k, v in r["probes"].items()},
                }
            )
    network.sort(key=lambda r: r["started_at"])
    assert len({r["started_at"] for r in network}) == len(network)
    (OUT / "network-samples.jsonl").write_bytes(b"\n".join(json_bytes(r) for r in network) + b"\n")
    network_summary = {
        "requested_start": start,
        "requested_end_exclusive": stop,
        "samples": len(network),
        "states": dict(Counter(r["status"] for r in network)),
        "first": network[0]["started_at"],
        "last": network[-1]["started_at"],
        "max_sample_interval_seconds": max(
            (utc(b["started_at"]) - utc(a["started_at"])).total_seconds()
            for a, b in zip(network, network[1:])
        ),
        "failures": [r for r in network if r["status"] != "ok" or r["failed_probes"]],
        "scope": "minute point samples; cannot exclude shorter outages or prove WebSocket delivery",
    }
    save("network.json", network_summary)

    daily = json.loads((ROOT / "state/maintenance-daily-latest.json").read_bytes())
    save("maintenance-daily.json", daily)
    overlaps = [
        r["id"]
        for r in indices["book_depth"]
        if utc(r["started_at"]) <= utc(daily["started_at"])
        and utc(r["updated_at"]) >= utc(daily["finished_at"])
    ]
    previous = json.loads(
        (OUT.parent / "depth-refresh-2026-10-01/operating-closed-sessions.json").read_bytes()
    )
    previously_audited = {r["id"] for r in previous["closed"]}
    anomalies = [r["id"] for r in indices["book_depth"] if r["state"] != "complete"]
    new_anomalies = [cid for cid in anomalies if cid not in previously_audited]
    pending = [r["id"] for r in indices["book_depth"] if r["verification_pending"]]
    last = indices["book_depth"][-1]["id"]
    protected = json.loads(
        (OUT.parent / "depth-budget-candidate-2026-10-02/preparation.json").read_bytes()
    )
    hashes = {
        p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
        for p in protected["operating_sha256"]
    }
    assert hashes == protected["operating_sha256"]
    summary = {
        "captured_at": captured_at,
        "window_start": start,
        "window_end_exclusive": stop,
        "selection": "whole closed sessions starting in the first 24 hours after activation; final session kept to its recorded end; no running sessions counted",
        "window_elapsed": True,
        "report_and_catalog_audit_complete": True,
        "summaries": summaries,
        "following_sessions": following,
        "partial_collection_ids": anomalies,
        "new_partial_collection_ids": new_anomalies,
        "pending_collection_ids": pending,
        "last_closed_sample_id": last,
        "daily_overlap_collection_ids": overlaps,
        "deep_selection": "all newly partial sessions, the pending session, and the final day-window session; the previously audited partial overlaps daily work and retains its earlier evidence",
        "previous_partial_evidence": "../depth-refresh-2026-10-01/operating-failure-verification.json",
        "deep_checks": "pending",
        "network": network_summary,
        "production_sha256_unchanged": hashes,
        "catalog_access": "mode=ro with live WAL; existing shared layout locks for file reads",
        "services_restarted": False,
        "candidate_applied": False,
    }
    save("audit.json", summary)
    print(
        json_bytes(
            {
                "depth": summaries["book_depth"],
                "aggtrade": summaries["agg_trades"],
                "new_partial": new_anomalies,
                "pending": pending,
                "normal_sample": last,
                "network": network_summary,
            }
        ).decode(),
        flush=True,
    )


if __name__ == "__main__":
    main()
