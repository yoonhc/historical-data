"""No host clock/service mutations. Persistent files, processes and locks are real."""

import copy
import importlib.util
import json
import multiprocessing
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("boot", ROOT / "integrations/runtime/clock_gate.py")
boot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot)


class FakeHost:
    def __init__(self, runtime, correction=41.0):
        self.runtime = runtime
        self.correction = correction
        self.ntp = 1800000000.0
        self.ref_age = 1
        self.provider = {"pid": 123, "invocation_id": "first"}
        self.synced = True
        self.calls = []
        self.busy = False
        self.rtc_ok = True

    def rtc_check(self):
        if not self.rtc_ok:
            raise boot.Hold("RTC not confirmed")

    def audit_quiescent(self):
        if self.busy:
            raise boot.Hold("Job is not quiescent")

    def observe(self):
        t = boot.rt.tracking(
            f"CB00710F,203.0.113.15,3,{self.ntp - self.ref_age},{self.correction},0,0,0,0,0.1,0.01,0.02,64,"
            + ("Normal" if self.synced else "Not synchronised")
        )
        wall = self.ntp - self.correction
        result = boot.rt.assess_clock(t, self.runtime["policy"], wall)
        return {
            **result,
            "provider": copy.deepcopy(self.provider),
            "sample": {"wall_ns": int(wall * 1e9)},
        }

    def step(self, expected):
        assert expected == self.provider
        self.calls.append("makestep")
        self.correction = 0


@pytest.fixture
def setup(tmp_path):
    runtime = boot.rt.load_profile(ROOT / "integrations/runtime/profile.example.json")
    p = {
        "state_dir": str(tmp_path),
        "policy": {
            "step_above_seconds": 0.1,
            "max_initial_correction_seconds": 300.0,
            "source_age_seconds": 30.0,
            "long_wait_seconds": 600,
            "attempt_seconds": 120,
            "poll_seconds": 5,
        },
    }
    ledger = boot.Ledger(tmp_path)
    boot.enroll(p, ledger, "installation-boot")
    host = FakeHost(runtime)
    return p, runtime, ledger, host


def go(setup, now=100):
    p, runtime, ledger, host = setup
    with ledger.lock():
        return boot.attempt(p, runtime, ledger, host, current_boot="next-boot", boottime=now)


@pytest.mark.parametrize("offset", [41, -41, 0.101, -0.101, 0.1, -0.1, 0])
def test_large_initial_offset_uses_source_then_step_then_readiness(setup, offset):
    p, runtime, ledger, host = setup
    host.correction = offset
    if abs(offset) > 0.1:
        assert not host.observe()["ready"]  # Using the old full gate first would stall.
    assert go(setup)["ready"]
    assert host.calls == (["makestep"] if abs(offset) > 0.1 else [])
    state = ledger.read()
    assert state["phase"] == "closed"
    assert boot.check_gate(
        p, runtime, ledger=ledger, checker=lambda _: host.observe(), current_boot="next-boot"
    )["ready"]


@pytest.mark.parametrize("fault", ["unsynced", "stale", "too_large", "rtc", "busy"])
def test_never_steps_without_source_rtc_or_quiescence(setup, fault):
    p, runtime, ledger, host = setup
    if fault == "unsynced":
        host.synced = False
    if fault == "stale":
        host.ref_age = 31
    if fault == "too_large":
        host.correction = 32400
    if fault == "rtc":
        host.rtc_ok = False
    if fault == "busy":
        host.busy = True
    if fault in {"rtc", "busy"}:
        with pytest.raises(boot.Hold):
            go(setup)
    else:
        assert not go(setup)["ready"]
    assert host.calls == []
    with pytest.raises(boot.Hold):
        boot.check_gate(
            p, runtime, ledger=ledger, checker=lambda _: host.observe(), current_boot="next-boot"
        )


def test_wait_recovers_and_long_wait_survives_service_retries(setup):
    _, _, ledger, host = setup
    host.synced = False
    assert not go(setup, now=10)["long_wait"]
    assert go(setup, now=611)["long_wait"]
    assert ledger.read()["last_wait"]["wait_seconds"] == 601
    host.synced = True
    assert go(setup, now=612)["ready"]
    assert host.calls == ["makestep"]


