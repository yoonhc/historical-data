"""Audit a finished paired stream soak and summarize its recorded resource samples."""

import argparse
import json
import statistics
from collections import Counter
from datetime import timedelta
from pathlib import Path

from marketdata.aggtrades import read_trades
from marketdata.depth import replay
from marketdata.domain import json_bytes, now, utc
from marketdata.store import Store, atomic_write
from verify_depth_capture import verify as verify_depth


def summarize(root, output):
    run = json.loads((output / "run.json").read_bytes())
    if run["state"] == "running":
        raise ValueError("Wait for the bounded run to finish before auditing")
    samples = [json.loads(line) for line in (output / "telemetry.jsonl").read_text().splitlines()]
    store = Store(root)
    try:
        collections = {}
        for dataset in ("agg_trades", "book_depth"):
            identity = next(
                s["collections"][dataset]["id"] for s in samples if s["collections"][dataset]
            )
            record = store.db.execute(
                "SELECT * FROM event_collections WHERE id=?", (identity,)
            ).fetchone()
            collections[dataset] = json.loads(record["report"])
            atomic_write(output / f"{dataset}-collection.json", json_bytes(collections[dataset]))
        agg = collections["agg_trades"]
        bounds = store.db.execute(
            "SELECT MIN(first_time),MAX(last_time) FROM event_segments WHERE collection_id=?",
            (agg["id"],),
        ).fetchone()
        start, end = utc(bounds[0]), utc(bounds[1]) + timedelta(microseconds=1)
        cursor, audits, previous, boundary_gaps = start, [], None, []
        while cursor < end:
            stop = min(end, cursor + timedelta(minutes=5))
            rows, audit = read_trades(store, cursor, stop)
            if rows:
                if previous is not None and rows[0]["agg_trade_id"] != previous + 1:
                    boundary_gaps.append([previous + 1, rows[0]["agg_trade_id"] - 1])
                previous = rows[-1]["agg_trade_id"]
            audits.append(audit)
            cursor = stop
        agg_audit = {
            "collection_id": agg["id"],
            "start": start,
            "end": end,
            "chunks": audits,
            "boundary_gaps": boundary_gaps,
            "rows": sum(a["rows"] for a in audits),
            "valid": all(a["valid"] for a in audits) and not boundary_gaps,
            "prior_session_gap": agg.get("uncollected_before_start"),
        }
        atomic_write(output / "aggtrade-audit.json", json_bytes(agg_audit))
        depth_id = collections["book_depth"]["id"]
        depth_replay = replay(store, depth_id)
        atomic_write(output / "depth-replay.json", json_bytes(depth_replay))
        parity = verify_depth(store, depth_id, replayed=depth_replay)
        atomic_write(output / "depth-source-parity.json", json_bytes(parity))
        resources = {}
        for name in ("aggtrade", "depth"):
            measured = [
                (s["elapsed_seconds"], s["processes"][name]["metrics"])
                for s in samples
                if s["processes"][name]["metrics"] and "VmRSS" in s["processes"][name]["metrics"]
            ]
            rss = [m["VmRSS"] for _, m in measured]
            elapsed = max(t for t, _ in measured)
            cpu = max(m["cpu_seconds"] for _, m in measured)
            resources[name] = {
                "samples": len(rss),
                "min_rss_bytes": min(rss),
                "median_rss_bytes": statistics.median(rss),
                "max_rss_bytes": max(rss),
                "last_rss_bytes": rss[-1],
                "peak_rss_bytes": max(m["VmHWM"] for _, m in measured),
                "cpu_seconds_at_last_sample": cpu,
                "mean_cpu_percent_one_core": 100 * cpu / elapsed if elapsed else None,
            }
        observations = {}
        for dataset, threshold in (("agg_trades", 30), ("book_depth", 10)):
            ages = [
                s["collections"][dataset]["receive_age_seconds"]
                for s in samples
                if s["collections"][dataset]
                and s["collections"][dataset]["state"] == "running"
                and s["collections"][dataset]["receive_age_seconds"] is not None
            ]
            observations[dataset] = {
                "samples": len(ages),
                "max_receive_age_seconds": max(ages) if ages else None,
                "stale_threshold_seconds": threshold,
                "samples_at_or_over_threshold": sum(a >= threshold for a in ages),
                "note": "Catalog heartbeat includes the flush interval and any planned reconnect.",
            }
        storage = {}
        for dataset, collection in collections.items():
            identities = [collection["id"]]
            identities += [
                r["collection_id"]
                for r in collection.get("recent_recoveries", [])
                if r.get("collection_id")
            ]
            placeholders = ",".join("?" for _ in identities)
            segments = list(
                store.db.execute(
                    f"SELECT * FROM event_segments WHERE collection_id IN ({placeholders})",
                    identities,
                )
            )
            source_ids = {r["source_id"] for r in segments}
            raw = [
                store.db.execute("SELECT * FROM artifacts WHERE id=?", (identity,)).fetchone()
                for identity in source_ids
            ]
            storage[dataset] = {
                "segments": len(segments),
                "raw_files": len(raw),
                "stored_rows_with_duplicates": sum(r["row_count"] for r in segments),
                "canonical_bytes": sum((store.root / r["path"]).stat().st_size for r in segments),
                "raw_bytes": sum(r["bytes"] for r in raw),
                "allocated_bytes": sum(
                    (store.root / r["path"]).stat().st_blocks * 512 for r in [*segments, *raw]
                ),
            }
        operations = []
        for path in sorted(output.glob("operations-*.json")):
            state = json.loads(path.read_bytes())
            oi = next(
                r
                for r in state["datasets"]
                if r["key"]["dataset"] == "open_interest" and r["key"]["series"] == "rest"
            )
            operations.append(
                {
                    "file": path.name,
                    "checked_at": state["checked_at"],
                    "healthy": state["healthy"],
                    "oi_latest": oi["data"]["latest_timestamp"],
                    "oi_last_run_id": oi["last_run"]["id"],
                    "states": {str(r["key"]): r["state"] for r in state["datasets"]},
                }
            )
        parity_samples = []
        for path in sorted(output.glob("aggtrade-parity-*.json")):
            sample = json.loads(path.read_bytes())
            parity_samples.append(
                {
                    "file": path.name,
                    "compared": sample["compared"],
                    "equal": sample["equal"],
                    "mismatched_ids": sample["mismatched_ids"],
                }
            )
        probes = [
            json.loads(r["metadata"])["oi_probe"]
            for r in store.db.execute(
                "SELECT metadata FROM fetches WHERE observed_at>=? AND observed_at<=? "
                "AND json_type(metadata,'$.oi_probe') IS NOT NULL ORDER BY observed_at",
                (run["started_at"], run["finished_at"]),
            )
        ]
        probe_times = [utc(r["received_at"]) for r in probes]
        probe_summary = {
            "observations": len(probes),
            "states": dict(Counter(r["state"] for r in probes)),
            "complete": sum(r["complete"] for r in probes),
            "max_request_seconds": max((r["elapsed_seconds"] for r in probes), default=None),
            "max_between_observations_seconds": max(
                ((b - a).total_seconds() for a, b in zip(probe_times, probe_times[1:])),
                default=None,
            ),
            "note": "Existing 15-second OI observation job; target_present=False alone is not an error.",
        }
        report = {
            "checked_at": now(),
            "run": run,
            "telemetry_samples": len(samples),
            "collections": {
                dataset: {
                    k: c.get(k)
                    for k in (
                        "id",
                        "state",
                        "complete",
                        "messages",
                        "connections",
                        "connection_errors",
                        "sync_losses",
                        "repaired_gaps",
                        "unrepaired_gaps",
                    )
                }
                for dataset, c in collections.items()
            },
            "aggtrade_valid": agg_audit["valid"],
            "aggtrade_unique_rows": agg_audit["rows"],
            "aggtrade_independent_samples": parity_samples,
            "depth_valid": depth_replay["valid"],
            "depth_replay_matches": depth_replay["replay_matches_live"],
            "depth_source_parity": parity,
            "depth_epoch_count": len(depth_replay["epochs"]),
            "depth_refreshes": sum(e.get("refreshes", 0) for e in depth_replay["epochs"].values()),
            "resources": resources,
            "stream_observations": observations,
            "storage": storage,
            "operations": operations,
            "operations_command_results": [
                run["operations_before"],
                *[sample["operations"] for sample in samples if "operations" in sample],
                *([run["operations_after"]] if "operations_after" in run else []),
            ],
            "oi_probe": probe_summary,
            "all_operations_healthy": all(r["healthy"] for r in operations),
            "min_free_bytes": min(s["free_bytes"] for s in samples),
            "notes": [
                "Process metrics are sampled every 30 seconds; CPU percentage is relative to one logical core.",
                "Depth connection boundaries are not repaired history; scope is the currently known snapshot price range.",
                "Aggregate continuity is between observed IDs within this run, excluding the explicitly recorded pre-start gap.",
            ],
        }
        report["passed"] = (
            run["state"] == "complete"
            and agg_audit["valid"]
            and depth_replay["valid"]
            and parity["raw_to_canonical_equal"]
            and parity["all_snapshot_checks_verified"]
            and report["all_operations_healthy"]
            and all(r["exit_code"] == 0 for r in report["operations_command_results"])
            and all(sample["equal"] for sample in parity_samples)
            and (not probes or all(r["complete"] for r in probes))
        )
        atomic_write(output / "summary.json", json_bytes(report))
        return report
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.root, args.output)
    print(
        json_bytes(
            {
                k: result[k]
                for k in (
                    "passed",
                    "aggtrade_unique_rows",
                    "depth_source_parity",
                    "resources",
                    "storage",
                )
            }
        ).decode()
    )
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
