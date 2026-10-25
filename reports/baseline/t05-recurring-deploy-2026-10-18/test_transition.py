"""Disposable roots and manager doubles; no production unit/data operations."""

# ruff: noqa: E402 -- tests load this independent sealed deployment directory.
import contextlib
import copy
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine as e
import host as live
import installation
from vendor import handoff_common as c


def node(text):
    return e.file_node(text.encode())


def fixture_plan():
    config = "/etc/marketdata/checkpoint-clock-retirement/"
    unit_paths = [
        config + "units/" + e.SERVICE,
        config + "units/" + e.AUTO + "-preview.service",
        config + "units/" + e.AUTO + "-apply@.service",
        e.SYSTEM + e.AUTO + "-apply@.service",
    ]
    changes = {p: {"before": node("old-" + p), "after": node("new-" + p)} for p in unit_paths}
    for unit in (e.SERVICE, e.TIMER):
        changes[e.SYSTEM + unit] = {"before": e.MASK, "after": node("new-" + unit)}
    disabled = {
        "enabled": False,
        "approved_at": None,
        "approval_reference": None,
        "scope_id": "new-scope",
        "scope": {"policy": "unchanged"},
    }
    old = {**disabled, "scope_id": "old-scope"}
    changes[e.GRANT] = {
        "before": e.file_node(c.encode(old)),
        "after": e.file_node(c.encode(disabled)),
    }
    link = e.SYSTEM + "timers.target.wants/" + e.TIMER
    changes[link] = {"before": e.ABSENT, "after": {"kind": "symlink", "target": e.SYSTEM + e.TIMER}}
    plan = {
        "configuration_order": unit_paths + [e.SYSTEM + e.SERVICE, e.SYSTEM + e.TIMER],
        "changes": changes,
        "disabled_proposal": disabled,
        "enable_link": link,
    }
    plan["operation_plan_id"] = e.operation_id(plan)
    return plan


class FakeHost:
    def __init__(self, root, plan, files):
        self.root, self.p, self.f = Path(root), plan, files
        self.manager_path = self.root / "fake-manager.json"
        self.locked = False
        if not self.manager_path.exists():
            self.store(
                {
                    "timer": "inactive",
                    "service": "idle",
                    "starts": 0,
                    "stops": 0,
                    "reloads": 0,
                    "config_ok": True,
                    "protected_ok": True,
                    "scope_ok": True,
                    "lock_busy": False,
                    "unfinished": [],
                    "installed": False,
                }
            )

    def read(self):
        return json.loads(self.manager_path.read_bytes())

    def store(self, value):
        self.manager_path.write_bytes(c.encode(value))

    def change(self, **values):
        value = self.read()
        value.update(values)
        self.store(value)

    def invariants(self, *, activation):
        c.require(not activation or self.read()["config_ok"], "Changed boot/config")
        c.require(self.read()["protected_ok"], "Evidence changed")

    def permissions(self, path):
        value = self.f.node(path)
        c.require(value["kind"] != "file" or value["mode"] == 0o644, "Mode changed")

    def install(self):
        self.change(installed=True)
        return {"installed": True}

    def import_check(self):
        c.require(self.read()["installed"], "Not installed")
        return {"uid": 1000}

    def verify_release(self):
        c.require(self.read()["installed"], "Installation incomplete")

    def require_timer_idle(self):
        c.require(self.read()["timer"] == "inactive", "Timer not idle")

    def require_idle(self):
        c.require(self.read()["service"] == "idle", "Work active")

    def drain(self):
        c.require(not self.locked, "Coordinator/runner held across drain")
        self.require_idle()

    @contextlib.contextmanager
    def configuration_lock(self):
        c.require(not self.read()["lock_busy"], "Lock contended")
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def unfinished(self, review_only=False):
        value = self.read()["unfinished"]
        c.require(review_only or not value, "Unfinished retirement")
        return value

    def reload(self):
        self.change(reloads=self.read()["reloads"] + 1)

    def probe(self):
        c.require(self.locked, "Verification outside configuration lock")
        value = copy.deepcopy(self.p["disabled_proposal"])
        if not self.read()["scope_ok"]:
            value["scope_id"] = "unreviewed-scope"
        return {"proposal": value, "uid": 1000, "guards": True}

    def start_timer(self):
        c.require(not self.locked, "Runner lock held while enabling execution")
        self.require_timer_idle()
        # Any real automatic invocation can only see a complete approved set.
        for path, entry in self.p["changes"].items():
            current = self.f.node(path)
            if path == e.GRANT:
                c.require(json.loads(current["text"])["enabled"], "Timer without grant")
            else:
                c.require(current == entry["after"], "Timer with incomplete configuration")
        self.change(timer="active", starts=self.read()["starts"] + 1)
        return self.verify_active()

    def verify_active(self):
        c.require(self.read()["timer"] == "active", "Start uncertain")
        return {"next_utc": "2026-10-19T06:40:00+00:00"}

    def stop_timer(self):
        if self.read()["timer"] != "inactive":
            self.change(timer="inactive", stops=self.read()["stops"] + 1)

    def verify_closed(self):
        self.require_idle()
        self.require_timer_idle()
        for unit in (e.SERVICE, e.TIMER):
            assert self.f.node(e.SYSTEM + unit) == e.MASK
        grant = self.f.json(e.GRANT)
        assert (
            grant["enabled"] is False
            and grant["approved_at"] is None
            and grant["approval_reference"] is None
        )


