"""Install the tested maintenance release, reload units, and run one hourly job.

Requires the user systemd bus. Production collectors are never stopped/restarted.
Unit files are existing symlinks into the repo. Installation is serialized with
maintenance and rolled back if copying or daemon-reload/definition checks fail.
The later operational smoke run records its result without changing data policy.
"""

import fcntl
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

OUT = Path(__file__).resolve().parent
release = json.loads((OUT / "release.json").read_text())
ROOT, STAGE = Path(release["destination_root"]), Path(release["source_root"])
UNIT_DIR = Path("/home/yoonhc/.config/systemd/user")
MAINTENANCE = ("marketdata-pack.service", "marketdata-backup.service")
COLLECTORS = ("marketdata-depth.service", "marketdata-aggtrade.service")
TIMERS = ("marketdata-pack.timer", "marketdata-backup.timer")
PROPERTIES = (
    "Id,ActiveState,SubState,Result,ExecMainStatus,ExecStart,MainPID,NRestarts,"
    "ExecMainStartTimestamp,NextElapseUSecRealtime,TimeoutStartUSec,RestartUSec,StartLimitBurst"
)


def digest(path):
    return sha256(path.read_bytes()).hexdigest() if path.exists() else None


def control(*args, timeout=30):
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )


def units():
    return {
        unit: dict(
            line.split("=", 1)
            for line in control("show", unit, "--property=" + PROPERTIES).stdout.splitlines()
            if "=" in line
        )
        for unit in (*MAINTENANCE, *COLLECTORS, *TIMERS)
    }


def atomic_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".maintenance-wait-new")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


assert release["tests"]["passed"] == 295 and release["tests"]["failed"] == 0
assert digest(OUT / "pilot.json") == release["pilot_sha256"]
assert json.loads((OUT / "pilot.json").read_text())["ok"]
assert (
    subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    == (release["base_commit"])
)
for name, hashes in release["files"].items():
    assert digest(ROOT / name) == hashes["old_sha256"], f"Destination changed: {name}"
    assert digest(STAGE / name) == hashes["new_sha256"], f"Staged file changed: {name}"
for unit in MAINTENANCE:
    path = UNIT_DIR / unit
    assert path.is_symlink() and path.resolve() == ROOT / "integrations/systemd" / unit
before = units()
assert all(before[unit]["ActiveState"] == "active" for unit in (*COLLECTORS, *TIMERS))
backup = STAGE / "predeployment-maintenance-wait"
backup.mkdir(exist_ok=False)
for name, hashes in release["files"].items():
    if hashes["old_sha256"] is not None:
        atomic_copy(ROOT / name, backup / name)

with (ROOT / "state/maintenance.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert all(units()[unit]["ActiveState"] == "inactive" for unit in MAINTENANCE)
    try:
        # The new run() argument is optional, so installing maintenance.py before
        # cli.py is compatible with old CLI processes starting during this step.
        for name in release["files"]:
            atomic_copy(STAGE / name, ROOT / name)
        control("daemon-reload")
        installed = units()
        for unit in MAINTENANCE:
            assert "--wait-for-lock" in installed[unit]["ExecStart"]
            assert installed[unit]["TimeoutStartUSec"] == "6h"
            assert installed[unit]["RestartUSec"] == "2min"
            assert installed[unit]["StartLimitBurst"] == "4"
        assert all(
            digest(ROOT / name) == hashes["new_sha256"] for name, hashes in release["files"].items()
        )
    except BaseException:
        for name, hashes in reversed(release["files"].items()):
            if hashes["old_sha256"] is None:
                (ROOT / name).unlink(missing_ok=True)
            else:
                atomic_copy(backup / name, ROOT / name)
        control("daemon-reload")
        (OUT / "deployment-failed.json").write_text('{"installation_rolled_back": true}\n')
        raise

result = {
    "installed_at": datetime.now(timezone.utc).isoformat(),
    "installed_hashes_match": True,
    "units_before": before,
    "units_after_reload": installed,
    "collector_restart_commands": [],
}
(OUT / "deployment.json").write_text(json.dumps(result, indent=2) + "\n")
control("start", "marketdata-pack.service", timeout=120)
report = json.loads((ROOT / "state/maintenance-hourly-latest.json").read_text())
(OUT / "hourly-result.json").write_text(json.dumps(report, indent=2) + "\n")
assert report["ok"] and report["wait_for_lock"]
after = units()
result.update(
    finished_at=datetime.now(timezone.utc).isoformat(),
    units_after_smoke=after,
    collector_pids_unchanged=all(
        before[unit]["MainPID"] == after[unit]["MainPID"] for unit in COLLECTORS
    ),
    timer_deadlines_unchanged=all(
        before[unit]["NextElapseUSecRealtime"] == after[unit]["NextElapseUSecRealtime"]
        for unit in TIMERS
    ),
    hourly_smoke={"id": report["id"], "ok": report["ok"], "wait_for_lock": True},
)
environment = dict(os.environ)
environment.pop("PYTHONPATH", None)
result["statuses"] = {}
for command in ("depth-status", "aggtrade-status", "maintenance-status"):
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/marketdata"), "--root", str(ROOT), command],
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    value = json.loads(proc.stdout)
    result["statuses"][command] = {"exit_code": proc.returncode, "healthy": value["healthy"]}
result["ok"] = report["ok"] and all(
    status["exit_code"] == 0 and status["healthy"] for status in result["statuses"].values()
)
(OUT / "deployment.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({k: v for k, v in result.items() if not k.startswith("units_")}), flush=True)
assert result["ok"]