def test_installation_boot_cannot_step_or_be_reenrolled(setup):
    p, runtime, ledger, host = setup
    with pytest.raises(boot.Hold, match="Installation boot"):
        boot.attempt(p, runtime, ledger, host, current_boot="installation-boot", boottime=100)
    with pytest.raises(boot.Hold, match="overwritten"):
        boot.enroll(p, ledger, "another")
    assert host.calls == []


@pytest.mark.parametrize("fault", ["missing", "corrupt", "profile"])
def test_broken_or_foreign_state_does_not_rearm(setup, fault):
    p, _, ledger, host = setup
    if fault == "missing":
        ledger.path.unlink()
    if fault == "corrupt":
        ledger.path.write_text('{"phase":"closed"}')
    if fault == "profile":
        p["policy"]["step_above_seconds"] = 0.05
    with pytest.raises(boot.Hold):
        go(setup)
    assert not host.calls


def test_closed_window_stays_closed_even_with_no_jobs_and_new_daemon(setup):
    p, runtime, ledger, host = setup
    assert go(setup)["ready"]
    original = ledger.path.read_bytes()
    host.provider = {"pid": 456, "invocation_id": "restarted"}
    host.correction = -41
    assert go(setup)["step_reopened"] is False
    assert host.calls == ["makestep"] and ledger.path.read_bytes() == original
    with pytest.raises(boot.Hold, match="Current clock"):
        boot.check_gate(
            p, runtime, ledger=ledger, checker=lambda _: host.observe(), current_boot="next-boot"
        )
    host.correction = 0
    assert boot.check_gate(
        p, runtime, ledger=ledger, checker=lambda _: host.observe(), current_boot="next-boot"
    )["ready"]
    with pytest.raises(boot.Hold, match="This boot"):
        boot.check_gate(
            p,
            runtime,
            ledger=ledger,
            checker=lambda _: host.observe(),
            current_boot="different-boot",
        )


def crash_during_command(p, runtime, root, after):
    ledger = boot.Ledger(root)
    host = FakeHost(runtime)

    def crash(_):
        if after:
            (Path(root) / "simulated-command.json").write_text('{"correction":0}')
        os.kill(os.getpid(), signal.SIGKILL)

    host.step = crash
    with ledger.lock():
        boot.attempt(p, runtime, ledger, host, current_boot="next-boot", boottime=100)


@pytest.mark.parametrize("after", [False, True])
def test_sigkill_at_step_boundary_is_not_replayed(setup, after):
    p, runtime, ledger, host = setup
    child = multiprocessing.get_context("spawn").Process(
        target=crash_during_command, args=(p, runtime, str(ledger.root), after)
    )
    child.start()
    child.join(10)
    assert child.exitcode == -signal.SIGKILL
    assert ledger.read()["phase"] == "step_intent"
    assert ledger.read()["step_result"] == "unknown"
    host.correction = 0 if after else 41
    result = go(setup)
    assert result["ready"] is after
    assert host.calls == []
    if not after:
        assert result["needs_review"]
    assert ledger.read()["step_result"] == "unknown"


@pytest.mark.parametrize("stage", ["intent", "event", "close"])
def test_storage_failure_cannot_publish_unearned_readiness(setup, monkeypatch, stage):
    p, runtime, ledger, host = setup
    original_save, original_event = ledger.save, ledger.event

    def save(s):
        if (stage == "intent" and s["phase"] == "step_intent") or (
            stage == "close" and s["phase"] == "closed"
        ):
            raise OSError("disk full")
        original_save(s)

    def event(k, **kw):
        if stage == "event" and k == "step_intent":
            raise OSError("disk full")
        return original_event(k, **kw)

    monkeypatch.setattr(ledger, "save", save)
    monkeypatch.setattr(ledger, "event", event)
    with pytest.raises(OSError):
        go(setup)
    assert host.calls == (["makestep"] if stage == "close" else [])
    with pytest.raises(boot.Hold):
        boot.check_gate(
            p, runtime, ledger=ledger, checker=lambda _: host.observe(), current_boot="next-boot"
        )


