import copy
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import handoff as h


class Crash(BaseException):
    pass


class Managers:
    """Manager state double, using the unchanged native state classifier."""
    def __init__(self, plan, fs):
        self.plan, self.fs, self.commands = plan, fs, []
        self.count = 1
        self.loaded = {}
        self.props = {}
        self.queued = {}
        for n in (*h.c.SERVICES, *h.c.TIMERS, h.NEW_WAITER):
            self.props[n] = {"Id": n, "MainPID": "0", "ControlPID": "0", "InvocationID": "",
                "ActiveEnterTimestampMonotonic": "0", "ActiveState": "inactive", "SubState": "dead"}
        self.reload()
        for n in h.c.TIMERS:
            self.props[n].update(ActiveState="active", SubState="waiting")

    def reload(self):
        for n in self.props:
            self.loaded[n] = self.fs.node(h.c.SYSTEM_ROOT + n)

    def show(self, manager, name):
        assert manager == "system"
        value = dict(self.props[name])
        node = self.loaded[name]
        value["LoadState"] = "masked" if node == h.MASK else "not-found" if node["kind"] == "absent" else "loaded"
        return value

    def jobs(self, manager):
        assert manager == "system"
        return [{"job": i + 1, "unit": n, "state": s, "type": "start"} for i, (n, s) in enumerate(self.queued.items())]

    def states(self, manager, names):
        return {n: {"properties": (p := self.show(manager, n)), "classification": h.c.classify(p, self.jobs(manager)),
                    "jobs": [j for j in self.jobs(manager) if j["unit"] == n]} for n in names}

    def command(self, manager, action, *args):
        assert manager == "system"
        names = [n for n in args if not n.startswith("--")]
        assert all(n in (*h.c.SERVICES, *h.c.TIMERS, h.NEW_WAITER) for n in names)
        assert h.c.WAITER not in names
        self.commands.append((action, tuple(names)))
        if action == "daemon-reload":
            self.reload()
        for n in names:
            if action == "stop":
                assert self.props[n]["MainPID"] == self.props[n]["ControlPID"] == "0", "Must not stop an executing body"
                if n in h.c.SERVICES:
                    assert self.loaded[n] == h.MASK and self.fs.json(h.t.LEDGER)["schema"] == 2
                self.props[n].update(ActiveState="inactive", SubState="dead")
                self.queued.pop(n, None)
            elif action == "start":
                assert self.fs.json(h.t.LEDGER)["phase"] == "closed"
                assert self.loaded[n]["kind"] == "file"
                self.count += 1
                self.props[n].update(InvocationID=str(self.count), ActiveEnterTimestampMonotonic=str(self.count))
                if n in h.c.TIMERS:
                    self.props[n].update(ActiveState="active", SubState="waiting")
            else:
                raise AssertionError(action)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    def pending(self, name):
        self.props[name].update(ActiveState="activating", SubState="auto-restart")


class Host:
    def __init__(self, plan, fs, managers):
        self.plan, self.fs, self.m = plan, fs, managers
        self.auxiliary_busy = False

    def immutable(self):
        for path, value in self.plan["inner"]["protected"].items():
            actual = self.fs.node(path)
            h.c.require(actual.get("sha256") == value["sha256"] if value["kind"] == "digest" else actual == value,
                        "Protected input changed")
        for key, value in self.plan["inner"]["retirement_masks"].items():
            manager, name = key.split(":")
            prefix = h.c.SYSTEM_ROOT if manager == "system" else h.c.USER_ROOT
            h.c.require(self.fs.node(prefix + name) == value == h.MASK, "Retirement mask changed")

    def auxiliary_idle(self):
        h.c.require(not self.auxiliary_busy, "T05 running")

    def drained(self):
        self.immutable()
        self.auxiliary_idle()
        h.c.require(all(v["classification"] == "idle" for v in self.m.states("system", (*h.c.SERVICES, *h.c.TIMERS)).values()), "Not drained")

    def activation(self, direction):
        self.drained()
        for n in (*h.c.SERVICES, h.NEW_WAITER):
            h.c.require(self.m.loaded[n] == self.fs.node(h.c.SYSTEM_ROOT + n), "Need reload")
        return {"double": True, "direction": direction}

    def clock_ready(self):
        return None


