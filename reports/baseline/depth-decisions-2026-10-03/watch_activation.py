"""Observe one natural activation; read-only catalog and systemd, no network or signals."""

import hashlib
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "reports/depth-decisions-20261003/activation-v2"


def save(name, value):
    path = OUT / name
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def service_state():
    result = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            "marketdata-depth.service",
            "--property=ActiveState,SubState,MainPID,ExecMainStartTimestamp,CPUUsageNSec,MemoryCurrent",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    release = json.loads((HERE / "release.json").read_bytes())
    original = release["current_capture_before"]
    expected_start = datetime.fromisoformat(original["started_at"]) + timedelta(
        seconds=original["seconds_requested"] + 15
    )
    started = datetime.now(timezone.utc)
    duration = min(4200, max(300, (expected_start - started).total_seconds() + 300))
    deadline = time.monotonic() + duration
    record = {
        "status": "waiting_for_natural_rotation",
        "started_at": started.isoformat(),
        "original_collection_id": original["id"],
        "expected_next_start_at": expected_start.isoformat(),
        "max_observation_seconds": duration,
        "scope": "initial service activation and 20 seconds of healthy samples; not a completed session or 24-hour quality verdict",
        "forced_restart": False,
    }
    save("activation.json", record)
    healthy_since, candidate = None, None
    try:
        record["service_before"] = service_state()
        while time.monotonic() < deadline:
            for rel, hashes in release["files"].items():
                assert (
                    hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == hashes["new_sha256"]
                ), rel
            observed = datetime.now(timezone.utc)
            report = json.loads((ROOT / "state/depth-latest.json").read_bytes())
            log = report.get("refresh_decisions", {})
            received = report.get("last_received_at")
            age = (
                (observed - datetime.fromisoformat(received)).total_seconds() if received else None
            )
            activated = (
                report["id"] != original["id"]
                and datetime.fromisoformat(report["started_at"])
                >= datetime.fromisoformat(release["installed_at"])
                and report.get("coverage_refresh") == release["request_policy"]
                and log.get("policy") == "coverage_decisions_v1"
                and log.get("evaluations", 0) >= 50
            )
            checks = [c for e in report["epochs"] for c in e.get("final", {}).get("checks", [])]
            equal = sum(bool(c.get("comparable") and c.get("equal")) for c in checks)
            healthy = bool(
                activated
                and report["state"] == "running"
                and report.get("synchronized")
                and age is not None
                and 0 <= age < 10
                and equal >= 2
                and not report.get("error")
                and not report.get("flush_error")
                and not report.get("connection_errors")
                and not report.get("sync_losses")
                and report.get("persisted_messages", 0) >= 50
            )
            sample = {
                "at": observed.isoformat(),
                "collection_id": report["id"],
                "started_at": report["started_at"],
                "state": report["state"],
                "logging_present": activated,
                "healthy": healthy,
                "receive_age_seconds": age,
                "messages": report.get("messages"),
                "persisted_messages": report.get("persisted_messages"),
                "sync_losses": report.get("sync_losses"),
                "connection_errors": report.get("connection_errors"),
                "rest_equal": equal,
                "evaluations": log.get("evaluations"),
                "deferred_evaluations": log.get("deferred_evaluations"),
                "primary_reason_counts": log.get("primary_reason_counts"),
            }
            with (OUT / "samples.jsonl").open("a") as handle:
                handle.write(json.dumps(sample) + "\n")
            record.update(last_sample=sample)
            if not healthy:
                healthy_since, candidate = None, None
            elif candidate != report["id"]:
                healthy_since, candidate = time.monotonic(), report["id"]
            elif time.monotonic() - healthy_since >= 20:
                with closing(
                    sqlite3.connect(
                        (ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro",
                        uri=True,
                    )
                ) as db:
                    current_row = db.execute(
                        "SELECT report FROM event_collections WHERE id=?", (candidate,)
                    ).fetchone()
                    previous_row = db.execute(
                        "SELECT report FROM event_collections WHERE id=?", (original["id"],)
                    ).fetchone()
                    source_ids = db.execute(
                        "SELECT DISTINCT source_id FROM event_segments WHERE collection_id=?",
                        (candidate,),
                    ).fetchall()
                    contexts = []
                    for (source_id,) in source_ids:
                        for (metadata,) in db.execute(
                            "SELECT metadata FROM fetches WHERE artifact_id=?", (source_id,)
                        ):
                            metadata = json.loads(metadata)
                            if (
                                metadata.get("collection_id") == candidate
                                and "refresh_context" in metadata
                            ):
                                contexts.append({"source_id": source_id, **metadata})
                current, previous = json.loads(current_row[0]), json.loads(previous_row[0])
                assert current["refresh_decisions"]["policy"] == "coverage_decisions_v1"
                assert contexts, "No durable REST request context found"
                log = current["refresh_decisions"]
                assert sum(log["primary_reason_counts"].values()) == log["deferred_evaluations"]
                assert len(log["recent_deferrals"]) <= 8
                protected = {
                    p: hashlib.sha256(Path(p).read_bytes()).hexdigest() == expected
                    for p, expected in release["protected_input_sha256"].items()
                }
                assert all(protected.values()), "Protected inputs changed"
                record.update(
                    status="observed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    new_collection_id=candidate,
                    service_after=service_state(),
                    previous_collection_state=previous["state"],
                    previous_end_reason=previous["epochs"][-1].get("end_reason"),
                    request_policy_unchanged=True,
                    protected_hash_matches=protected,
                    persisted_request_contexts=len(contexts),
                    deferred_reason_observed=bool(log["deferred_evaluations"]),
                    first_session_due_after=(
                        datetime.fromisoformat(current["started_at"])
                        + timedelta(seconds=current["seconds_requested"])
                    ).isoformat(),
                    observation_24h_elapsed_at=(
                        datetime.fromisoformat(current["started_at"]) + timedelta(hours=24)
                    ).isoformat(),
                    first_completed_session_verified=False,
                    observation_24h_complete=False,
                )
                save("activated-capture-sample.json", current)
                save("request-contexts.json", contexts)
                save("activation.json", record)
                return 0
            save("activation.json", record)
            time.sleep(min(10, max(0, deadline - time.monotonic())))
        record.update(status="timeout", finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        record.update(
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
    save("activation.json", record)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
