"""Read user-systemd state during the existing daily/hourly scheduled jobs."""

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent
DAILY = datetime.fromisoformat("2026-09-27T04:10:00+00:00")
HOURLY = datetime.fromisoformat("2026-09-27T04:15:00+00:00")
UNITS = (
    "marketdata-backup.service",
    "marketdata-pack.service",
    "marketdata-backup.timer",
    "marketdata-pack.timer",
    "marketdata-depth.service",
    "marketdata-aggtrade.service",
)
PROPERTIES = (
    "Id,ActiveState,SubState,MainPID,NRestarts,Result,ExecMainStatus,"
    "ExecMainStartTimestamp,ExecMainExitTimestamp,LastTriggerUSec,NextElapseUSecRealtime"
)
samples = []
last_phase = None
deadline = time.monotonic() + 3600
while time.monotonic() < deadline:
    result = subprocess.run(
        ["systemctl", "--user", "show", *UNITS, "--property=" + PROPERTIES],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    units = {}
    for block in result.stdout.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        units[fields["Id"]] = fields
    sample = {"at": datetime.now(timezone.utc).isoformat(), "units": units}
    samples.append(sample)
    temporary = OUT / "units-observation.json.tmp"
    temporary.write_text(json.dumps({"interval_seconds": 10, "samples": samples}, indent=2) + "\n")
    temporary.replace(OUT / "units-observation.json")
    phase = [
        (units[u]["ActiveState"], units[u]["SubState"], units[u]["MainPID"], units[u]["NRestarts"])
        for u in UNITS[:2]
    ]
    if phase != last_phase:
        print(json.dumps({"at": sample["at"], "backup_and_pack": phase}), flush=True)
        last_phase = phase
    daily = json.loads((ROOT / "state/maintenance-daily-latest.json").read_bytes())
    hourly = json.loads((ROOT / "state/maintenance-hourly-latest.json").read_bytes())
    daily_done = (
        datetime.fromisoformat(daily["started_at"]) >= DAILY and daily["state"] != "running"
    )
    hourly_done = (
        datetime.fromisoformat(hourly["started_at"]) >= HOURLY and hourly["state"] != "running"
    )
    if daily_done and (hourly_done or not daily["ok"]):
        if all(units[u]["ActiveState"] in ("inactive", "failed") for u in UNITS[:2]):
            print(json.dumps({"finished": True, "samples": len(samples)}), flush=True)
            break
    time.sleep(10)
else:
    raise TimeoutError("Scheduled maintenance has not finished within one hour")
