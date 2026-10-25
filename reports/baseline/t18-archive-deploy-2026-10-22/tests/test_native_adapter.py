import fcntl
import importlib.util
import json
import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_adapter_test", HERE / "native_host.py")
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)
op, c = n.op, n.c


def host():
    h = object.__new__(n.Host)
    h.locked = False
    return h


def row(**values):
    return {"ActiveState": "inactive", "MainPID": "0", "ControlPID": "0", "Job": "", **values}


def test_timer_without_pid_properties_is_idle(monkeypatch):
    h = host()
    h.show = lambda names: {c.TIMER: {"ActiveState": "inactive", "Job": ""}}
    monkeypatch.setattr(n, "run", lambda args: pytest.fail("No T05 query in archiver-only stop"))
    h.idle((), (c.TIMER,))


@pytest.mark.parametrize(
    "state",
    [
        row(ActiveState="activating", MainPID="123"),
        row(ActiveState="activating", SubState="auto-restart"),
        row(ControlPID="123"),
        row(Job="81"),
        row(ActiveState="active"),
    ],
)
def test_active_body_or_restart_or_job_cannot_be_drained(state):
    h = host()
    h.show = lambda names: {c.SERVICE: state}
    with pytest.raises(op.Hold, match="not idle"):
        h.idle((c.SERVICE,))


def test_manual_t05_job_blocks_connection_transition(monkeypatch):
    h = host()
    h.show = lambda names: {c.T05_SERVICE: row()}
    monkeypatch.setattr(
        n,
        "run",
        lambda args: json.dumps(
            [{"unit": "marketdata-checkpoint-retirement-apply@123.service", "type": "start"}]
        ),
    )
    with pytest.raises(op.Hold, match="T05 job"):
        h.idle((c.T05_SERVICE,))


def test_all_existing_locks_are_held_together_and_released(tmp_path):
    h = host()
    paths = [tmp_path / str(i) for i in range(10)]
    for p in paths:
        p.touch()
    h.transition_lock_paths = lambda: paths
    with h.transaction_locks():
        assert h.locked
        for path in paths:
            with path.open("rb") as f:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not h.locked
    for path in paths:
        with path.open("rb") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_contended_coordinator_releases_previous_request_locks(tmp_path):
    h = host()
    first, second = tmp_path / "request.lock", tmp_path / "coordinator.lock"
    first.touch()
    second.touch()
    h.transition_lock_paths = lambda: [first, second]
    with second.open("rb") as occupied:
        fcntl.flock(occupied, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(op.Hold, match="busy"):
            with h.transaction_locks():
                pytest.fail("Should not enter configuration")
    with first.open("rb") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not h.locked


def test_existing_symlink_lock_is_rejected(tmp_path):
    h = host()
    destination = tmp_path / "actual"
    destination.touch()
    link = tmp_path / "request.lock"
    link.symlink_to(destination)
    h.transition_lock_paths = lambda: [link]
    with pytest.raises(op.Hold, match="unsafe"):
        with h.transaction_locks():
            pytest.fail("Unexpected entry")


def test_mixed_incomplete_request_never_gets_rewritten(tmp_path, monkeypatch):
    h = host()
    h.locked = True
    monkeypatch.setattr(c, "ROOT", tmp_path)
    originals = {}
    for job in c.JOBS:
        path = tmp_path / "state/scheduled-clock-policy" / job / "request.json"
        path.parent.mkdir(parents=True)
        raw = c.encode(
            {
                "state": "pending" if job == "backup" else "complete",
                "identity": job,
                "pins": {"day": "2026-10-21"},
            }
        )
        path.write_bytes(raw)
        originals[path] = raw
    h.save = lambda *a, **k: pytest.fail("Do not claim a complete inventory")
    with pytest.raises(op.Hold, match="backup"):
        h.terminal_requests()
    assert originals == {p: p.read_bytes() for p in originals}


def test_no_business_service_control_or_reset_is_exposed(monkeypatch):
    h = host()
    monkeypatch.setattr(n, "run", lambda *a, **k: pytest.fail("Out-of-scope manager command"))
    for name in (c.SERVICE, c.T05_SERVICE, "chrony.service"):
        with pytest.raises(op.Hold):
            h.start_timer(name)
        with pytest.raises(op.Hold):
            h.stop_timers((name,))


def test_changed_timer_dependency_is_rejected_before_any_stop(monkeypatch):
    h = host()
    h.timers = lambda names: {
        c.TIMER: {"ActiveState": "active", "Job": "", "RequiredBy": "unrelated.service"}
    }
    monkeypatch.setattr(
        n, "run", lambda *a, **k: pytest.fail("Dependency must be checked before control")
    )
    with pytest.raises(op.Hold, match="dependencies"):
        h.stop_timers((c.TIMER,))


def test_growth_failure_does_not_change_capacity_policy(monkeypatch):
    h = host()
    prepared = json.loads((HERE / "prepared-plan.json").read_bytes())
    h.p = prepared
    h.plan = {
        "additional_resume_headroom_bytes": 1024**3,
        "checkpoint_manifest_paths": sorted(
            str(p)
            for p in Path("/mnt/backup/marketdata-historical/automatic").glob("*/*/backup.json")
        ),
    }
    h.save = lambda name, result: None
    original_reserve = prepared["capacity"]["required_collection_reserve_bytes"]
    monkeypatch.setattr(
        n.os, "statvfs", lambda path: os.statvfs_result((4096, 4096, 1, 1, 1, 1, 1, 1, 0, 255))
    )
    with pytest.raises(op.Hold, match="headroom"):
        h.capacity()
    assert h.p["capacity"]["required_collection_reserve_bytes"] == original_reserve