def setup(root, initialize=True):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    plan = fixture_plan()
    prefix = "/var/lib/marketdata-clock/t05-recurring-operations/" + plan["operation_plan_id"]
    files = c.SafeFiles(root, plan["changes"], {}, control_prefix=prefix)
    if initialize:
        for path, item in plan["changes"].items():
            files.write(path, item["before"])
        # Unrelated data and prior failed/successful runs survive every action.
        (root / "evidence.json").write_text('{"baseline":"original","new_objects":[1]}')
        (root / "journal.json").write_text('{"old_failure":true,"new_success":true}')
    return plan, files, FakeHost(root, plan, files)


APPLY_STEPS = [
    "install",
    "import",
    "disabled_grant",
    *["configure:" + path for path in fixture_plan()["configuration_order"]],
    "reload",
    "grant",
    "enable_link",
    "enable_reload",
    "timer_start",
    "apply_complete",
]


class Interrupted(BaseException):
    pass


@pytest.mark.parametrize(
    "boundary", [n + ":" + suffix for n in APPLY_STEPS for suffix in ("intent", "effect")]
)
def test_apply_interrupt_resume_without_duplicate_start(tmp_path, boundary):
    plan, files, host = setup(tmp_path)

    def hook(name):
        if name == boundary:
            raise Interrupted(name)

    with pytest.raises(Interrupted):
        e.Engine(plan, files, host, hook).run("apply", plan["operation_plan_id"])
    before = (tmp_path / "evidence.json").read_bytes()
    if boundary == "timer_start:intent":
        # Intent alone cannot establish whether an external start occurred.
        with pytest.raises(c.Hold, match="Start uncertain"):
            e.Engine(plan, files, host).run("apply", plan["operation_plan_id"])
        result = e.Engine(plan, files, host).run("rollback", plan["operation_plan_id"])
        assert result["phase"] == "rollback_masked"
        assert host.read()["starts"] == 0
    else:
        assert e.Engine(plan, files, host).run("apply", plan["operation_plan_id"])["passed"]
        count = host.read()["reloads"]
        assert (
            e.Engine(plan, files, host).run("apply", plan["operation_plan_id"])["phase"]
            == "already_activated"
        )
        assert host.read()["starts"] == 1 and host.read()["reloads"] == count
    assert (tmp_path / "evidence.json").read_bytes() == before


