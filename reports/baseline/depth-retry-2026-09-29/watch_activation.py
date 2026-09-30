"""Bounded read-only observation of the next natural depth session.

No signals, services, timers, network requests, or catalog writes. The caller
starts this once after release.json exists; evidence is written in this directory.
"""

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def save(name, data):
    path = OUT / name
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def main():
    release = json.loads((OUT / "release.json").read_bytes())
    original = release["current_capture_before"]
    expected = release["files"]["packages/marketdata/src/marketdata/depth_stream.py"]["new_sha256"]
    started = datetime.now(timezone.utc)
    expected_end = datetime.fromisoformat(original["started_at"]) + timedelta(
        seconds=original["seconds_requested"] + 15
    )
    # A fixed monotonic limit prevents wall-clock changes from extending this task.
    duration = max(300, (expected_end - started).total_seconds() + 300)
    deadline = time.monotonic() + min(duration, 4200)
    samples, candidate, healthy_since = 0, None, None
    record = {
        "status": "waiting_for_natural_rotation",
        "started_at": started.isoformat(),
        "original_collection_id": original["id"],
        "expected_next_start_at": expected_end.isoformat(),
        "max_observation_seconds": min(duration, 4200),
        "no_services_restarted": True,
        "scope": "new operating session policy and 20 seconds of healthy samples; no claim of retry branch occurrence or 24-hour stability",
    }
    save("activation.json", record)
    try:
        while time.monotonic() < deadline:
            observed = datetime.now(timezone.utc)
            report = json.loads((ROOT / "state/depth-latest.json").read_bytes())
            source = ROOT / "packages/marketdata/src/marketdata/depth_stream.py"
            if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise RuntimeError("Deployed source changed during activation observation")
            received = report.get("last_received_at")
            age = (
                (observed - datetime.fromisoformat(received)).total_seconds() if received else None
            )
            new_policy = (
                report["id"] != original["id"]
                and datetime.fromisoformat(report["started_at"])
                >= datetime.fromisoformat(release["installed_at"])
                and report.get("lagged_retry_policy") == "same_socket_backoff_v1"
                and report.get("lagged_retry_delays_seconds") == [1, 2, 4, 8, 10]
                and report.get("lagged_retry_budget", {}).get("max_requests") == 8
                and report.get("lagged_retry_budget", {}).get("window_seconds") == 60
            )
            healthy = bool(
                new_policy
                and report["state"] == "running"
                and report.get("synchronized")
                and age is not None
                and 0 <= age < 10
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
                "policy": report.get("lagged_retry_policy"),
                "new_policy": new_policy,
                "healthy": healthy,
                "receive_age_seconds": age,
                "messages": report.get("messages"),
                "persisted_messages": report.get("persisted_messages"),
                "sync_losses": report.get("sync_losses"),
                "lagged_retry_requests": report.get("lagged_retry_requests"),
            }
            with (OUT / "activation-samples.jsonl").open("a") as handle:
                handle.write(json.dumps(sample) + "\n")
            samples += 1
            record.update(last_sample=sample, samples=samples)
            if not healthy:
                candidate, healthy_since = None, None
            elif candidate != report["id"]:
                candidate, healthy_since = report["id"], time.monotonic()
            elif time.monotonic() - healthy_since >= 20:
                with closing(
                    sqlite3.connect((ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro", uri=True)
                ) as db:
                    row = db.execute(
                        "SELECT dataset,kind,state,report FROM event_collections WHERE id=?",
                        (candidate,),
                    ).fetchone()
                    previous = db.execute(
                        "SELECT state,report FROM event_collections WHERE id=?", (original["id"],)
                    ).fetchone()
                if row is None or row[:3] != ("book_depth", "websocket", "running"):
                    raise RuntimeError("New capture not confirmed in the live catalog")
                current = json.loads(row[3])
                if current.get("lagged_retry_policy") != "same_socket_backoff_v1":
                    raise RuntimeError("Catalog policy differs from the latest report")
                prior = json.loads(previous[1]) if previous else {}
                record.update(
                    status="observed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    new_collection_id=candidate,
                    previous_collection_state=previous[0] if previous else None,
                    previous_end_reason=(prior.get("epochs") or [{}])[-1].get("end_reason"),
                    source_sha256=expected,
                    retry_branch_observed=bool(current.get("lagged_retry_requests")),
                )
                save("activated-capture-sample.json", current)
                save("activation.json", record)
                return
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


if __name__ == "__main__":
    main()
