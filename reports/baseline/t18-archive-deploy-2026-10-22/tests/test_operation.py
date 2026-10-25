"""Real fixture files and old/new records; only systemd/provider is a stand-in."""

import contextlib
import copy
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("op_test", HERE / "operation.py")
op = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op)
c = op.c
PREPARED = json.loads((HERE / "prepared-plan.json").read_bytes())


def plan():
    value = {
        "schema": 1,
        "purpose": op.PURPOSE,
        "drain_seconds": 600,
        "resume_timer": c.TIMER,
        "runtime_timer_holds": list(op.TIMERS),
        "prepared": PREPARED,
    }
    value["operation_id"] = c.sha(c.canonical(value))
    return value


class Crash(BaseException):
    pass


class Fixture:
    def __init__(self, root):
        self.root = root
        self.events = []
        self.locked = False
        self.busy = False
        self.unfinished = False
        self.proposal_wrong = False
        self.started = []
        self.rows = {
            n: {
                "ActiveState": "inactive" if n == c.TIMER else "active",
                "InvocationID": "old",
                "Job": "",
            }
            for n in op.TIMERS
        }
        for path, pair in PREPARED["changes"].items():
            self.write(path, pair["before"]["text"].encode())
        self.write(c.LEDGER, c.encode(PREPARED["ledger_before"]))
        self.write(c.GRANT, PREPARED["recurring_before"]["text"].encode())
        self.write("/data/old-receipt.json", b"old committed receipt\n")

    def path(self, path):
        return self.root / str(path).lstrip("/")

    def raw(self, path):
        return self.path(path).read_bytes()

    def write(self, path, raw):
        p = self.path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)

    def record_path(self, name):
        return "/records/" + name

    def read(self, name):
        p = self.path(self.record_path(name))
        return json.loads(p.read_bytes()) if p.exists() else None

    def save(self, name, value, immutable=False):
        if immutable and self.read(name) is not None:
            op.require(self.read(name) == value, "Immutable fixture receipt differs")
        self.write(self.record_path(name), c.encode(value))

    def now(self):
        self.events.append("authority_timestamp")
        return "2026-10-22T01:00:00+00:00"

    def immutable(self):
        pass

    def timers(self, names):
        return {n: copy.deepcopy(self.rows[n]) for n in names}

    def stop_timers(self, names):
        self.events.append("hold")
        for n in names:
            self.rows[n]["ActiveState"] = "inactive"

    def start_timer(self, name):
        assert not self.locked
        self.started.append(name)
        self.rows[name] = {
            "ActiveState": "active",
            "InvocationID": "new-" + str(len(self.started)),
            "Job": "",
        }

    def drain(self, services, seconds):
        assert not self.locked and seconds == 600
        self.events.append("drain_without_coordinator")
        op.require(not self.busy, "Normal drain pending")

    def idle(self, services, timers):
        assert self.locked
        assert all(self.rows[n]["ActiveState"] == "inactive" for n in timers)

    @contextlib.contextmanager
    def transaction_locks(self):
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def terminal_requests(self):
        assert self.locked
        op.require(not self.unfinished, "Unfinished request")

    def install(self):
        self.events.append("install")

    def reload_and_verify(self, side):
        assert self.locked
        assert json.loads(self.raw(c.LEDGER))["schema"] == 2
        assert json.loads(self.raw(c.GRANT))["enabled"] is False
        assert all(
            self.raw(p) == pair[side]["text"].encode() for p, pair in PREPARED["changes"].items()
        )

    def propose(self):
        assert json.loads(self.raw(c.GRANT))["enabled"] is False
        if self.proposal_wrong:
            return {"scope_id": "different"}
        if self.raw(c.BINDING) == PREPARED["changes"][c.BINDING]["after"]["text"].encode():
            return PREPARED["recurring_proposal_after"]
        return {
            **json.loads(PREPARED["recurring_before"]["text"]),
            "enabled": False,
            "approved_at": None,
            "approval_reference": None,
        }

    def readonly_check(self, side="after"):
        assert not self.locked
        assert json.loads(self.raw(c.LEDGER))["schema"] == 1

    def capacity(self):
        self.events.append("fresh_capacity")

    def verify_activated(self, grant):
        assert self.rows[c.TIMER]["ActiveState"] == "active"
        assert json.loads(self.raw(c.GRANT)) == grant


