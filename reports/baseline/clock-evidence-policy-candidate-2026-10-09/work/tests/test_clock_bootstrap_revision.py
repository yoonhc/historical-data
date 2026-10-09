"""Read-only wait barriers and bootstrap/job separation; no operating services."""

import importlib.util
import json
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "boot_revision", ROOT / "integrations/runtime/clock_gate.py"
)
boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot)


def test_wait_does_not_consume_job_failures_and_recovers_after_long_delay():
    stop, events, checks = threading.Event(), [], []
    now = [0.0]

    def check():
        checks.append(now[0])
        if len(checks) < 4:
            raise boot.Hold("not yet synchronized")
        return {"ready": True}

    stop.wait = lambda _: now.__setitem__(0, now[0] + 400)
    assert boot.wait_for_gate(check, stop, monotonic=lambda: now[0], emit=events.append) == 0
    assert [e["phase"] for e in events] == ["waiting"] * 3 + ["released"]
    assert events[2]["long_wait"] and events[-1]["wait_seconds"] == 1200


@pytest.mark.parametrize(
    "error", [boot.Hold("lock"), OSError("state unreadable"), ValueError("bad profile")]
)
def test_wait_cancellation_never_reports_ready(error):
    stop, events = threading.Event(), []
    stop.wait = lambda _: stop.set()

    def check():
        raise error

    assert boot.wait_for_gate(check, stop, emit=events.append) == 75
    assert [e["phase"] for e in events] == ["waiting", "cancelled"]
    assert all(not e["ready"] for e in events)


def test_wait_releases_shared_lock_while_waiting(tmp_path):
    ledger = boot.Ledger(tmp_path)
    p = {}
    with ledger.lock():
        boot.enroll(p, ledger, "fixture")
    stop = threading.Event()
    entered = []

    def pause(_):
        with ledger.lock():
            entered.append("coordinator can acquire exclusive lock")
        stop.set()

    stop.wait = pause
    assert (
        boot.wait_for_gate(
            lambda: boot.check_gate(p, {}, ledger=ledger, current_boot="fixture"),
            stop,
            emit=lambda _: None,
        )
        == 75
    )
    assert entered


@pytest.mark.parametrize(
    "fault", [None, "wait_command", "wait_extra", "wait_post", "dependency", "job_active"]
)
def test_coordinator_accepts_verified_waiter_but_never_running_business(fault):
    p = {
        "guard_files": {},
        "system_collectors": [],
        "wait_unit": "marketdata-clock-ready.service",
        "wait_command": "/safe/python -I gate.py wait",
        "guard_command": "/safe/python -I gate.py check",
        "user_units": {
            "marketdata-clock-ready.service": "waiter",
            "marketdata-daily.service": "guarded",
        },
    }
    host = boot.Host(p, {})

    def user(*args):
        if args[0] == "list-unit-files":
            return "\n".join(p["user_units"])
        waiter = args[1] == p["wait_unit"]
        f = {
            "LoadState": "loaded",
            "ActiveState": "activating" if waiter else "inactive",
            "MainPID": "111" if waiter else "0",
            "ControlPID": "0",
            "ExecStart": f"{{ argv[]={p['wait_command']} ; }}" if waiter else "",
            "ExecStartPre": "" if waiter else f"{{ argv[]={p['guard_command']} ; }}",
            "After": p["wait_unit"],
            "Requires": p["wait_unit"],
        }
        if waiter and fault == "wait_command":
            f["ExecStart"] = "{ argv[]=/bin/sh unreviewed ; }"
        if waiter and fault == "wait_extra":
            f["ExecStart"] += " { argv[]=/bin/sh unreviewed ; }"
        if waiter and fault == "wait_post":
            f["ExecStartPost"] = "unreviewed"
        if not waiter and fault == "dependency":
            f["Requires"] = ""
        if not waiter and fault == "job_active":
            f["ActiveState"], f["MainPID"] = "active", "222"
        return "\n".join(f"{k}={v}" for k, v in f.items())

    host.user_command = user
    if fault:
        with pytest.raises(boot.Hold):
            host.audit_quiescent()
    else:
        host.audit_quiescent()


def test_render_preserves_business_restart_timeout_and_grants_only_socket_directory():
    spec = importlib.util.spec_from_file_location(
        "render", ROOT / "integrations/runtime/render_bootstrap.py"
    )
    render = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render)
    runtime = json.loads((ROOT / "integrations/runtime/profile.example.json").read_text())
    inventory = json.loads((ROOT / "integrations/runtime/user-unit-inventory.json").read_text())
    files, p = render.render(runtime, json.dumps(runtime).encode(), inventory)
    for name, mode in inventory.items():
        if mode != "guarded":
            continue
        unit = files[f"user/{name}.d/90-marketdata-clock.conf"]
        assert f"After={p['wait_unit']}" in unit and f"Requires={p['wait_unit']}" in unit
        assert not any(
            k in unit for k in ["Restart=", "StartLimit", "TimeoutStartSec=", "SuccessExitStatus="]
        )
    wait = files[f"user/{p['wait_unit']}"]
    assert "TimeoutStartSec=infinity" in wait and "RemainAfterExit=no" in wait
    root = files["system/marketdata-clock-bootstrap.service"]
    assert "ReadWritePaths=/run/chrony\n" in root and "CAP_SYS_TIME" not in root
