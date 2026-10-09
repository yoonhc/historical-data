"""Real user-systemd retries with fake clock inputs. Never invokes Host or chronyc.

All writes are below a new /tmp directory; the only units manipulated are two
random md-clock-isolation-* names created by this script. Not a root-unit test.
"""

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("isolated_boot", HERE / "clock_gate.py")
boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot)


def profiles(root):
    runtime = boot.rt.load_profile(HERE / "profile.example.json")
    p = {
        "state_dir": str(root),
        "policy": {
            "step_above_seconds": 0.1,
            "max_initial_correction_seconds": 300.0,
            "source_age_seconds": 30.0,
            "attempt_seconds": 0.3,
            "poll_seconds": 0.05,
            "long_wait_seconds": 0.2,
        },
    }
    return p, runtime


class FakeClock:
    def __init__(self, root, runtime):
        self.root, self.runtime = root, runtime

    def rtc_check(self):
        pass  # Explicit fixture. Production RTC checks are separately tested.

    def audit_quiescent(self):
        if (self.root / "child-started").exists():
            raise boot.Hold("Fixture child already started")

    def observe(self):
        f = json.loads((self.root / "fake-clock.json").read_text())
        ntp = 1800000000.0
        t = boot.rt.tracking(
            f"CB00710F,203.0.113.15,3,{ntp - 1},{f['correction']},0,0,0,0,0.1,0.01,0.02,64,"
            + ("Normal" if f["synchronized"] else "Not synchronised")
        )
        wall = ntp - f["correction"]
        return {
            **boot.rt.assess_clock(t, self.runtime["policy"], wall),
            "provider": {"pid": 123, "invocation_id": f["invocation"]},
            "sample": {"wall_ns": int(wall * 1e9)},
        }

    def step(self, expected):
        # Fake correction changes a JSON fixture, NEVER the host system clock.
        self.audit_quiescent()
        with (self.root / "fake-step-calls.jsonl").open("a") as out:
            out.write(json.dumps(expected) + "\n")
        boot.rt.atomic_json(
            self.root / "fake-clock.json",
            {"correction": 0, "synchronized": True, "invocation": expected["invocation_id"]},
        )


def worker(action, root):
    p, runtime = profiles(root)
    ledger = boot.Ledger(root)
    boot.rt.boot_id = lambda: "isolated-boot"
    host = FakeClock(root, runtime)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    if action == "bootstrap":
        return boot.run_bootstrap(p, runtime, ledger, host, stop)
    if action == "gate":
        try:
            result = boot.check_gate(p, runtime, ledger=ledger, checker=lambda _: host.observe())
            print(json.dumps(result), flush=True)
            return 0
        except boot.Hold as exc:
            print(str(exc), file=sys.stderr)
            return 75
    (root / "child-started").write_text(str(os.getpid()))
    while not stop.wait(0.05):
        pass
    (root / "child-finished").write_text("graceful\n")
    return 0


def command(args, check=True):
    result = subprocess.run(args, text=True, capture_output=True, timeout=15)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]}: {result.stderr}")
    return result


def wait_for(predicate, limit=15):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise RuntimeError("isolated test timeout")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["test", "bootstrap", "gate", "child"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action != "test":
        return worker(args.action, args.root)
    root = Path(tempfile.mkdtemp(prefix="md-clock-bootstrap-isolated-"))
    p, _ = profiles(root)
    ledger = boot.Ledger(root)
    with ledger.lock():
        boot.enroll(p, ledger, "installation-boot")
    boot.rt.atomic_json(
        root / "fake-clock.json", {"correction": 41, "synchronized": False, "invocation": "first"}
    )
    token = uuid.uuid4().hex[:10]
    prep, child = [f"md-clock-isolation-{token}-{kind}.service" for kind in ("prepare", "child")]
    py, script = sys.executable, str(Path(__file__).resolve())
    evidence = {
        "root": str(root),
        "units": [prep, child],
        "host_clock_control": False,
        "system_unit_test": False,
    }

    def state(unit):
        out = command(
            [
                "systemctl",
                "--user",
                "show",
                unit,
                "--property=ActiveState,NRestarts,MainPID,Result,ExecStartPre",
            ]
        ).stdout
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    try:
        command(
            [
                "systemd-run",
                "--user",
                "--unit",
                prep,
                "--no-block",
                "-p",
                "Type=oneshot",
                "-p",
                "RemainAfterExit=yes",
                "-p",
                "Restart=on-failure",
                "-p",
                "RestartSec=0.15",
                "-p",
                "StartLimitIntervalSec=0",
                py,
                "-I",
                script,
                "bootstrap",
                "--root",
                str(root),
            ]
        )
        gate = f"{py} -I {script} gate --root {root}"
        command(
            [
                "systemd-run",
                "--user",
                "--unit",
                child,
                "--no-block",
                "-p",
                f"ExecStartPre={gate}",
                "-p",
                "Restart=on-failure",
                "-p",
                "RestartSec=0.15",
                "-p",
                "StartLimitIntervalSec=0",
                "-p",
                "KillSignal=SIGINT",
                "-p",
                "TimeoutStopSec=5",
                py,
                "-I",
                script,
                "child",
                "--root",
                str(root),
            ]
        )
        wait_for(lambda: int(state(prep).get("NRestarts", 0)) >= 2)
        evidence["before_recovery"] = {"prepare": state(prep), "child": state(child)}
        assert f"argv[]={gate} ;" in evidence["before_recovery"]["child"]["ExecStartPre"]
        assert not (root / "child-started").exists()
        assert not (root / "fake-step-calls.jsonl").exists()
        boot.rt.atomic_json(
            root / "fake-clock.json",
            {"correction": 41, "synchronized": True, "invocation": "first"},
        )
        wait_for(lambda: (root / "child-started").exists())
        evidence["after_recovery"] = {"prepare": state(prep), "child": state(child)}
        assert len((root / "fake-step-calls.jsonl").read_text().splitlines()) == 1
        # Restart both the fake provider and completed oneshot while a child runs.
        boot.rt.atomic_json(
            root / "fake-clock.json",
            {"correction": -41, "synchronized": True, "invocation": "second"},
        )
        command(["systemctl", "--user", "restart", prep])
        assert len((root / "fake-step-calls.jsonl").read_text().splitlines()) == 1
        gate_result = command([py, "-I", script, "gate", "--root", str(root)], check=False)
        assert gate_result.returncode == 75
        evidence["post_restart_gate_exit"] = gate_result.returncode
        evidence["step_calls"] = 1
        command(["systemctl", "--user", "stop", child])
        assert (root / "child-finished").read_text() == "graceful\n"
        evidence["after_stop"] = state(child)
        assert evidence["after_stop"]["MainPID"] == "0"
        evidence["passed"] = True
    finally:
        evidence["journal"] = command(
            ["journalctl", "--user", "-u", prep, "-u", child, "--no-pager", "-n", "120"],
            check=False,
        ).stdout
        for unit in (child, prep):
            command(["systemctl", "--user", "stop", unit], check=False)
            command(["systemctl", "--user", "reset-failed", unit], check=False)
        if args.output:
            args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in evidence.items() if k != "journal"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
