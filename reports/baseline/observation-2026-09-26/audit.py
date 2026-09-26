"""Audit the first 24 hours after the bootstrap deployment without changing collection.

Run from the repository root. Catalog connections are read-only. Full transient
results go under reports/observation-2026-09-26; reviewed evidence goes here.
"""

import gzip
import importlib.util
import json
import sqlite3
import subprocess
from collections import Counter
from datetime import timedelta
from pathlib import Path

from marketdata import depth
from marketdata.aggtrades import read_trades
from marketdata.domain import json_bytes, now, sha256, utc
from marketdata.store import Store

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent
LOCAL = ROOT / "reports/observation-2026-09-26"
DEPLOYMENT = ROOT / "reports/baseline/depth-bootstrap-2026-09-25/deployment.json"
START = utc(json.loads(DEPLOYMENT.read_bytes())["activated_at"])
END = START + timedelta(days=1)


def save(name, value, directory=OUT):
    (directory / name).write_text(
        json.dumps(json.loads(json_bytes(value)), ensure_ascii=False, indent=2) + "\n"
    )


class Reader:
    """Use production immutable-file readers without Store's schema initialization."""

    _revision_table = Store._revision_table
    file_bytes = Store.file_bytes
    raw = Store.raw

    def __init__(self):
        self.root = ROOT
        self.db = sqlite3.connect((ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self._files_db = self.db


def collect_metadata(reader):
    reports = [
        json.loads(r["report"])
        for r in reader.db.execute(
            "SELECT * FROM event_collections WHERE dataset='book_depth' "
            "AND started_at>=? AND started_at<? ORDER BY started_at",
            (START.isoformat(), END.isoformat()),
        )
    ]
    assert len(reports) == 24 and all(r["state"] != "running" for r in reports)
    counts, checks, reasons = Counter(), Counter(), Counter()
    reduced, failures = [], []
    for report in reports:
        assert report["bootstrap_policy"] == "buffer_before_anchor_v1"
        counts[report["state"]] += 1
        for key in (
            "sync_losses",
            "coverage_resets",
            "lagged_snapshots",
            "unreconstructed_messages",
            "connections",
            "bootstrap_messages",
            "connection_errors",
            "messages",
            "persisted_messages",
        ):
            counts[key] += report.get(key, 0)
        item = {
            k: report.get(k)
            for k in (
                "id",
                "state",
                "started_at",
                "finished_at",
                "updated_at",
                "bootstrap_policy",
                "connections",
                "bootstrap_messages",
                "sync_losses",
                "coverage_resets",
                "lagged_snapshots",
                "unreconstructed_messages",
                "messages",
                "persisted_messages",
                "connection_errors",
            )
        }
        item["epochs"] = []
        for epoch in report["epochs"]:
            final = epoch.get("final") or {}
            reasons[epoch["reason"]] += 1
            for check in final.get("checks", []):
                checks[
                    "equal"
                    if check.get("equal")
                    else "unequal_comparable"
                    if check.get("comparable")
                    else "uncomparable"
                ] += 1
            detail = {
                k: epoch.get(k) for k in ("epoch", "reason", "started_at", "ended_at", "end_reason")
            }
            detail.update(
                error=final.get("error"),
                counts=final.get("counts"),
                synced=(final.get("book") or {}).get("synced"),
                checks=len(final.get("checks", [])),
                pending=final.get("pending_check_sequence"),
            )
            item["epochs"].append(detail)
            if final.get("error"):
                failures.append({"collection_id": report["id"], **detail})
        reduced.append(item)
    result = {
        "checked_at": now(),
        "fixed_window_start": START,
        "fixed_window_end": END,
        "session_start": reports[0]["started_at"],
        "session_end": reports[-1]["updated_at"],
        "scope": "Totals for 24 completed sessions starting inside the fixed window, including trailing overhang.",
        "counts": counts,
        "checks": checks,
        "epoch_reasons": reasons,
        "failures": failures,
        "sessions": reduced,
    }
    save("depth-sessions.json", result)
    print(
        json_bytes({"depth_counts": counts, "checks": checks, "epoch_reasons": reasons}).decode(),
        flush=True,
    )
    save("original-depth-reports.json", reports, LOCAL)
    return reports


def verify_bootstrap(reader, reports):
    selected = {r["id"]: r for r in reports}
    prefixes, anchors = {}, {}
    for row in reader.db.execute(
        "SELECT f.artifact_id,f.metadata,a.kind FROM fetches f JOIN artifacts a ON a.id=f.artifact_id "
        "WHERE a.kind IN ('depth_bootstrap','depth_snapshot') AND f.observed_at>=? "
        "AND f.observed_at<=? ORDER BY f.id",
        (START.isoformat(), reports[-1]["updated_at"]),
    ):
        metadata = json.loads(row["metadata"])
        if metadata.get("collection_id") not in selected:
            continue
        key = (metadata["collection_id"], metadata["epoch"])
        if row["kind"] == "depth_bootstrap":
            assert key not in prefixes
            prefixes[key] = (row["artifact_id"], metadata)
        elif metadata.get("kind") == "anchor":
            anchors[key] = (row["artifact_id"], metadata)
    results = []
    for report in reports:
        for epoch in report["epochs"]:
            if epoch["reason"] not in ("initial", "planned_rotation"):
                continue
            key = (report["id"], epoch["epoch"])
            source, _ = prefixes[key]
            prefix = json.loads(gzip.decompress(reader.raw(source)[0]))
            anchor_source, metadata = anchors[key]
            anchor_payload = json.loads(reader.raw(anchor_source)[0])
            anchor_id = anchor_payload["lastUpdateId"]
            assert len(prefix) == 1
            assert utc(prefix[0]["received_at"]) < utc(metadata["request_started_at"])
            segments = reader.db.execute(
                "SELECT * FROM event_segments WHERE collection_id=? AND first_id>? "
                "ORDER BY first_id LIMIT 4",
                (report["id"], metadata["sequence"]),
            ).fetchall()
            updates = [
                row
                for segment in segments
                for row in reader._revision_table(segment).to_pylist()
                if row["epoch"] == epoch["epoch"] and row["kind"] == "update"
            ]
            assert updates
            first = updates[0]
            expected = depth.parse(
                json.loads(prefix[0]["message"]),
                "update",
                epoch["epoch"],
                first["sequence"],
                first["source_id"],
                first["source_row"],
                prefix[0]["received_at"],
            )
            assert json_bytes(first) == json_bytes(expected)
            bridge = next(row for row in updates if row["final_update_id"] >= anchor_id)
            assert bridge["first_update_id"] <= anchor_id <= bridge["final_update_id"]
            results.append(
                {
                    "collection_id": report["id"],
                    "epoch": epoch["epoch"],
                    "reason": epoch["reason"],
                    "prefix_source_id": source,
                    "anchor_source_id": anchor_source,
                    "prefix_journaled_exactly": True,
                    "strict_bridge": True,
                    "anchor_id": anchor_id,
                    "bridge_first_id": bridge["first_update_id"],
                    "bridge_final_id": bridge["final_update_id"],
                    "first_receive_to_request_seconds": (
                        utc(metadata["request_started_at"]) - utc(prefix[0]["received_at"])
                    ).total_seconds(),
                }
            )
    assert len(results) == sum(r["connections"] for r in reports) == len(prefixes)
    save(
        "bootstrap-receipts.json",
        {"checked_at": now(), "connections": len(results), "results": results},
    )
    print(json_bytes({"bootstrap_connections_verified": len(results)}).decode(), flush=True)


def audit_aggtrade(reader):
    hours, boundaries, previous = [], [], None
    for hour in range(24):
        left = START + timedelta(hours=hour)
        rows, report = read_trades(reader, left, left + timedelta(hours=1))
        report.update(
            first_timestamp=rows[0]["timestamp"] if rows else None,
            last_timestamp=rows[-1]["timestamp"] if rows else None,
        )
        if previous and rows:
            boundaries.append(
                {
                    "previous_last_id": previous["agg_trade_id"],
                    "next_first_id": rows[0]["agg_trade_id"],
                    "contiguous": rows[0]["agg_trade_id"] == previous["agg_trade_id"] + 1,
                    "time_ordered": rows[0]["timestamp"] >= previous["timestamp"],
                }
            )
        if rows:
            previous = rows[-1]
        save(f"aggtrade-hour-{hour:02}.json", report, LOCAL)
        hours.append({k: v for k, v in report.items() if k != "segments"})
        if hour % 6 == 5:
            print(json_bytes({"aggtrade_hours_checked": hour + 1}).decode(), flush=True)
    result = {
        "start": START,
        "end": END,
        "rows": sum(h["rows"] for h in hours),
        "valid": all(h["valid"] for h in hours)
        and len(boundaries) == 23
        and all(b["contiguous"] and b["time_ordered"] for b in boundaries),
        "hours": hours,
        "boundaries": boundaries,
        "scope": "Persisted combined REST/WebSocket IDs; time-window edge completeness not externally proved.",
    }
    save("aggtrade-audit.json", result)
    print(
        json_bytes({"aggtrade_rows": result["rows"], "valid": result["valid"]}).decode(), flush=True
    )


def audit_samples(reader, reports):
    failed = [r for r in reports if r["sync_losses"]]
    selected = [*failed, reports[-1]]
    replayed = []
    for report in selected:
        result = depth.replay(reader, report["id"])
        assert result["replay_matches_live"]
        save(f"depth-replay-{report['id']}.json", result, LOCAL)
        replayed.append({k: v for k, v in result.items() if k not in ("epochs", "segments")})
        print(json_bytes(replayed[-1]).decode(), flush=True)
    save("depth-replay-summary.json", replayed)

    source = ROOT / "reports/baseline/depth-coverage-2026-09-25/analyze.py"
    spec = importlib.util.spec_from_file_location("coverage_evidence", source)
    coverage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(coverage)
    coverage.OUT = OUT
    cases, lagged = [], []
    for report in failed:
        for epoch in report["epochs"]:
            error = (epoch.get("final") or {}).get("error", "") or ""
            if error == coverage.ERROR:
                cases.append(coverage.analyze(reader.db, report, epoch))
            elif error.startswith("Snapshot bridge missing"):
                rows, inputs, source_count = coverage.load_rows(reader.db, report, epoch)
                anchor, first = rows[:2]
                assert anchor["kind"] == "anchor" and first["kind"] == "update"
                assert first["first_update_id"] > anchor["final_update_id"]
                lagged.append(
                    {
                        "collection_id": report["id"],
                        "epoch": epoch["epoch"],
                        "reason": epoch["reason"],
                        "rows_raw_equal": len(rows),
                        "raw_artifacts_verified": source_count,
                        "anchor_id": anchor["final_update_id"],
                        "first_U": first["first_update_id"],
                        "first_u": first["final_update_id"],
                        "first_pu": first["previous_update_id"],
                        "anchor_exchange_time": anchor["timestamp"],
                        "first_exchange_time": first["timestamp"],
                        "exchange_time_difference_ms": (
                            first["timestamp"] - anchor["timestamp"]
                        ).total_seconds()
                        * 1000,
                        "source_segments": inputs,
                        "unreconstructed_updates": len(rows) - 1,
                    }
                )
    save("failures.json", {"coverage_cases": cases, "lagged_anchor_cases": lagged})


def maintenance_and_oi(reader):
    probes = []
    for row in reader.db.execute(
        "SELECT f.id,f.metadata FROM fetches f JOIN artifacts a ON a.id=f.artifact_id "
        "WHERE a.kind='oi_probe' AND f.observed_at>=? AND f.observed_at<? ORDER BY f.id",
        ((START - timedelta(minutes=1)).isoformat(), (END + timedelta(minutes=1)).isoformat()),
    ):
        probe = json.loads(row["metadata"])["oi_probe"]
        if START <= utc(probe["requested_at"]) < END:
            probes.append(probe)
    probes.sort(key=lambda p: p["requested_at"])
    gaps = [
        (utc(b["requested_at"]) - utc(a["requested_at"])).total_seconds()
        for a, b in zip(probes, probes[1:])
    ]
    save(
        "oi-summary.json",
        {
            "start": START,
            "end": END,
            "count": len(probes),
            "states": Counter(p["state"] for p in probes),
            "http_status": Counter(str(p["status"]) for p in probes),
            "clock_flags": sum(bool(p["clock_flags"]) for p in probes),
            "max_request_gap_seconds": max(gaps),
            "gaps_over_45_seconds": sum(g > 45 for g in gaps),
            "first_requested_at": probes[0]["requested_at"],
            "last_requested_at": probes[-1]["requested_at"],
        },
    )
    runs = []
    for path in sorted((ROOT / "reports/maintenance").glob("*/*.json")):
        run = json.loads(path.read_bytes())
        if START <= utc(run["started_at"]) < END:
            runs.append(
                {
                    k: run.get(k)
                    for k in (
                        "id",
                        "started_at",
                        "finished_at",
                        "mode",
                        "state",
                        "ok",
                        "error",
                        "validation",
                        "backup",
                    )
                }
            )
    save("maintenance-window.json", {"start": START, "end": END, "runs": runs})
    statuses = {}
    for command in ("depth-status", "aggtrade-status", "maintenance-status"):
        proc = subprocess.run([".venv/bin/marketdata", command], capture_output=True, text=True)
        value = json.loads(proc.stdout)
        save(f"{command}.json", value, LOCAL)
        if command == "maintenance-status":
            statuses[command] = value
        else:
            statuses[command] = {k: v for k, v in value.items() if k != "report"}
            statuses[command]["report"] = {
                k: value["report"].get(k)
                for k in (
                    "id",
                    "state",
                    "started_at",
                    "updated_at",
                    "last_received_at",
                    "connections",
                    "sync_losses",
                    "lagged_snapshots",
                    "coverage_resets",
                    "synchronized",
                    "messages",
                    "detected_gaps",
                    "repaired_gaps",
                    "unrepaired_gaps",
                )
            }
        statuses[command]["exit_code"] = proc.returncode
    save("operations.json", {"checked_at": now(), **statuses})


def main():
    LOCAL.mkdir(parents=True, exist_ok=True)
    reader = Reader()
    try:
        reports = collect_metadata(reader)
        verify_bootstrap(reader, reports)
        audit_aggtrade(reader)
        audit_samples(reader, reports)
        maintenance_and_oi(reader)
        paths = [
            Path(__file__).relative_to(ROOT),
            DEPLOYMENT.relative_to(ROOT),
            Path("reports/baseline/depth-coverage-2026-09-25/analyze.py"),
            Path("packages/marketdata/src/marketdata/depth.py"),
            Path("packages/marketdata/src/marketdata/depth_stream.py"),
            Path("packages/marketdata/src/marketdata/aggtrades.py"),
        ]
        save(
            "run.json",
            {
                "checked_at": now(),
                "fixed_window_start": START,
                "fixed_window_end": END,
                "git_head": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True
                ).strip(),
                "source_sha256": {str(p): sha256((ROOT / p).read_bytes()) for p in paths},
            },
        )
    finally:
        reader.db.close()


if __name__ == "__main__":
    main()