@pytest.mark.parametrize(
    "boundary",
    [
        "revoke:effect",
        "mask:" + e.SYSTEM + e.SERVICE + ":effect",
        "mask:" + e.SYSTEM + e.TIMER + ":effect",
        "unlink_enable:effect",
        "rollback_reload:effect",
        *["restore:" + p + ":effect" for p in fixture_plan()["changes"]],
    ],
)
def test_rollback_interrupts_preserve_new_records(tmp_path, boundary):
    plan, files, host = setup(tmp_path)
    e.Engine(plan, files, host).run("apply", plan["operation_plan_id"])
    (tmp_path / "evidence.json").write_text('{"baseline":"original","new_objects":[1,2]}')
    expected = (tmp_path / "evidence.json").read_bytes()

    def hook(name):
        if name == boundary:
            raise Interrupted(name)

    with pytest.raises(Interrupted):
        e.Engine(plan, files, host, hook).run("rollback", plan["operation_plan_id"])
    result = e.Engine(plan, files, host).run("rollback", plan["operation_plan_id"])
    assert result["phase"] == "rollback_masked"
    assert (tmp_path / "evidence.json").read_bytes() == expected
    for path, item in plan["changes"].items():
        assert files.node(path) == item["before"]
    with pytest.raises(c.Hold, match="cannot reactivate"):
        e.Engine(plan, files, host).run("apply", plan["operation_plan_id"])


@pytest.mark.parametrize(
    "boundary", ["disabled_grant:effect", "grant:effect", "enable_link:effect", "install:effect"]
)
def test_partial_install_can_roll_back_without_activation(tmp_path, boundary):
    plan, files, host = setup(tmp_path)

    def hook(name):
        if name == boundary:
            raise Interrupted(name)

    with pytest.raises(Interrupted):
        e.Engine(plan, files, host, hook).run("apply", plan["operation_plan_id"])
    e.Engine(plan, files, host).run("rollback", plan["operation_plan_id"])
    assert host.read()["starts"] == 0
    for path, item in plan["changes"].items():
        assert files.node(path) == item["before"]


def test_stop_running_work_does_not_kill_or_rewrite_grant(tmp_path):
    plan, files, host = setup(tmp_path)
    e.Engine(plan, files, host).run("apply", plan["operation_plan_id"])
    grant = files.node(e.GRANT)
    host.change(service="activating-with-main-pid")
    with pytest.raises(c.Hold, match="Work active"):
        e.Engine(plan, files, host).run("stop", plan["operation_plan_id"])
    assert (
        host.read()["timer"] == "inactive" and host.read()["service"] == "activating-with-main-pid"
    )
    assert files.node(e.GRANT) == grant
    host.change(service="idle", unfinished=[{"plan": "incomplete"}])
    result = e.Engine(plan, files, host).run("stop", plan["operation_plan_id"])
    assert result["unfinished_review"] == [{"plan": "incomplete"}]
    host.verify_closed()
    e.Engine(plan, files, host).run("rollback", plan["operation_plan_id"])


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"scope_ok": False}, "Installed scope differs"),
        ({"config_ok": False}, "Changed boot/config"),
        ({"lock_busy": True}, "Lock contended"),
        ({"unfinished": ["pending"]}, "Unfinished retirement"),
    ],
)
def test_refusals_do_not_enable(tmp_path, change, reason):
    plan, files, host = setup(tmp_path)
    host.change(**change)
    with pytest.raises(c.Hold, match=reason):
        e.Engine(plan, files, host).run("apply", plan["operation_plan_id"])
    assert host.read()["starts"] == 0 and not files.json(e.GRANT)["enabled"]


def test_unknown_file_is_not_overwritten(tmp_path):
    plan, files, host = setup(tmp_path)
    path = plan["configuration_order"][0]
    files.write(path, node("unrecognised"))
    with pytest.raises(c.Hold, match="Initial configuration differs"):
        e.Engine(plan, files, host).run("apply", plan["operation_plan_id"])
    assert files.node(path) == node("unrecognised")


