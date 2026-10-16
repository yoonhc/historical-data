# ruff: noqa: F811 -- imported pytest fixtures
import fcntl
import json
from types import SimpleNamespace

import pytest
import deployment as d
import handoff as h
import recover_request as r
from test_handoff import lab, executor, assert_final, Crash  # noqa: F401
from test_recovery import recovery  # noqa: F401


@pytest.mark.parametrize(
    "cut", ["phase:reconciled", "pending-before-mask", "transaction-staged", "phase:committed"]
)
@pytest.mark.parametrize("direction", ["apply", "rollback"])
def test_exact_recovery_then_interrupted_transition_preserves_new_receipt(
    lab, recovery, cut, direction
):
    x = recovery
    lab.requests = {
        "oi-quality": {"requires_reconciliation": True, "sha256": x.plan["request_sha256"]}
    }

    def reconcile():
        assert lab.fs.json(h.t.LEDGER) == lab.plan["inner"]["ledger_before"]
        assert all(
            lab.fs.node(p) == op["before"] for p, op in lab.plan["inner"]["operations"].items()
        )
        assert all(lab.m.props[n]["ActiveState"] == "inactive" for n in h.c.TIMERS)
        with lab.fs.path("/var/lib/marketdata-clock/coordinator.lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            receipt = x.run()
        lab.requests["oi-quality"] = {
            "requires_reconciliation": False,
            "sha256": receipt["after_sha256"],
        }
        return receipt

    def fault(point):
        if point == cut:
            raise Crash(point)

    with pytest.raises(Crash):
        executor(lab, reconciliation=reconcile, fault=fault).run()
    completed = r.read(x.plan["request_path"])
    assert json.loads(completed)["state"] == "complete"
    executor(lab, reconciliation=reconcile).run(direction)
    assert_final(lab, "after" if direction == "apply" else "before")
    assert r.read(x.plan["request_path"]) == completed
    assert x.monitors == 1


def test_failed_recovery_never_stages_configuration_or_starts_work(lab, recovery):
    recovery.fail = "record"
    with pytest.raises(Exception):
        executor(lab, reconciliation=recovery.run).run()
    assert lab.fs.json(h.t.LEDGER) == lab.plan["inner"]["ledger_before"]
    assert all(lab.fs.node(p) == op["before"] for p, op in lab.plan["inner"]["operations"].items())
    assert r.read(recovery.plan["request_path"]) == recovery.original
    assert not any(action == "start" for action, _ in lab.m.commands)
    recovery.fail = None
    executor(lab, reconciliation=recovery.run).run()
    assert_final(lab, "after")


def test_rendered_change_is_exact_path_substitution_and_guard_linkage():
    plan = d.load_checked()
    frozen = json.loads((d.HERE / "candidate-inputs.json").read_bytes())
    old = "/opt/marketdata/releases/" + frozen["prior_release_id"]
    new = "/opt/marketdata/releases/" + frozen["release_id"]
    for name in (*h.c.SERVICES, h.NEW_WAITER):
        op = plan["inner"]["operations"][h.c.SYSTEM_ROOT + name]
        assert op["after"]["text"] == op["before"]["text"].replace(old, new)
    assert frozen["runtime_changes"] == ["policy/request_contract.py", "policy/run_job.py"]
    before, after = (
        json.loads(plan["inner"]["operations"][h.t.PROFILE][side]["text"])
        for side in ("before", "after")
    )
    assert {k: v for k, v in before.items() if k != "guard_files"} == {
        k: v for k, v in after.items() if k != "guard_files"
    }
    assert set(before["guard_files"]) == set(after["guard_files"])


def test_rollback_observes_prior_t16_policy_not_legacy_strict_gate(monkeypatch):
    plan = d.load_checked()
    calls = []

    def run(command, **kw):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(d.subprocess, "run", run)
    assert d.observe(plan, "rollback")["ready"] is True
    command = " ".join(calls[0])
    assert "544c61aca49f1355" in command and "readiness.py" in command
    assert "clock-control" not in command and "strict" not in command


def test_native_recovery_callback_holds_shared_coordinator_only(tmp_path, monkeypatch):
    plan = d.load_checked()
    lock = tmp_path / "coordinator.lock"
    lock.touch()
    original_open = d.os.open

    def open_file(path, *args, **kw):
        if str(path) == "/var/lib/marketdata-clock/coordinator.lock":
            path = lock
        return original_open(path, *args, **kw)

    monkeypatch.setattr(d.os, "open", open_file)

    def run(command, **kw):
        with lock.open("rb") as shared:
            fcntl.flock(shared, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with lock.open("rb") as exclusive:
            with pytest.raises(BlockingIOError):
                fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert "recover_request.py" in " ".join(command)
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                {
                    "plan_id": plan["recovery_plan_id"],
                    "request_state": "complete",
                    "work_invocations": 0,
                }
            ),
        )

    monkeypatch.setattr(d.subprocess, "run", run)
    manager = SimpleNamespace(
        states=lambda *args: {"marketdata-oi-quality.service": {"classification": "idle"}}
    )
    assert d.reconcile(plan, manager)["work_invocations"] == 0
    with lock.open("rb") as unlocked:
        fcntl.flock(unlocked, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("state", ["running", "pending", "unknown"])
def test_native_recovery_never_interrupts_running_or_queued_job(monkeypatch, state):
    plan = d.load_checked()
    monkeypatch.setattr(
        d.subprocess, "run", lambda *args, **kw: pytest.fail("must not start recovery")
    )
    manager = SimpleNamespace(
        states=lambda *args: {"marketdata-oi-quality.service": {"classification": state}}
    )
    with pytest.raises(h.c.Hold, match="running or queued"):
        d.reconcile(plan, manager)