def test_real_flock_excludes_another_bootstrap(setup):
    p, runtime, ledger, host = setup
    assert go(setup)["ready"]
    with ledger.lock():
        with pytest.raises(boot.Hold, match="owns"):
            with boot.Ledger(ledger.root).lock():
                pytest.fail("lock bypass")
        with pytest.raises(boot.Hold, match="owns"):
            boot.check_gate(
                p,
                runtime,
                ledger=ledger,
                checker=lambda _: host.observe(),
                current_boot="next-boot",
            )


def test_command_has_no_future_step_window_and_detects_provider_change(setup, monkeypatch):
    p, runtime, _, _ = setup
    p["chrony_socket"] = "/run/chrony/chronyd.sock"
    identity = {"pid": 1, "invocation_id": "a"}
    monkeypatch.setattr(boot.rt, "provider_identity", lambda _: identity)
    calls = []
    host = boot.Host(p, runtime, runner=lambda argv: calls.append(argv) or "200 OK")
    host.step(identity)
    assert calls == [
        [runtime["chronyc"], "-n", "-h", p["chrony_socket"], "-m", "retries 0", "makestep"]
    ]
    with pytest.raises(boot.Hold, match="invocation"):
        host.step({**identity, "invocation_id": "old"})
    assert len(calls) == 1


def test_untrusted_input_is_refused_by_real_cli(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text("{}")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "integrations/runtime/clock_gate.py"),
            "bootstrap",
            "--profile",
            str(p),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 75
    assert "Untrusted" in result.stderr


def test_inventory_guards_and_persistent_masks(setup, monkeypatch):
    p, runtime, _, _ = setup
    p.update(
        user_units={
            "marketdata-oi.service": "guarded",
            "marketdata-oi.timer": "timer",
            "marketdata-depth.service": "masked",
            "marketdata-checkpoint-retirement.timer": "masked",
        },
        guard_files={},
        guard_command="/usr/bin/python3 -I /opt/gate.py check",
        wait_unit="marketdata-clock-ready.service",
        system_collectors=["marketdata-collector@depth.service"],
    )
    state = {"bad": None}

    def runner(args):
        assert "systemctl" in args[0]
        return "ActiveState=inactive\nMainPID=0\nControlPID=0\n"

    host = boot.Host(p, runtime, runner=runner)

    def user_command(*args):
        if args[0] == "list-unit-files":
            return "\n".join(f"{n} disabled" for n in p["user_units"]) + (
                "\nmarketdata-unknown.service enabled" if state["bad"] == "unknown" else ""
            )
        name = args[1]
        mode = p["user_units"][name]
        f = {
            "LoadState": "masked" if mode == "masked" else "loaded",
            "ActiveState": "inactive",
            "MainPID": "0",
            "ControlPID": "0",
            "UnitFileState": "masked" if mode == "masked" else "static",
            "ExecStartPre": f"{{ argv[]={p['guard_command']} ; }}",
            "Unit": "marketdata-oi.service",
            "After": p["wait_unit"],
            "Requires": p["wait_unit"],
        }
        if state["bad"] == "missing_guard" and mode == "guarded":
            f["ExecStartPre"] = ""
        if state["bad"] == "runtime_mask" and mode == "masked":
            f["UnitFileState"] = "masked-runtime"
        if state["bad"] == "active" and mode == "guarded":
            f["ActiveState"] = "active"
        if state["bad"] == "wrong_timer" and mode == "timer":
            f["Unit"] = "other.service"
        return "\n".join(f"{k}={v}" for k, v in f.items())

    monkeypatch.setattr(host, "user_command", user_command)
    host.audit_quiescent()
    for fault in ["missing_guard", "runtime_mask", "active", "unknown", "wrong_timer"]:
        state["bad"] = fault
        with pytest.raises(boot.Hold):
            host.audit_quiescent()


