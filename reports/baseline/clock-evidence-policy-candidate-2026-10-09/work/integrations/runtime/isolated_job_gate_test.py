"""Eight queued user jobs plus the real coordinator audit, using a fake clock.

Only randomly named md-clock-jobs-* units are linked in the runtime user manager.
Business commands write /tmp evidence; no original ExecStart, chronyc, or Host.rtc_check
is executed. Timeouts/limits are shortened to test waiting beyond a job timeout.
This is NOT a root-system sandbox, calendar catch-up, or host clock-control test.
"""

import argparse
import configparser
import importlib.util
import json
import os
import signal
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("fixture", HERE / "isolated_bootstrap_test.py")
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)
boot = fixture.boot
JOBS = [
    "daily",
    "backup",
    "pack",
    "oi",
    "oi-probe",
    "oi-quality",
    "restic-archive",
    "network-observer",
]


def command(*args, check=True):
    return fixture.command(list(args), check=check)


def state(unit):
    out = command(
        "systemctl",
        "--user",
        "show",
        unit,
        "--property=LoadState,ActiveState,SubState,MainPID,ControlPID,NRestarts,"
        "Result,ExecMainStartTimestampMonotonic,After,Requires",
    ).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


class Clock(fixture.FakeClock):
    def __init__(self, root, runtime, p):
        super().__init__(root, runtime)
        self.p = p

    def audit_quiescent(self):
        host = boot.Host(self.p, self.runtime)

        def user(*args):
            args = list(args)
            if args[0] == "list-unit-files":
                args[1] = self.p["fixture_job_prefix"] + "*"
            return command("systemctl", "--user", *args).stdout

        # Same Host audit, actual states/commands/dependencies. Only manager selection
        # differs: current user directly, instead of root -> runuser -> current user.
        host.user_command = user
        host.audit_quiescent()


def worker(action, root, job):
    p = json.loads((root / "profile.json").read_text())
    runtime = boot.rt.load_profile(HERE / "profile.example.json")
    boot.rt.boot_id = lambda: "fixture-next-boot"
    ledger, stop = boot.Ledger(root), threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    host = Clock(root, runtime, p)

    def checker():
        return boot.check_gate(p, runtime, ledger=ledger, checker=lambda _: host.observe())

    if action == "bootstrap":
        return boot.run_bootstrap(p, runtime, ledger, host, stop)
    if action == "wait":
        return boot.wait_for_gate(checker, stop, poll_seconds=0.05, long_wait_seconds=0.2)
    if action == "gate":
        try:
            checker()
            return 0
        except boot.Hold:
            return 75
    checker()
    with (root / (job + ".jsonl")).open("a") as out:
        out.write(
            json.dumps({"job": job, "monotonic": time.monotonic(), "pid": os.getpid()}) + "\n"
        )
    return 1 if (root / ("fail-" + job)).exists() else 0


