"""Bounded user-systemd contention tests; only synthetic stores and transient units.

Run with staged packages and tests on PYTHONPATH. The unchanged production CLI
is a fail-fast control. Test retry intervals are shortened; no production unit
or store is changed. Completed transient services may be unloaded by systemd.
"""

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from marketdata import maintenance as ops
from marketdata.store import Store
from test_maintenance import closed_events

OUT = Path(__file__).resolve().parent
STAGE = OUT.parents[2]
ROOT = Path("/home/yoonhc/projects/algoTrade/historical_data")
NONCE = uuid.uuid4().hex[:8]
WORK = STAGE / "reports/maintenance-wait-2026-09-26/pilot-stores" / NONCE
WORK.mkdir(parents=True)
PROPERTIES = (
    "LoadState,ActiveState,SubState,Result,ExecMainStatus,MainPID,NRestarts,"
    "TimeoutStartUSec,RestartUSec,StartLimitBurst"
)


def control(*args, check=True):
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, timeout=15, check=check
    )


def status(unit):
    result = control("show", unit, "--property=" + PROPERTIES, check=False)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def wait_until(predicate, seconds=12):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("Pilot condition not reached")
        time.sleep(0.05)


def run_case(label, *, mode="hourly", wait=True, timeout=False):
    directory = WORK / label
    directory.mkdir()
    store = Store(directory / "store")
    closed_events(store)
    latest = store.root / f"state/maintenance-{mode}-latest.json"
    prior = b'{"pilot":"prior report"}\n'
    latest.write_bytes(prior)
    stdout, stderr = directory / "stdout.jsonl", directory / "stderr.jsonl"
    unit = f"marketdata-lock-pilot-{label}-{NONCE}.service"
    command = [
        "systemd-run",
        "--user",
        "--wait",
        "--unit=" + unit,
        "--service-type=oneshot",
        "--property=Restart=" + ("no" if timeout else "on-failure"),
        "--property=RestartSec=100ms",
        "--property=StartLimitIntervalSec=30s",
        "--property=StartLimitBurst=4",
        "--property=TimeoutStartSec=" + ("3s" if timeout else "30s"),
        "--property=TimeoutStopSec=5s",
        "--property=KillSignal=SIGINT",
        "--property=StandardOutput=append:" + str(stdout),
        "--property=StandardError=append:" + str(stderr),
        "--setenv=PYTHONPATH=" + str((STAGE if wait else ROOT) / "packages/marketdata/src"),
        sys.executable,
        "-m",
        "marketdata.cli",
        "--root",
        str(store.root),
        "maintenance",
        "--mode",
        mode,
        "--min-free-gib=0",
    ]
    if wait:
        command.append("--wait-for-lock")
    process = None
    evidence = {"unit": unit, "mode": mode, "command": command}
    try:
        with ops.maintenance_lock(store):
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            marker = "waiting_for_maintenance_lock" if wait else "Another maintenance job"
            wait_until(lambda: stderr.exists() and marker in stderr.read_text())
            evidence["while_locked_first"] = status(unit)
            if wait and not timeout:
                # Repeated start requests while activating must share one job.
                control("start", "--no-block", unit)
                with store.writer():
                    store.put_raw(b"collection during lock wait", "test:pilot", "test")
                time.sleep(2)
                evidence["while_locked_last"] = status(unit)
                for sample in (evidence["while_locked_first"], evidence["while_locked_last"]):
                    assert sample["ActiveState"] == "activating", sample
                    assert sample["NRestarts"] == "0", sample
                    assert int(sample["MainPID"]) > 0, sample
                assert (
                    evidence["while_locked_first"]["MainPID"]
                    == evidence["while_locked_last"]["MainPID"]
                )
                assert process.poll() is None
            else:
                runner_out, runner_error = process.communicate(timeout=12)
                assert process.returncode != 0

                # systemd-run --wait can return at the first failed attempt,
                # while the service still has automatic retries pending.
                # systemd 255 can retain Result=exit-code at the start limit;
                # check terminal state and retry count, then the journal.
                def terminal_failure():
                    state = status(unit)
                    return state.get("ActiveState") == "failed" and (
                        state.get("Result") == "timeout"
                        if timeout
                        else state.get("NRestarts") == "4"
                    )

                wait_until(terminal_failure)
                evidence["failure"] = status(unit)
                journal = subprocess.run(
                    ["journalctl", "--user", "-u", unit, "--no-pager", "-o", "cat"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                ).stdout
                evidence["journal"] = journal
                if not timeout:
                    assert "Start request repeated too quickly" in journal
            assert latest.read_bytes() == prior
            assert not (store.root / "reports/maintenance").exists()
            evidence["prior_report_preserved_while_waiting"] = True
        if wait and not timeout:
            runner_out, runner_error = process.communicate(timeout=20)
            assert process.returncode == 0, runner_error.decode()
            report = json.loads(latest.read_text())
            assert report["ok"] and report["packing"]["files"] == 8
            assert report["new_prune"]["removed"] == 8
            assert report["lock_wait_seconds"] >= 2
            assert report["wait_for_lock"] and report["logical_files_deleted"] == 0
            if mode == "daily":
                assert report["validation"]["ok"] and not report["backup"]["reused"]
            assert store.validate()["ok"]
            evidence["report"] = report
            evidence["collection_write_while_waiting"] = True
            evidence["validation_after"] = True
        evidence.update(
            runner_exit_code=process.returncode,
            runner_stdout=runner_out.decode(),
            runner_stderr=runner_error.decode(),
            stderr_events=[json.loads(line) for line in stderr.read_text().splitlines()],
            final_service=status(unit),
            ok=True,
        )
        return evidence
    finally:
        if status(unit).get("LoadState") == "loaded":
            control("stop", unit, check=False)
        if process and process.poll() is None:
            process.communicate(timeout=10)
        # Only these uniquely named test units are touched.
        control("reset-failed", unit, check=False)
        store.close()


assert Path(ops.__file__).resolve().is_relative_to(STAGE)
assert os.getuid() != 0
result = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "work": str(WORK),
    "retry_interval_seconds": 0.1,
    "start_limit_interval_seconds": 30,
    "start_limit_burst": 4,
    "cases": [],
}
for label, arguments in (
    ("old", {"wait": False}),
    ("hourly", {}),
    ("daily", {"mode": "daily"}),
    ("timeout", {"timeout": True}),
):
    result["cases"].append(run_case(label, **arguments))
    (OUT / "pilot.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"case": label, "ok": True}), flush=True)
result.update(finished_at=datetime.now(timezone.utc).isoformat(), ok=True)
(OUT / "pilot.json").write_text(json.dumps(result, indent=2) + "\n")