def test_rendered_units_gate_all_known_jobs_before_clock_wait(setup):
    _, runtime, _, _ = setup
    spec = importlib.util.spec_from_file_location(
        "render_boot", ROOT / "integrations/runtime/render_bootstrap.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    inventory = json.loads((ROOT / "integrations/runtime/user-unit-inventory.json").read_text())
    files, profile = module.render(runtime, json.dumps(runtime).encode(), inventory)
    assert len([m for m in inventory.values() if m == "guarded"]) == 8
    for name, mode in inventory.items():
        if mode == "guarded":
            assert (
                "ExecStartPre=" + profile["guard_command"]
                in files[f"user/{name}.d/90-marketdata-clock.conf"]
            )
    rootunit = files["system/marketdata-clock-bootstrap.service"]
    assert "Before=chrony-wait.service time-sync.target" in rootunit
    assert "After=network-online.target chrony.service user@" in rootunit
    assert "After=time-sync.target" not in rootunit
    assert "rtc-check" in files["system/chrony.service.d/marketdata.conf"]
    assert inventory["marketdata-checkpoint-retirement.timer"] == "masked"
    assert "CAP_SYS_TIME" not in rootunit


@pytest.mark.parametrize(
    "fault", [None, "windows_unconfirmed", "empty_adjtime", "local_adjtime", "local_timedated"]
)
def test_rtc_write_daemon_cannot_start_before_both_os_confirmation(
    setup, tmp_path, monkeypatch, fault
):
    p, runtime, _, _ = setup
    confirmation = tmp_path / "rtc-confirmation.json"
    confirmation.write_text(
        json.dumps(
            {
                "schema": 1,
                "windows_utc_confirmed": fault != "windows_unconfirmed",
                "confirmed_at": "fixture",
                "evidence_note": "isolated test, not actual Windows evidence",
            }
        )
    )
    p["rtc_confirmation"] = str(confirmation)
    original = Path.read_text

    def read(path, *a, **kw):
        if str(path) == "/etc/adjtime":
            return (
                ""
                if fault == "empty_adjtime"
                else ("0\n0\nLOCAL\n" if fault == "local_adjtime" else "0\n0\nUTC\n")
            )
        return original(path, *a, **kw)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(boot, "trusted", lambda _: None)
    calls = []

    def runner(argv):
        calls.append(argv)
        return "yes" if fault == "local_timedated" else "no"

    host = boot.Host(p, runtime, runner=runner)
    if fault:
        with pytest.raises(boot.Hold):
            host.rtc_check()
    else:
        host.rtc_check()
    assert all(argv[0] == "/usr/bin/timedatectl" and argv[1] == "show" for argv in calls)


def test_step_command_failure_is_unknown_and_never_retried(setup):
    _, _, ledger, host = setup

    def timeout(_):
        host.calls.append("ambiguous")
        raise subprocess.TimeoutExpired("fake chronyc", 3)

    host.step = timeout
    with pytest.raises(subprocess.TimeoutExpired):
        go(setup)
    assert ledger.read()["step_result"] == "unknown"
    assert not go(setup)["ready"]
    assert host.calls == ["ambiguous"]


def test_final_publication_failure_can_recover_without_second_step(setup, monkeypatch):
    _, _, ledger, host = setup
    original = ledger.save

    def fail_once(s):
        if s["phase"] == "closed":
            raise OSError("failed publication")
        original(s)

    monkeypatch.setattr(ledger, "save", fail_once)
    with pytest.raises(OSError):
        go(setup)
    monkeypatch.setattr(ledger, "save", original)
    assert go(setup)["ready"]
    assert host.calls == ["makestep"]


def test_source_ready_but_post_step_quality_bad_never_releases_jobs(setup):
    _, _, ledger, host = setup

    def bad_step(_):
        host.calls.append("makestep")
        host.correction = 1

    host.step = bad_step
    assert not go(setup)["ready"]
    assert ledger.read()["phase"] == "step_returned"
    assert not go(setup)["ready"]
    assert host.calls == ["makestep"]


def test_forged_closed_record_without_observation_is_not_a_marker(setup):
    _, _, ledger, host = setup
    s = ledger.read()
    s["phase"] = "closed"
    ledger.save(s)
    with pytest.raises(boot.Hold, match="corrupt"):
        go(setup)
    assert not host.calls