@pytest.fixture
def lab(tmp_path):
    plan = json.loads((HERE / "host-plan.json").read_bytes())
    root = tmp_path / "machine"
    root.mkdir()
    inner = plan["inner"]
    allowed = set(inner["operations"]) | set(inner["protected"]) | {h.t.LEDGER}
    allowed.update((h.c.SYSTEM_ROOT if k.startswith("system:") else h.c.USER_ROOT) + k.split(":")[1] for k in inner["retirement_masks"])
    fs = h.c.SafeFiles(root, allowed, {}, control_prefix="/var/lib/marketdata-clock")
    for p, op in inner["operations"].items():
        fs.write(p, op["before"])
    for p, value in inner["protected"].items():
        if value["kind"] == "digest":
            raw = Path(p).read_bytes()
            assert h.c.sha(raw) == value["sha256"]
            value = {"kind": "file", "mode": 0o644, "text": raw.decode(), "sha256": h.c.sha(raw)}
        fs.write(p, value)
    for k, value in inner["retirement_masks"].items():
        fs.write((h.c.SYSTEM_ROOT if k.startswith("system:") else h.c.USER_ROOT) + k.split(":")[1], value)
    fs.save(h.t.LEDGER, inner["ledger_before"])
    fs.path("/var/lib/marketdata-clock/coordinator.lock").touch()
    m = Managers(plan, fs)
    host = Host(plan, fs, m)
    evidence = root / "preserved-business-records"
    evidence.write_bytes(b"request pins, attempt history, data, T05 baseline and new completion objects\n")
    return SimpleNamespace(plan=plan, fs=fs, m=m, host=host, evidence=evidence,
                           ticks=0, slept=lambda: None, requests={})


def executor(lab, **kw):
    def sleep(_):
        lab.ticks += 1
        lab.slept()
    approval = {"purpose": h.PURPOSE, "enabled": True, "plan_id": lab.plan["plan_id"],
                "approval_reference": "isolated", "directions": ["apply", "rollback"]}
    return h.Handoff(lab.plan, approval, lab.fs, lab.m, lab.host, requests=lambda: lab.requests,
                     boot=lambda: lab.plan["boot_id"], monotonic=lambda: lab.ticks, sleep=sleep, **kw)


def assert_final(lab, side):
    ledger = lab.fs.json(h.t.LEDGER)
    assert ledger["phase"] == "closed"
    for k, value in lab.plan["inner"]["ledger_before"].items():
        if k not in ("profile_sha256", "configuration_history"):
            assert ledger[k] == value
    assert all(lab.fs.node(p) == op[side] for p, op in lab.plan["inner"]["operations"].items())
    assert lab.evidence.read_bytes().startswith(b"request pins, attempt history, data, T05 baseline")
    lab.host.immutable()


def test_normal_body_finishes_with_shared_coordinator_and_old_guards(lab):
    n = "marketdata-backup.service"
    lab.m.props[n].update(MainPID="900", ActiveState="activating", SubState="start")
    def finish():
        with lab.fs.path("/var/lib/marketdata-clock/coordinator.lock").open("rb") as f:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            assert lab.fs.json(h.t.LEDGER) == lab.plan["inner"]["ledger_before"]
            assert all(lab.fs.node(p) == op["before"] for p, op in lab.plan["inner"]["operations"].items())
            lab.evidence.write_bytes(lab.evidence.read_bytes() + b"new T05 completion from finishing backup\n")
        lab.m.props[n].update(MainPID="0", ActiveState="inactive", SubState="dead")
    lab.slept = finish
    result = executor(lab).run()
    assert lab.ticks == 1 and result["phase"] == "complete"
    assert not any(a == "stop" and n in names for a, names in lab.m.commands)
    assert_final(lab, "after")