def test_authority_bytes_stable_across_resume(tmp_path):
    plan, files, host = setup(tmp_path)

    def hook(name):
        if name == "grant:effect":
            raise Interrupted()

    with pytest.raises(Interrupted):
        e.Engine(plan, files, host, hook).run("apply", plan["operation_plan_id"])
    value = files.node(e.GRANT)
    audit = "/var/lib/marketdata-clock/t05-recurring-operations/" + plan["operation_plan_id"]
    assert files.node(audit + "/active-grant.json") == value
    assert files.json(audit + "/active-grant-hash.json")["sha256"] == value["sha256"]
    e.Engine(plan, files, host).run("apply", plan["operation_plan_id"])
    assert files.node(e.GRANT) == value
    assert files.json(e.GRANT)["scope_id"] == plan["disabled_proposal"]["scope_id"]


@pytest.mark.parametrize("mode", ["symlink", "hardlink", "mode"])
def test_sealed_install_rejects_unreviewed_objects(tmp_path, mode):
    src = tmp_path / "src"
    src.write_bytes(b"fixed")
    dest = tmp_path / "release"
    entries = {
        "data": {"kind": "file", "source": str(src), "sha256": c.sha(b"fixed"), "mode": 0o644}
    }
    installation.install(dest, entries, "release", live=False)
    path = dest / "data"
    if mode == "symlink":
        path.unlink()
        path.symlink_to(src)
    elif mode == "hardlink":
        os.link(path, tmp_path / "linked")
    else:
        path.chmod(0o666)
    with pytest.raises(c.Hold):
        installation.install(dest, entries, "release", live=False)


@pytest.mark.parametrize(
    "boundary", ["mkdir", "staged", "renamed", "staged_payload", "renamed_payload"]
)
def test_real_process_kill_during_install(tmp_path, boundary):
    src = tmp_path / "source"
    src.write_bytes(b"sealed source" * 1024)
    code = """
import sys, os, signal
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import installation
from vendor import handoff_common as c
root=Path(sys.argv[2]); boundary=sys.argv[3]
def hook(stage,path):
    if stage==boundary or (path.name=='data' and boundary==stage+'_payload'):
        os.kill(os.getpid(), signal.SIGKILL)
entries={'a/data':{'kind':'file','source':str(root/'source'),'sha256':c.sha((root/'source').read_bytes()),'mode':0o644}}
installation.install(root/'release', entries,'test',live=False,hook=hook)
"""
    run = subprocess.run([sys.executable, "-B", "-c", code, str(HERE), str(tmp_path), boundary])
    assert run.returncode == -signal.SIGKILL
    entries = {
        "a/data": {
            "kind": "file",
            "source": str(src),
            "sha256": c.sha(src.read_bytes()),
            "mode": 0o644,
        }
    }
    installation.install(tmp_path / "release", entries, "test", live=False)
    assert (tmp_path / "release/a/data").read_bytes() == src.read_bytes()


@pytest.mark.parametrize(
    "boundary",
    [
        "disabled_grant:effect",
        "grant:effect",
        "timer_start:effect",
        "revoke:effect",
        "mask:" + e.SYSTEM + e.SERVICE + ":effect",
    ],
)
def test_real_process_kill_configuration_transition(tmp_path, boundary):
    plan, files, host = setup(tmp_path)
    action = "stop" if boundary.startswith(("revoke:", "mask:")) else "apply"
    if action == "stop":
        e.Engine(plan, files, host).run("apply", plan["operation_plan_id"])
    code = """
import sys,os,signal
sys.path.insert(0,sys.argv[1])
from test_transition import setup
import engine
p,f,h=setup(sys.argv[2],initialize=False)
def hook(name):
    if name==sys.argv[3]: os.kill(os.getpid(),signal.SIGKILL)
engine.Engine(p,f,h,hook).run(sys.argv[4],p['operation_plan_id'])
"""
    run = subprocess.run(
        [sys.executable, "-B", "-c", code, str(HERE), str(tmp_path), boundary, action]
    )
    assert run.returncode == -signal.SIGKILL
    result = e.Engine(plan, files, host).run(action, plan["operation_plan_id"])
    assert result["passed"] and host.read()["starts"] == 1