def crash_at(point):
    def fault(actual):
        if actual == point:
            raise Crash(point)

    return fault


FAULTS = [
    "installed",
    "timer_inventory",
    "timers_held",
    "drained",
    "transaction_intent",
    "pending",
    "grant_disabled",
    *["file:" + p for p in sorted(PREPARED["changes"])],
    "proposal_verified",
    "grant_restored",
    "receipt",
    "ledger_committed",
    "configuration_committed",
    "start_return:" + c.TIMER,
    "activated",
]


@pytest.mark.parametrize("point", FAULTS)
def test_interruption_reuses_authority_and_preserves_work(tmp_path, point):
    host = Fixture(tmp_path)
    with pytest.raises(Crash):
        op.Operation(plan(), host, fault=crash_at(point)).run("apply")
    authority = host.raw("/records/authority.json")
    result = op.Operation(plan(), host).run("apply")
    assert result["phase"] == "activated"
    assert host.raw("/records/authority.json") == authority
    assert host.events.count("authority_timestamp") == 1
    assert host.started.count(c.TIMER) == 1
    assert host.raw("/data/old-receipt.json") == b"old committed receipt\n"
    ledger = json.loads(host.raw(c.LEDGER))
    assert (
        ledger["configuration_history"][:-1] == PREPARED["ledger_before"]["configuration_history"]
    )
    assert ledger["step_attempts"] == PREPARED["ledger_before"]["step_attempts"]


def test_unissued_start_intent_is_not_automatically_repeated(tmp_path):
    host = Fixture(tmp_path)
    with pytest.raises(Crash):
        op.Operation(plan(), host, fault=crash_at("start_intent:" + c.TIMER)).run("apply")
    with pytest.raises(op.Hold, match="ambiguous"):
        op.Operation(plan(), host).run("apply")
    assert c.TIMER not in host.started


def test_proposal_mismatch_leaves_disabled_grant_and_pending_ledger(tmp_path):
    host = Fixture(tmp_path)
    host.proposal_wrong = True
    with pytest.raises(op.Hold, match="proposal differs"):
        op.Operation(plan(), host).run("apply")
    assert json.loads(host.raw(c.GRANT))["enabled"] is False
    assert json.loads(host.raw(c.LEDGER))["schema"] == 2
    assert not host.started


@pytest.mark.parametrize("reason", ["busy", "unfinished"])
def test_drain_or_request_hold_does_not_change_scope_or_ledger(tmp_path, reason):
    host = Fixture(tmp_path)
    setattr(host, reason, True)
    before = {p: host.raw(p) for p in (*PREPARED["changes"], c.GRANT, c.LEDGER)}
    with pytest.raises(op.Hold):
        op.Operation(plan(), host).run("apply")
    assert before == {p: host.raw(p) for p in before}
    assert not host.started


@pytest.mark.parametrize(
    "point", ["pending", "file:" + c.BINDING, "receipt", "ledger_committed", "activated"]
)
def test_rollback_preserves_new_records_and_keeps_archiver_held(tmp_path, point):
    host = Fixture(tmp_path)
    with pytest.raises(Crash):
        op.Operation(plan(), host, fault=crash_at(point)).run("apply")
    host.write("/data/new-shared-body.stdout", b"new retained body")
    host.write("/data/new-command-receipt.json", b"new receipt")
    result = op.Operation(plan(), host).run("rollback")
    assert result["phase"] == "rolled_back_archiver_held"
    assert host.rows[c.TIMER]["ActiveState"] == "inactive"
    assert host.raw(c.GRANT) == PREPARED["recurring_before"]["text"].encode()
    assert host.raw("/data/new-shared-body.stdout") == b"new retained body"
    assert host.raw("/data/new-command-receipt.json") == b"new receipt"
    op.Operation(plan(), host).run("rollback")
    with pytest.raises(op.Hold, match="reactivate"):
        op.Operation(plan(), host).run("apply")


