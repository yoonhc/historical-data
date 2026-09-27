"""Observe the existing daily timer without starting or modifying any service."""

import json
import time
from pathlib import Path

from marketdata.domain import json_bytes, now, utc
from marketdata.store import atomic_write

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent
SCHEDULED = utc("2026-09-27T04:10:00Z")


def sample_stream(name):
    report = json.loads((ROOT / f"state/{name}-latest.json").read_bytes())
    received = report.get("last_received_at")
    fields = (
        "id",
        "state",
        "started_at",
        "updated_at",
        "last_received_at",
        "messages",
        "persisted_messages",
        "synchronized",
        "sync_losses",
        "coverage_resets",
        "lagged_snapshots",
        "connection_errors",
        "unrepaired_gaps",
        "out_of_order_messages",
        "error",
        "flush_error",
    )
    return {
        **{key: report.get(key) for key in fields},
        "receive_age_seconds": (now() - utc(received)).total_seconds() if received else None,
    }


def main():
    started, samples, last_phase = now(), [], None
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        daily = json.loads((ROOT / "state/maintenance-daily-latest.json").read_bytes())
        current = utc(daily["started_at"]) >= SCHEDULED
        sample = {
            "at": now(),
            "hourly": json.loads((ROOT / "state/maintenance-hourly-latest.json").read_bytes()),
            "daily": {
                **{
                    key: daily.get(key)
                    for key in ("id", "state", "phase", "started_at", "updated_at", "ok", "error")
                },
                "scheduled_run_started": current,
            },
            "depth": sample_stream("depth"),
            "aggtrade": sample_stream("aggtrade"),
        }
        samples.append(sample)
        phase = (current, daily["state"], daily.get("phase"))
        atomic_write(
            OUT / "observation.json",
            json_bytes(
                {
                    "started_at": started,
                    "scheduled_at": SCHEDULED,
                    "sample_interval_seconds": 10,
                    "samples": samples,
                    "scope": "Sampled persisted application reports, not a proof of every wire receipt.",
                }
            ),
        )
        if phase != last_phase:
            print(json_bytes({"at": sample["at"], "daily": sample["daily"]}).decode(), flush=True)
            last_phase = phase
        hourly = sample["hourly"]
        hourly_done = utc(hourly["started_at"]) >= SCHEDULED and hourly["state"] != "running"
        if current and daily["state"] != "running":
            atomic_write(OUT / "daily-result.json", json_bytes(daily))
            print(
                json_bytes({"finished": True, "ok": daily["ok"], "samples": len(samples)}).decode(),
                flush=True,
            )
            if hourly_done or not daily["ok"]:
                atomic_write(OUT / "hourly-result.json", json_bytes(hourly))
                return
        time.sleep(10)
    raise TimeoutError("The scheduled daily job has not finished within the observation budget")


if __name__ == "__main__":
    main()
