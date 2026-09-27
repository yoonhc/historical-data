"""Read-only post-deployment audit. Run --content after scheduled maintenance ends.

Metadata totals describe entire sessions starting in the fixed 24h window, with
their observation end stated explicitly. Trade/OI checks use the exact window.
Neither collectors nor archived backups are opened as writable stores.
"""

import argparse
import json
import sqlite3
from collections import Counter
from datetime import timedelta
from pathlib import Path

from marketdata import depth
from marketdata.aggtrades import read_trades
from marketdata.domain import json_bytes, now, sha256, utc
from marketdata.store import Store

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent
DEPLOYMENT = ROOT / "reports/baseline/depth-history-2026-09-26/deployment.json"
START = utc(json.loads(DEPLOYMENT.read_bytes())["new_collection"]["started_at"])
END = START + timedelta(days=1)


def save(name, value):
    (OUT / name).write_text(json.dumps(json.loads(json_bytes(value)), indent=2) + "\n")


class Reader:
    _revision_table = Store._revision_table
    file_bytes = Store.file_bytes
    raw = Store.raw

    def __init__(self):
        self.root = ROOT
        uri = (ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro"
        self.db = sqlite3.connect(uri, uri=True)
        self.db.row_factory = sqlite3.Row
        self._files_db = sqlite3.connect(uri, uri=True, isolation_level=None)
        self._files_db.row_factory = sqlite3.Row

    def close(self):
        self._files_db.close()
        self.db.close()


def metadata(reader):
    reports = [
        json.loads(row[0])
        for row in reader.db.execute(
            "SELECT report FROM event_collections WHERE dataset='book_depth' "
            "AND started_at>=? AND started_at<? ORDER BY started_at",
            (START.isoformat(), END.isoformat()),
        ).fetchall()
    ]
    counts, checks, reasons = Counter(), Counter(), Counter()
    sessions, errors = [], []
    for report in reports:
        assert report["history_policy"] == "bounded_same_socket_v1"
        counts["state_" + report["state"]] += 1
        for key in (
            "connections",
            "messages",
            "persisted_messages",
            "connection_errors",
            "sync_losses",
            "coverage_resets",
            "lagged_snapshots",
            "unreconstructed_messages",
        ):
            counts[key] += report.get(key, 0)
        session = {
            key: report.get(key)
            for key in (
                "id",
                "state",
                "started_at",
                "updated_at",
                "last_received_at",
                "connections",
                "connection_errors",
                "sync_losses",
                "coverage_resets",
                "lagged_snapshots",
                "messages",
                "persisted_messages",
                "errors",
                "error",
                "flush_error",
            )
        }
        session["epochs"] = []
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
                key: epoch.get(key)
                for key in ("epoch", "reason", "started_at", "ended_at", "end_reason")
            }
            detail.update(
                error=final.get("error"),
                counts=final.get("counts"),
                checks=len(final.get("checks", [])),
                synced=(final.get("book") or {}).get("synced"),
            )
            session["epochs"].append(detail)
            if detail["error"]:
                errors.append({"collection_id": report["id"], **detail})
        sessions.append(session)
    save("depth-original-reports.json", reports)
    result = {
        "checked_at": now(),
        "fixed_window_start": START,
        "fixed_window_end": END,
        "scope": "Entire sessions starting in the fixed window, through each updated_at. "
        "Includes trailing time outside the window; running sessions are not complete.",
        "sessions": sessions,
        "counts": counts,
        "checks": checks,
        "epoch_reasons": reasons,
        "epoch_errors": errors,
    }
    save("depth-metadata.json", result)
    print(json_bytes({"depth_counts": counts, "checks": checks}).decode(), flush=True)
    probes = []
    records = reader.db.execute(
        "SELECT f.metadata FROM fetches f JOIN artifacts a ON a.id=f.artifact_id "
        "WHERE a.kind='oi_probe' AND f.observed_at>=? AND f.observed_at<? ORDER BY f.id",
        ((START - timedelta(minutes=1)).isoformat(), (END + timedelta(minutes=1)).isoformat()),
    ).fetchall()
    for record in records:
        probe = json.loads(record[0])["oi_probe"]
        if START <= utc(probe["requested_at"]) < END:
            probes.append(probe)
    probes.sort(key=lambda p: p["requested_at"])
    gaps = [
        {
            "before": a["requested_at"],
            "after": b["requested_at"],
            "seconds": (utc(b["requested_at"]) - utc(a["requested_at"])).total_seconds(),
        }
        for a, b in zip(probes, probes[1:])
    ]
    oi = {
        "start": START,
        "end": END,
        "count": len(probes),
        "states": Counter(p["state"] for p in probes),
        "http_statuses": Counter(str(p["status"]) for p in probes),
        "clock_flags": sum(bool(p["clock_flags"]) for p in probes),
        "maximum_interval_seconds": max((g["seconds"] for g in gaps), default=None),
        "gaps_over_45_seconds": [g for g in gaps if g["seconds"] > 45],
        "unsuccessful": [p for p in probes if p["state"] != "ok" or p["status"] != 200],
    }
    save("oi-summary.json", oi)
    print(
        json_bytes(
            {
                "oi_count": len(probes),
                "states": oi["states"],
                "gaps_over_45_seconds": oi["gaps_over_45_seconds"],
            }
        ).decode(),
        flush=True,
    )
    release = json.loads((DEPLOYMENT.parent / "release.json").read_bytes())
    sources = {
        path: {
            "sha256": sha256((ROOT / path).read_bytes()),
            "matches_deployed": sha256((ROOT / path).read_bytes()) == hashes["new_sha256"],
        }
        for path, hashes in release["files"].items()
        if path.endswith(("/depth.py", "/depth_stream.py"))
    }
    assert sources and all(s["matches_deployed"] for s in sources.values())
    save("source-verification.json", sources)
    return reports