def test(output):
    root = Path(tempfile.mkdtemp(prefix="md-clock-jobs-"))
    token = uuid.uuid4().hex[:8]
    prefix = f"md-clock-jobs-{token}-"
    jp = prefix + "work-"
    wait_unit, prep = jp + "clock.service", prefix + "bootstrap.service"
    py, script = sys.executable, str(Path(__file__).resolve())
    base = f"{py} -I {script}"
    gate, wait = f"{base} gate --root {root}", f"{base} wait --root {root}"
    p, _ = fixture.profiles(root)
    p.update(
        guard_files={},
        system_collectors=[],
        fixture_job_prefix=jp,
        guard_command=gate,
        wait_command=wait,
        wait_unit=wait_unit,
        user_units={**{jp + n + ".service": "guarded" for n in JOBS}, wait_unit: "waiter"},
    )
    (root / "profile.json").write_text(json.dumps(p))
    ledger = boot.Ledger(root)
    with ledger.lock():
        boot.enroll(p, ledger, "installation-boot")
    boot.rt.atomic_json(
        root / "fake-clock.json", {"correction": 41, "synchronized": False, "invocation": "first"}
    )
    unitdir = root / "units"
    unitdir.mkdir()
    files = {
        wait_unit: f"[Service]\nType=oneshot\nExecStart={wait}\nTimeoutStartSec=infinity\n"
        "TimeoutStopSec=5s\nRemainAfterExit=no\nNoNewPrivileges=yes\n",
        prep: f"[Unit]\nStartLimitIntervalSec=0\n[Service]\nType=oneshot\n"
        f"ExecStart={base} bootstrap --root {root}\nRemainAfterExit=yes\n"
        "Restart=on-failure\nRestartSec=0.1s\nTimeoutStartSec=30s\n",
    }
    policies = {}
    for name in JOBS:
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.read(HERE.parent / "systemd" / f"marketdata-{name}.service")
        policies[name] = {s: dict(cp[s]) for s in cp.sections()}
        restart = cp.get("Service", "Restart", fallback="no")
        files[jp + name + ".service"] = (
            f"[Unit]\nRequires={wait_unit}\nAfter={wait_unit}\n"
            "StartLimitIntervalSec=20s\nStartLimitBurst=2\n"
            f"[Service]\nType=oneshot\nExecStartPre={gate}\n"
            f"ExecStart={base} body --root {root} --job {name}\n"
            f"Restart={restart}\nRestartSec=0.1s\nTimeoutStartSec=0.5s\n"
        )
    for name, value in files.items():
        (unitdir / name).write_text(value)
    evidence = {
        "root": str(root),
        "units": list(files),
        "original_job_units": policies,
        "host_clock_control": False,
        "root_sandbox_test": False,
        "shortened_job_timeout_seconds": 0.5,
        "guarded_jobs": JOBS,
    }
    jobs = [jp + n + ".service" for n in JOBS]

    def lines(name):
        path = root / (name + ".jsonl")
        return len(path.read_text().splitlines()) if path.exists() else 0

    try:
        command("systemctl", "--user", "link", "--runtime", *[str(unitdir / n) for n in files])
        command("systemctl", "--user", "daemon-reload")
        command("systemctl", "--user", "start", "--no-block", *jobs)
        command("systemctl", "--user", "start", "--no-block", prep)
        fixture.wait_for(lambda: state(wait_unit)["ActiveState"] == "activating")
        time.sleep(1.2)  # Deliberately exceeds each job's 0.5-second startup timeout.
        evidence["before_recovery"] = {n: state(u) for n, u in zip(JOBS, jobs)}
        assert all(lines(n) == 0 for n in JOBS)
        for value in evidence["before_recovery"].values():
            assert value["MainPID"] == value["ControlPID"] == value["NRestarts"] == "0"
            assert value["ExecMainStartTimestampMonotonic"] == "0"
        assert not (root / "fake-step-calls.jsonl").exists()
        boot.rt.atomic_json(
            root / "fake-clock.json",
            {"correction": 41, "synchronized": True, "invocation": "first"},
        )
        fixture.wait_for(lambda: all(lines(n) == 1 for n in JOBS), limit=30)
        fixture.wait_for(lambda: all(state(u)["ActiveState"] == "inactive" for u in jobs))
        evidence["after_recovery"] = {n: state(u) for n, u in zip(JOBS, jobs)}
        assert all(
            v["Result"] == "success" and v["NRestarts"] == "0"
            for v in evidence["after_recovery"].values()
        )
        assert len((root / "fake-step-calls.jsonl").read_text().splitlines()) == 1

        # A later invocation must not reuse a successful barrier as perpetual readiness.
        boot.rt.atomic_json(
            root / "fake-clock.json",
            {"correction": 41, "synchronized": False, "invocation": "restarted"},
        )
        command("systemctl", "--user", "start", "--no-block", *jobs)
        fixture.wait_for(lambda: state(wait_unit)["ActiveState"] == "activating")
        time.sleep(1.2)
        assert all(lines(n) == 1 for n in JOBS)
        command("systemctl", "--user", "restart", prep)
        assert len((root / "fake-step-calls.jsonl").read_text().splitlines()) == 1
        boot.rt.atomic_json(
            root / "fake-clock.json",
            {"correction": 0, "synchronized": True, "invocation": "restarted"},
        )
        fixture.wait_for(lambda: all(lines(n) == 2 for n in JOBS), limit=30)
        fixture.wait_for(lambda: all(state(u)["ActiveState"] == "inactive" for u in jobs))
        evidence["second_invocation"] = {n: state(u) for n, u in zip(JOBS, jobs)}

        # Actual work failure still obeys the bounded restart policy.
        (root / "fail-backup").write_text("fixture")
        # Clear only this fixture's prior activation count before a distinct failure test.
        reset = command("systemctl", "--user", "reset-failed", jp + "backup.service", check=False)
        # A completed unreferenced oneshot may already be garbage-collected by
        # systemd, which also clears its start-limit counter. That is not a failure.
        assert reset.returncode == 0 or "not loaded" in reset.stderr
        evidence["fixture_reset"] = {"returncode": reset.returncode, "stderr": reset.stderr}
        command("systemctl", "--user", "start", "--no-block", jp + "backup.service")

        def limit_reached():
            value = state(jp + "backup.service")
            return value["ActiveState"] == "failed" and int(value["NRestarts"]) >= 2

        fixture.wait_for(limit_reached)
        time.sleep(0.4)
        evidence["business_failure"] = state(jp + "backup.service")
        assert lines("backup") == 4  # Two successes plus two bounded failed body attempts.
        evidence.update(passed=True, synthetic_steps=1, counts={n: lines(n) for n in JOBS})
    finally:
        evidence["journal"] = command(
            "journalctl",
            "--user",
            *sum((["-u", u] for u in files), []),
            "--no-pager",
            "-n",
            "500",
            check=False,
        ).stdout
        for unit in [*jobs, wait_unit, prep]:
            command("systemctl", "--user", "stop", unit, check=False)
            command("systemctl", "--user", "reset-failed", unit, check=False)
        command("systemctl", "--user", "disable", "--runtime", *files, check=False)
        command("systemctl", "--user", "daemon-reload", check=False)
        output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in evidence.items()
                if k in {"root", "passed", "synthetic_steps", "counts"}
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["test", "bootstrap", "wait", "gate", "body"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--job")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "test":
        test(args.output)
    else:
        raise SystemExit(worker(args.action, args.root, args.job))
