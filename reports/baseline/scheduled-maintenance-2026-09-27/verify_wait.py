"""Verify the real scheduled daily/hourly overlap from saved read-only observations."""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent


def timestamp(value):
    return datetime.fromisoformat(value)


daily = json.loads((OUT / "daily-result.json").read_bytes())
hourly = json.loads((OUT / "hourly-result.json").read_bytes())
observations = json.loads((OUT / "observation.json").read_bytes())["samples"]
unit_samples = json.loads((OUT / "units-observation.json").read_bytes())["samples"]
assert daily["ok"] and hourly["ok"]
assert daily["wait_for_lock"] and hourly["wait_for_lock"]
requested, started, finished = map(
    timestamp, (hourly["requested_at"], hourly["started_at"], hourly["finished_at"])
)
assert requested < timestamp(daily["finished_at"]) <= started
assert requested >= timestamp("2026-09-27T04:15:00+00:00")
assert requested < timestamp("2026-09-27T04:15:10+00:00")
wall_wait = (started - requested).total_seconds()
assert abs(wall_wait - hourly["lock_wait_seconds"]) < 1
waiting = [s for s in observations if requested <= timestamp(s["at"]) < started]
assert len(waiting) >= 2
prior = observations[0]["hourly"]
assert all(s["hourly"] == prior for s in waiting)
unit_waiting = [s for s in unit_samples if requested <= timestamp(s["at"]) < started]
assert len(unit_waiting) >= 2
pack_name, backup_name = "marketdata-pack.service", "marketdata-backup.service"
pids = {s["units"][pack_name]["MainPID"] for s in unit_waiting}
assert len(pids) == 1 and "0" not in pids
assert all(
    s["units"][pack_name]["ActiveState"] == "activating"
    and s["units"][pack_name]["NRestarts"] == "0"
    for s in unit_waiting
)
last_units = unit_samples[-1]["units"]
for name in (pack_name, backup_name):
    assert last_units[name]["ActiveState"] == "inactive"
    assert last_units[name]["Result"] == "success"
    assert last_units[name]["ExecMainStatus"] == "0"
    assert last_units[name]["NRestarts"] == "0"
journal = subprocess.run(
    [
        "journalctl",
        "--user",
        "-u",
        pack_name,
        "-u",
        backup_name,
        "--since",
        "2026-09-27 04:09:00 UTC",
        "--until",
        (finished + timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "--no-pager",
        "-o",
        "short-iso",
    ],
    check=True,
    capture_output=True,
    text=True,
    timeout=30,
).stdout
(OUT / "service-journal.txt").write_text(journal)
assert journal.count('"phase": "waiting_for_maintenance_lock"') == 1
assert "Start request repeated too quickly" not in journal
result = {
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "ok": True,
    "daily_id": daily["id"],
    "hourly_id": hourly["id"],
    "daily_started_at": daily["started_at"],
    "daily_finished_at": daily["finished_at"],
    "hourly_requested_at": hourly["requested_at"],
    "hourly_started_at": hourly["started_at"],
    "hourly_finished_at": hourly["finished_at"],
    "lock_wait_seconds": hourly["lock_wait_seconds"],
    "wall_wait_seconds": wall_wait,
    "daily_finish_to_hourly_start_seconds": (
        started - timestamp(daily["finished_at"])
    ).total_seconds(),
    "waiting_pid": next(iter(pids)),
    "waiting_unit_samples": len(unit_waiting),
    "waiting_report_samples": len(waiting),
    "prior_hourly_report_preserved": True,
    "restart_count": 0,
    "wait_events_in_journal": 1,
    "final_units": last_units,
    "collector_pids_during_maintenance": {
        name: sorted({s["units"][name]["MainPID"] for s in unit_samples})
        for name in ("marketdata-depth.service", "marketdata-aggtrade.service")
    },
    "scope": "Existing timers and real lock contention; no manual maintenance start or restart.",
}
(OUT / "wait-verification.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({k: v for k, v in result.items() if k != "final_units"}))