def test_stop_timeout_closes_reactivation_without_force_or_config_changes(tmp_path):
    host = Fixture(tmp_path)
    op.Operation(plan(), host).run("apply")
    before = host.raw(c.GRANT), host.raw(c.LEDGER)
    host.busy = True
    with pytest.raises(op.Hold):
        op.Operation(plan(), host).run("stop")
    assert before == (host.raw(c.GRANT), host.raw(c.LEDGER))
    assert host.rows[c.TIMER]["ActiveState"] == "inactive"
    with pytest.raises(op.Hold, match="reactivate"):
        op.Operation(plan(), host).run("apply")


@pytest.mark.parametrize(
    "point",
    [
        "rollback_intent",
        "pending",
        "grant_disabled",
        "file:" + c.BINDING,
        "proposal_verified",
        "grant_restored",
        "receipt",
        "ledger_committed",
        "start_return:marketdata-oi.timer",
        "rolled_back",
    ],
)
def test_rollback_interruption_can_finish_without_reopening_archiver(tmp_path, point):
    host = Fixture(tmp_path)
    op.Operation(plan(), host).run("apply")
    host.write("/data/new-receipt.json", b"retained new scope receipt")
    with pytest.raises(Crash):
        op.Operation(plan(), host, fault=crash_at(point)).run("rollback")
    op.Operation(plan(), host).run("rollback")
    assert host.rows[c.TIMER]["ActiveState"] == "inactive"
    assert host.started.count(c.TIMER) == 1
    assert host.raw("/data/new-receipt.json") == b"retained new scope receipt"
    history = json.loads(host.raw(c.LEDGER))["configuration_history"]
    assert [v["direction"] for v in history[-2:]] == ["apply", "rollback"]


def test_changed_initial_ledger_is_refused_before_timer_hold(tmp_path):
    host = Fixture(tmp_path)
    ledger = json.loads(host.raw(c.LEDGER))
    ledger["step_attempts"] = 1
    host.write(c.LEDGER, c.encode(ledger))
    with pytest.raises(op.Hold, match="Initial ledger"):
        op.Operation(plan(), host).run("apply")
    assert "hold" not in host.events and "install" not in host.events


def test_rollback_before_configuration_restores_other_timers_without_ledger_history(tmp_path):
    host = Fixture(tmp_path)
    host.busy = True
    with pytest.raises(op.Hold):
        op.Operation(plan(), host).run("apply")
    host.busy = False
    result = op.Operation(plan(), host).run("rollback")
    assert result["phase"] == "rolled_back_archiver_held"
    assert json.loads(host.raw(c.LEDGER)) == PREPARED["ledger_before"]
    assert host.rows[c.TIMER]["ActiveState"] == "inactive"
    assert all(row["ActiveState"] == "active" for name, row in host.rows.items() if name != c.TIMER)
    with pytest.raises(op.Hold, match="reactivate"):
        op.Operation(plan(), host).run("apply")


def test_unknown_partial_configuration_is_not_overwritten(tmp_path):
    host = Fixture(tmp_path)
    with pytest.raises(Crash):
        op.Operation(plan(), host, fault=crash_at("pending")).run("apply")
    host.write(c.BINDING, b'{"unexpected": true}\n')
    with pytest.raises(op.Hold, match="Unrecognized configuration"):
        op.Operation(plan(), host).run("apply")
    assert host.raw(c.BINDING) == b'{"unexpected": true}\n'
