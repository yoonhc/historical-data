import copy
import fcntl
import json
import os
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import transition as t


def approval(plan):
    return {"purpose": t.PURPOSE, "plan_id": plan["plan_id"], "enabled": True,
            "approval_reference": "isolated test only", "directions": ["apply", "rollback"]}


def rehash(plan):
    plan.pop("plan_id", None)
    plan["plan_id"] = t.sha(t.encode(plan))
    return plan


class Host:
    def __init__(self, fs):
        self.fs = fs
        self.busy = False
        self.ready = True
        self.loaded = True

    def drained(self):
        t.require(not self.busy, "Body or pending job still running")

    def activation(self, direction):
        t.require(self.loaded, "Not reloaded")
        return {"direction": direction, "systemd": "isolated double"}

    def clock_ready(self):
        return self.ready


@pytest.fixture
def lab(tmp_path):
    plan = json.loads((HERE / "configuration-plan.json").read_text())
    root = tmp_path / "machine"
    root.mkdir()
    (root / ".migration-fixture").touch()
    fs = t.Files(root)
    for p, op in plan["operations"].items():
        fs.write(p, op["before"])
    for p, expected in plan["protected"].items():
        if expected["kind"] == "digest":
            raw = Path(p).read_bytes()
            assert t.sha(raw) == expected["sha256"]
            fs.write(p, {"kind": "file", "mode": 0o644, "text": raw.decode(), "sha256": t.sha(raw)})
        else:
            fs.write(p, expected)
    for key, expected in plan["retirement_masks"].items():
        manager, name = key.split(":")
        prefix = t.SYSTEM if manager == "system" else "/home/yoonhc/.config/systemd/user/"
        fs.write(prefix + name, expected)
    fs.save(t.LEDGER, plan["ledger_before"])
    fs.path("/var/lib/marketdata-clock/coordinator.lock").touch()
    host = Host(fs)
    # New operational state is deliberately outside the configuration transaction.
    artifacts = {}
    for rel in ("data/catalog.sqlite", "state/request.json", "state/attempt.json", "state/observations.jsonl",
                "t05/baseline.json", "t05/objects/completion.json", "t05/objects/order.json"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((rel + " original/new preserved data\n").encode())
        artifacts[p] = p.read_bytes()
    return plan, fs, host, artifacts


def transaction(lab, **kw):
    plan, fs, host, _ = lab
    return t.Transaction(plan, approval(plan), fs, host, boot=lambda: plan["boot_id"], **kw)


def unchanged_evidence(lab, side):
    plan, fs, _, artifacts = lab
    for p, raw in artifacts.items():
        assert p.read_bytes() == raw
    final = fs.json(t.LEDGER)
    assert final["phase"] == "closed"
    for key, value in plan["ledger_before"].items():
        if key not in ("profile_sha256", "configuration_history"):
            assert final[key] == value
    assert all(fs.node(p) == op[side] for p, op in plan["operations"].items())


CUTS = ["intent", "pending", *[f"file:{i}" for i in range(13)], "staged", "receipt", "committed"]


class Interrupted(BaseException):
    pass


@pytest.mark.parametrize("cut", CUTS)
@pytest.mark.parametrize("recovery", ["resume", "rollback"])
def test_every_durable_boundary(lab, cut, recovery):
    def fault(point):
        if point == cut:
            raise Interrupted(point)
    first = transaction(lab, fault=fault)
    with pytest.raises(Interrupted):
        first.stage()
        first.commit()
    after = transaction(lab)
    direction = "apply" if recovery == "resume" else "rollback"
    after.stage(direction)
    after.commit()
    unchanged_evidence(lab, "after" if direction == "apply" else "before")
    assert after.commit()["idempotent"]


def test_pending_and_partial_files_cannot_commit(lab):
    tx = transaction(lab)
    tx.stage()
    p = next(iter(lab[0]["operations"]))
    lab[1].write(p, lab[0]["operations"][p]["before"])
    with pytest.raises(t.Hold, match="Partial"):
        tx.commit()
    assert lab[1].json(t.LEDGER)["schema"] == 2


def test_running_body_and_missing_reload_hold(lab):
    tx = transaction(lab)
    lab[2].busy = True
    with pytest.raises(t.Hold, match="running"):
        tx.stage()
    assert lab[1].json(t.LEDGER) == lab[0]["ledger_before"]
    lab[2].busy = False
    tx.stage()
    lab[2].loaded = False
    with pytest.raises(t.Hold, match="reloaded"):
        tx.commit()


def test_coordinator_is_released_between_configuration_calls(lab):
    tx = transaction(lab)
    lock = lab[1].path("/var/lib/marketdata-clock/coordinator.lock")
    # T05 completion can acquire its shared lock before drain and after stage.
    for action in (lambda: None, tx.stage, tx.commit):
        action()
        with lock.open("rb") as f:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            with pytest.raises(t.Hold, match="Coordinator"):
                tx.stage()


@pytest.mark.parametrize("change", ["boot", "timer", "mask", "foreign-ledger", "approval", "scope"])
def test_drift_is_refused(lab, change):
    plan, fs, host, _ = lab
    if change == "boot":
        tx = t.Transaction(plan, approval(plan), fs, host, boot=lambda: "different")
    elif change == "approval":
        with pytest.raises(t.Hold):
            t.Transaction(plan, {**approval(plan), "purpose": "scheduled-manager-only-v1"}, fs, host, boot=lambda: plan["boot_id"])
        return
    elif change == "scope":
        altered = copy.deepcopy(plan)
        altered["operations"]["/etc/chrony/marketdata.conf"] = next(iter(altered["operations"].values()))
        with pytest.raises(t.Hold):
            t.Transaction(rehash(altered), approval(altered), fs, host, boot=lambda: plan["boot_id"])
        return
    else:
        tx = transaction(lab)
        if change == "timer":
            fs.write(t.SYSTEM + "marketdata-daily.timer", {"kind": "absent"})
        elif change == "mask":
            fs.write(t.SYSTEM + "marketdata-checkpoint-retirement.timer", {"kind": "absent"})
        else:
            fs.save(t.LEDGER, {"schema": 2, "purpose": "foreign"})
    with pytest.raises((t.Hold, FileNotFoundError)):
        tx.stage()


def test_same_candidate_reapply_needs_new_plan_preserves_history(lab):
    tx = transaction(lab)
    tx.stage(); tx.commit()
    tx.stage("rollback"); tx.commit()
    with pytest.raises(t.Hold):
        tx.stage("apply")
    plan = copy.deepcopy(lab[0])
    plan["ledger_before"] = lab[1].json(t.LEDGER)
    rehash(plan)
    again = t.Transaction(plan, approval(plan), lab[1], lab[2], boot=lambda: plan["boot_id"])
    again.stage(); again.commit()
    assert len(lab[1].json(t.LEDGER)["configuration_history"]) == len(lab[0]["ledger_before"].get("configuration_history", [])) + 3
    unchanged_evidence(lab, "after")


def test_request_inventory_uses_existing_lock_and_never_writes(tmp_path):
    d = tmp_path / "daily"; d.mkdir()
    lock = d / "request.lock"; lock.touch()
    request = d / "request.json"
    raw = t.encode({"state": "deferred", "identity": "old", "pins": {"day": "2024-01-01"}})
    request.write_bytes(raw)
    assert t.pending_requests(tmp_path)["daily"]["requires_reconciliation"]
    with lock.open("rb") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        with pytest.raises(BlockingIOError):
            t.pending_requests(tmp_path)
    assert request.read_bytes() == raw


def test_live_filesystem_execution_remains_disabled():
    with pytest.raises(t.Hold, match="fixture"):
        t.Files("/")