def test_pending_request_is_recorded_then_delivered_before_timers_once(lab):
    name = "marketdata-backup.service"
    lab.m.pending(name)
    result = executor(lab).run()
    assert name in result["cancelled"]
    starts = [names[0] for a, names in lab.m.commands if a == "start"]
    assert starts == [name, *h.c.TIMERS]
    count = len(lab.m.commands)
    executor(lab).run()
    assert len(lab.m.commands) == count


def test_barrier_reload_can_remove_waiting_job_without_losing_its_request(lab):
    name = h.c.SERVICES[0]
    lab.m.pending(name)
    reload = lab.m.reload
    def cancel_on_reload():
        reload()
        if lab.m.loaded[name] == h.MASK:
            lab.m.props[name].update(ActiveState="inactive", SubState="dead")
    lab.m.reload = cancel_on_reload
    result = executor(lab).run()
    assert name in result["cancelled"] and result["starts"][name]["accepted"]
    assert not any(a == "stop" and names == (name,) for a, names in lab.m.commands)


@pytest.mark.parametrize("pending_durable", [False, True])
def test_cutoff_is_retained_only_after_pending_is_durable(lab, pending_durable):
    name = h.c.SERVICES[0]
    lab.m.pending(name)
    cut = "pending-before-mask" if pending_durable else "inner-intent"
    def fault(point):
        if point == cut:
            raise Crash(point)
    with pytest.raises(Crash):
        executor(lab, fault=fault).run()
    # Before pending, old request may finish. After pending, its gate may reject
    # a retry and remove the waiting job. Only the latter needs delivery.
    lab.m.props[name].update(ActiveState="inactive", SubState="dead")
    result = executor(lab).run()
    assert (name in result["starts"]) == pending_durable


CUTS = ["phase:installed", "timer-stop:" + h.c.TIMERS[0], "phase:timers_held", "request-inventory", "inner-intent",
        "pending-before-mask", "mask:" + h.c.SERVICES[0], "mask:" + h.c.SERVICES[-1], "phase:sealed",
        "cancel-intent:" + h.c.SERVICES[0], "cancel-return:" + h.c.SERVICES[0], "phase:drained",
        "barrier-remove:" + h.c.SERVICES[0], "barrier-remove:" + h.c.SERVICES[-1], "transaction-staged",
        "phase:staged", "phase:committed", "phase:activated", "phase:complete"]


@pytest.mark.parametrize("cut", CUTS)
@pytest.mark.parametrize("direction", ["apply", "rollback"])
def test_host_interruption_resume_and_rollback(lab, cut, direction):
    lab.m.pending(h.c.SERVICES[0])
    def fault(point):
        if point == cut:
            raise Crash(cut)
    with pytest.raises(Crash):
        executor(lab, fault=fault).run()
    result = executor(lab).run(direction)
    assert result["phase"] == "complete"
    assert_final(lab, "after" if direction == "apply" else "before")
    executor(lab).run(direction)


@pytest.mark.parametrize("cut", ["file:0", "file:7", "file:12", "receipt", "committed"])
@pytest.mark.parametrize("direction", ["apply", "rollback"])
def test_engine_interruptions_inside_host_phases(lab, cut, direction):
    first = executor(lab)
    def fault(point):
        if point == cut:
            raise Crash(cut)
    first.tx.fault = fault
    with pytest.raises(Crash):
        first.run()
    result = executor(lab).run(direction)
    assert result["phase"] == "complete"
    assert_final(lab, "after" if direction == "apply" else "before")


@pytest.mark.parametrize("name", [h.c.SERVICES[0], h.c.TIMERS[0]])
def test_start_return_interruption_uses_new_invocation_not_duplicate_start(lab, name):
    lab.m.pending(h.c.SERVICES[0])
    def fault(point):
        if point == "start-return:" + name:
            raise Crash(point)
    with pytest.raises(Crash):
        executor(lab, fault=fault).run()
    result = executor(lab).run()
    assert result["starts"][name]["reconciled"]
    assert sum(a == "start" and names == (name,) for a, names in lab.m.commands) == 1


