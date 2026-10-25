"""Only empty-job parsing and the existing interrupted-operation connection."""

import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


r = load("repair_under_test", HERE / "repair_resume.py")
n, op, c = r.n, r.op, r.c


def idle_host(monkeypatch, raw):
    host = object.__new__(n.Host)
    host.show = lambda names: {
        name: {"ActiveState": "inactive", "MainPID": "0", "ControlPID": "0", "Job": ""}
        for name in names
    }
    monkeypatch.setattr(n, "run", lambda args: raw)
    return host


@pytest.mark.parametrize("raw", ["", "\n", "[]\n", '[{"unit":"unrelated.service"}]'])
def test_successful_empty_or_unrelated_jobs(monkeypatch, raw):
    idle_host(monkeypatch, raw).idle((c.T05_SERVICE,))


@pytest.mark.parametrize("raw", ["not JSON", "{}", "null", "[{}]", '[{"unit":1}]'])
def test_nonempty_invalid_response_is_not_empty(monkeypatch, raw):
    with pytest.raises((op.Hold, json.JSONDecodeError)):
        idle_host(monkeypatch, raw).idle((c.T05_SERVICE,))


def test_exact_plan_t05_pending_still_blocks(monkeypatch):
    host = idle_host(monkeypatch, '[{"unit":"marketdata-checkpoint-retirement-apply@abc.service"}]')
    with pytest.raises(op.Hold, match="T05 job is still pending"):
        host.idle((c.T05_SERVICE,))


def test_failed_command_is_not_empty_success(monkeypatch):
    host = idle_host(monkeypatch, "")

    def denied(args):
        raise op.Hold("Command failed: access denied")

    monkeypatch.setattr(n, "run", denied)
    with pytest.raises(op.Hold, match="Command failed"):
        host.idle((c.T05_SERVICE,))


def test_live_oneshot_remains_nonidle(monkeypatch):
    host = idle_host(monkeypatch, "")
    host.show = lambda names: {c.T05_SERVICE: {"ActiveState": "activating", "MainPID": "123", "ControlPID": "0", "Job": "1"}}
    with pytest.raises(op.Hold, match="not idle"):
        host.idle((c.T05_SERVICE,))


def test_original_adapter_reproduces_empty_failure(monkeypatch):
    old = load("unchanged_old_native", HERE.parent / "t18-archive-deploy-2026-10-25/native_host.py")
    host = object.__new__(old.Host)
    host.show = lambda names: {c.T05_SERVICE: {"ActiveState": "inactive", "MainPID": "0", "ControlPID": "0", "Job": ""}}
    monkeypatch.setattr(old, "run", lambda args: "")
    with pytest.raises(json.JSONDecodeError):
        host.idle((c.T05_SERVICE,))


@pytest.mark.parametrize("mode", ["exact", "changed_state", "transaction_exists"])
def test_initial_recovery_boundary(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(n, "safe", lambda path: path)
    (tmp_path / "state.json").write_bytes(b"original state")
    recovery = {"initial_records": {"state.json": c.file_sha(tmp_path / "state.json")}}
    if mode == "changed_state":
        (tmp_path / "state.json").write_bytes(b"other state")
    elif mode == "transaction_exists":
        (tmp_path / "transaction.json").write_bytes(b"{}")
    if mode == "exact":
        r.initial_boundary(recovery, tmp_path)
    else:
        with pytest.raises(op.Hold):
            r.initial_boundary(recovery, tmp_path)


@pytest.mark.parametrize("direction", ["apply", "rollback"])
def test_original_authority_and_held_timers_recovery(tmp_path, direction):
    fixture = load("reused_operation_fixture", HERE.parent / "t18-archive-deploy-2026-10-22/tests/test_operation.py")
    fixture.PREPARED = json.loads((HERE / "prepared-plan.json").read_bytes())
    plan = json.loads((HERE / "operation-plan.json").read_bytes())
    host = fixture.Fixture(tmp_path)
    with pytest.raises(fixture.Crash):
        op.Operation(plan, host, fault=fixture.crash_at("timers_held")).run("apply")
    authority = host.raw("/records/authority.json")
    host.write("/records/failed-attempt.json", b"original JSONDecodeError\n")
    result = op.Operation(plan, host).run(direction)
    assert host.raw("/records/authority.json") == authority
    assert host.events.count("authority_timestamp") == 1
    assert host.raw("/records/failed-attempt.json") == b"original JSONDecodeError\n"
    assert host.raw("/data/old-receipt.json") == b"old committed receipt\n"
    if direction == "apply":
        assert result["phase"] == "activated"
        assert len(host.started) == 9 and host.started.count(c.TIMER) == 1
        op.Operation(plan, host).run("apply")
        assert len(host.started) == 9
    else:
        assert result["phase"] == "rolled_back_archiver_held"
        assert len(host.started) == 8 and c.TIMER not in host.started
        assert host.raw(c.GRANT) == fixture.PREPARED["recurring_before"]["text"].encode()
        assert json.loads(host.raw(c.LEDGER)) == fixture.PREPARED["ledger_before"]
        with pytest.raises(op.Hold, match="cannot reactivate"):
            op.Operation(plan, host).run("apply")
