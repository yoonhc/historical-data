"""A unique transient USER unit and dummy child. No production unit/time changes."""

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
SPEC = importlib.util.spec_from_file_location("runtime", HERE / "marketdata_runtime.py")
rt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rt)


def driver(mode, work):
    profile = rt.load_profile(work / "profile.json")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    def checker(_):
        state = (work / "provider").read_text() if (work / "provider").exists() else "missing"
        return {
            "ready": state == "a",
            "reasons": [] if state == "a" else [state],
            "provider": {"pid": 42, "invocation_id": state},
        }

    evidence = rt.Evidence(profile, "depth")
    try:
        if mode == "pre":
            try:
                rt.wait_ready(
                    profile, evidence, stop, checker=checker, env_check=lambda _: "isolated-test"
                )
                return 0
            except rt.NotReady:
                return 75
        return rt.supervise(
            profile,
            "depth",
            evidence,
            stop,
            checker=checker,
            env_check=lambda _: "isolated-test",
            spawn=lambda *a, **k: subprocess.Popen(
                [sys.executable, "-I", str(HERE / "isolated_service_test.py"), "child", str(work)],
                **k,
            ),
        )
    finally:
        evidence.close()


def child(work):
    (work / "child-started").write_text(str(os.getpid()))
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    while not stopped.wait(0.05):
        (work / "child-heartbeat").write_text(str(time.monotonic_ns()))
    (work / "child-flushed").write_text("SIGINT received; dummy final write completed\n")


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Isolated service condition timeout")


def harness():
    work = Path(tempfile.mkdtemp(prefix="md-runtime-service-test-"))
    p = json.loads((HERE / "profile.example.json").read_text())
    p["evidence_root"] = str(work / "evidence")
    p["policy"].update(
        ready_timeout_seconds=0.1,
        ready_poll_seconds=0.02,
        sample_seconds=0.05,
        provider_seconds=0.05,
        heartbeat_seconds=0.05,
        stop_timeout_seconds=2.0,
    )
    (work / "profile.json").write_text(json.dumps(p))
    unit = f"md-runtime-candidate-test-{uuid.uuid4().hex[:12]}.service"
    control = ["/usr/bin/systemctl", "--user"]
    script = str(HERE / "isolated_service_test.py")
    invocation = [
        "/usr/bin/systemd-run",
        "--user",
        "--no-block",
        f"--unit={unit}",
        "--property=Type=exec",
        "--property=Restart=on-failure",
        "--property=RestartSec=0.2s",
        "--property=StartLimitIntervalSec=0",
        "--property=KillMode=mixed",
        "--property=KillSignal=SIGINT",
        "--property=TimeoutStopSec=5s",
        "--property=NoNewPrivileges=yes",
        f"--property=ExecStartPre={sys.executable} -I {script} pre {work}",
        sys.executable,
        "-I",
        script,
        "run",
        str(work),
    ]
    result = {"work": str(work), "unit": unit, "scope": "transient_user_unit_dummy_collector"}
    try:
        start = subprocess.run(invocation, capture_output=True, text=True)
        if start.returncode:
            raise RuntimeError(start.stderr)

        def restart_count():
            value = subprocess.check_output(
                control + ["show", unit, "--property=NRestarts", "--value"], text=True
            )
            return int(value.strip() or 0)

        wait_for(lambda: restart_count() >= 2)
        assert not (work / "child-started").exists()
        result["pre_failure_retries"] = restart_count()
        result["child_absent_before_ready"] = True
        (work / "provider").write_text("a")
        wait_for(lambda: (work / "child-heartbeat").exists())
        result["started_after_readiness_recovered"] = True
        result["child_pid"] = int((work / "child-started").read_text())
        result["running_properties"] = subprocess.check_output(
            control
            + ["show", unit, "--property=ActiveState,SubState,MainPID,InvocationID,NRestarts"],
            text=True,
        )
        (work / "provider").write_text("restart-b")

        def unhealthy():
            try:
                return rt.status(p, "depth")["record"].get("clock_usable") is False
            except (OSError, ValueError):
                return False

        wait_for(unhealthy)
        os.kill(result["child_pid"], 0)
        result["clock_untrusted_child_still_running"] = True
        subprocess.run(control + ["stop", unit], check=True, timeout=10)
        assert (work / "child-flushed").exists()
        wait_for(lambda: not Path(f"/proc/{result['child_pid']}").exists())
        assert not rt.status(p, "depth")["clock_usable"]
        result["sigint_flushed_no_child_left"] = True
        result["final_properties"] = subprocess.check_output(
            control + ["show", unit, "--property=ActiveState,SubState,Result,ExecMainStatus"],
            text=True,
        )
        result["ok"] = True
    finally:
        subprocess.run(control + ["stop", unit], capture_output=True, timeout=10)
        subprocess.run(control + ["reset-failed", unit], capture_output=True, timeout=10)
    (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if len(sys.argv) == 1:
        harness()
    elif sys.argv[1] == "child":
        child(Path(sys.argv[2]))
    else:
        raise SystemExit(driver(sys.argv[1], Path(sys.argv[2])))