def test_ambiguous_start_requires_review_and_rollback_records_undelivered(lab):
    name = h.c.SERVICES[0]
    lab.m.pending(name)
    def fault(point):
        if point == "start-intent:" + name:
            raise Crash(point)
    with pytest.raises(Crash):
        executor(lab, fault=fault).run()
    with pytest.raises(h.c.Hold, match="Ambiguous start"):
        executor(lab).run()
    result = executor(lab).run("rollback")
    assert name in result["unreconciled_requests"] and result["result"]["request_reconciliation_required"]
    assert not any(a == "start" and names == (name,) for a, names in lab.m.commands)


@pytest.mark.parametrize("state", ["running", "between-commands"])
def test_600_second_hold_never_stops_work_or_changes_guards(lab, state):
    n = h.c.SERVICES[0]
    if state == "running":
        lab.m.props[n].update(MainPID="10", ActiveState="activating", SubState="start")
    else:
        lab.m.queued[n] = "running"
    with pytest.raises(h.c.Hold, match="Normal drain pending"):
        executor(lab).run()
    assert lab.ticks == 600
    assert lab.fs.json(h.t.LEDGER) == lab.plan["inner"]["ledger_before"]
    assert all(lab.fs.node(p) == op["before"] for p, op in lab.plan["inner"]["operations"].items())
    assert not any(a == "stop" and n in names for a, names in lab.m.commands)


def test_install_failure_precedes_timer_hold(lab):
    def fail():
        raise OSError("installation")
    with pytest.raises(OSError):
        executor(lab, installer=fail).run()
    assert lab.m.commands == []
    assert_final(lab, "before")


def test_unfinished_request_blocks_apply_and_exact_review_preserves_rollback(lab):
    executor(lab).run()
    lab.requests = {"daily": {"requires_reconciliation": True, "sha256": "a" * 64, "pins": {"day": "2026-10-15"}}}
    with pytest.raises(h.c.Hold, match="exact preservation review"):
        executor(lab).run("rollback")
    review = {"plan_id": lab.plan["plan_id"], "action": "preserve-for-reviewed-rollback",
              "requests": {"daily": "a" * 64}, "reason": "retain exact request; manual scope reconciliation required"}
    with pytest.raises(h.c.Hold):
        executor(lab, request_review={**review, "requests": {"daily": "b" * 64}}).run("rollback")
    result = executor(lab, request_review=review).run("rollback")
    assert result["result"]["request_reconciliation_required"]
    assert result["preserved_unfinished_requests"] == review["requests"]
    assert lab.requests["daily"]["pins"]["day"] == "2026-10-15"
    assert_final(lab, "before")


def test_initial_unfinished_request_cannot_be_deleted_or_bypassed(lab):
    lab.requests = {"backup": {"requires_reconciliation": True, "sha256": "c" * 64}}
    with pytest.raises(h.c.Hold, match="Unfinished wrapper request"):
        executor(lab).run()
    assert lab.fs.json(h.t.LEDGER) == lab.plan["inner"]["ledger_before"]
    assert lab.requests["backup"]["requires_reconciliation"]


def test_auxiliary_busy_and_configuration_drift_hold(lab):
    lab.host.auxiliary_busy = True
    with pytest.raises(h.c.Hold, match="T05 running"):
        executor(lab).run()
    lab.host.auxiliary_busy = False
    path = h.c.SYSTEM_ROOT + h.c.TIMERS[0]
    lab.fs.write(path, {"kind": "absent"})
    with pytest.raises(h.c.Hold, match="Protected input"):
        executor(lab).run()


def test_reviewed_plan_cannot_expand_manager_or_configuration_scope(lab):
    plan = copy.deepcopy(lab.plan)
    plan["request_root"] = "/tmp/not-operating-state"
    plan["plan_id"] = h.c.sha(h.c.encode({k: v for k, v in plan.items() if k != "plan_id"}))
    with pytest.raises(h.c.Hold):
        h.validate(plan, {"purpose": h.PURPOSE, "enabled": True, "plan_id": plan["plan_id"],
                          "approval_reference": "test", "directions": ["apply", "rollback"]})