def content(reader, reports):
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
        save(f"aggtrade-hour-{hour:02}.json", report)
        hours.append({k: v for k, v in report.items() if k != "segments"})
        if hour % 6 == 5:
            print(json_bytes({"aggtrade_hours_checked": hour + 1}).decode(), flush=True)
    aggregate = {
        "start": START,
        "end": END,
        "rows": sum(h["rows"] for h in hours),
        "valid": all(h["valid"] for h in hours)
        and len(boundaries) == 23
        and all(b["contiguous"] and b["time_ordered"] for b in boundaries),
        "hours": hours,
        "boundaries": boundaries,
        "scope": "Persisted REST/WebSocket IDs, not external proof of time-window edges.",
    }
    save("aggtrade-audit.json", aggregate)
    print(
        json_bytes({"aggtrade_rows": aggregate["rows"], "valid": aggregate["valid"]}).decode(),
        flush=True,
    )
    closed = [r for r in reports if r["state"] != "running"]
    selected = [r for r in closed if r["state"] in ("partial", "interrupted")]
    selected.append(next(r for r in reversed(closed) if r["state"] == "complete"))
    replayed = []
    for report in selected:
        result = depth.replay(reader, report["id"])
        save(f"depth-replay-{report['id']}.json", result)
        reduced = {k: v for k, v in result.items() if k not in ("epochs", "segments")}
        replayed.append(reduced)
        print(json_bytes(reduced).decode(), flush=True)
        assert result["replay_matches_live"]
        if report["state"] != "complete":
            assert not result["valid"]
    save("depth-replay-summary.json", replayed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--content", action="store_true")
    args = parser.parse_args()
    reader = Reader()
    try:
        reports = metadata(reader)
        if args.content:
            daily = json.loads((ROOT / "state/maintenance-daily-latest.json").read_bytes())
            hourly = json.loads((ROOT / "state/maintenance-hourly-latest.json").read_bytes())
            assert daily["state"] != "running" and hourly["state"] != "running"
            content(reader, reports)
    finally:
        reader.close()