def test_oneshot_main_pid_and_pending_jobs_are_not_idle():
    p = {
        "Id": e.SERVICE,
        "ActiveState": "activating",
        "SubState": "start",
        "MainPID": "55",
        "ControlPID": "0",
    }
    assert c.classify(p, []) == "running"
    p["MainPID"] = "0"
    assert c.classify(p, [{"unit": e.SERVICE, "state": "waiting"}]) == "pending"
    assert c.classify(p, [{"unit": e.SERVICE, "state": "running"}]) == "unknown"


def test_actual_command_allowlist_cannot_control_service():
    manager = c.Managers(
        allowed={("system", e.TIMER)}, runner=lambda *a, **k: pytest.fail("Unexpected command")
    )
    for unit in (e.SERVICE, "chrony.service", "marketdata-backup.timer"):
        with pytest.raises(c.Hold, match="Out-of-scope"):
            manager.command("system", "stop", unit)


def test_explicit_approval_and_plan_hash_are_required(tmp_path):
    plan, files, host = setup(tmp_path)
    with pytest.raises(c.Hold, match="approval"):
        e.Engine(plan, files, host).run("apply", "wrong")
    plan["disabled_proposal"]["scope_id"] = "changed"
    with pytest.raises(c.Hold, match="Plan digest"):
        e.Engine(plan, files, host)


def test_completed_installation_is_not_silently_repaired(tmp_path):
    src = tmp_path / "src"
    src.write_bytes(b"fixed")
    entries = {
        "data": {"kind": "file", "source": str(src), "sha256": c.sha(b"fixed"), "mode": 0o644}
    }
    installation.install(tmp_path / "release", entries, "release", live=False)
    installation.verify(tmp_path / "release", entries, "release", live=False)
    (tmp_path / "release/data").unlink()
    with pytest.raises(c.Hold, match="missing"):
        installation.verify(tmp_path / "release", entries, "release", live=False)
    assert not (tmp_path / "release/data").exists()


def test_real_shared_coordinator_and_exclusive_runner_boundaries(tmp_path):
    import fcntl

    coordinator, runner = tmp_path / "coordinator", tmp_path / "runner"
    coordinator.touch()
    runner.touch()
    with live.acquire_locks([(coordinator, fcntl.LOCK_SH), (runner, fcntl.LOCK_EX)]):
        # A backup's SH completion lock can coexist; a second runner cannot.
        with coordinator.open("rb") as f:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with pytest.raises(c.Hold, match="Lock contended"):
            with live.acquire_locks([(runner, fcntl.LOCK_EX)]):
                pytest.fail("Duplicate runner lock accepted")
        with pytest.raises(c.Hold, match="Lock contended"):
            with live.acquire_locks([(coordinator, fcntl.LOCK_EX)]):
                pytest.fail("Config transition lock accepted")
    with live.acquire_locks([(runner, fcntl.LOCK_EX)]):
        pass


def test_symlink_parent_never_written(tmp_path):
    plan, files, host = setup(tmp_path)
    parent = tmp_path / "etc/marketdata/checkpoint-clock-retirement/units"
    moved = parent.with_name("saved-units")
    parent.rename(moved)
    parent.symlink_to(moved, target_is_directory=True)
    with pytest.raises(OSError):
        e.Engine(plan, files, host).run("apply", plan["operation_plan_id"])
    assert all(p.read_text().startswith("old-") for p in moved.iterdir())
