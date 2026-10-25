"""New T05 boundaries; temporary stores only, never operating data."""

import json
import multiprocessing
import os
import signal
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from marketdata.domain import json_bytes
from test_clock_retirement import (  # noqa: F401
    allow_test_system_context,
)
from test_clock_retirement import (
    case as case,
)
from test_clock_retirement import (
    prepared as prepared,
)
from test_clock_retirement import (
    ready as ready,
)
from test_clock_retirement import (
    setup_archive as setup_archive,
)

from scripts import clock_retirement as safe
from scripts import retire_checkpoints as retention


@contextmanager
def failure(exc, when):
    if when == "enter":
        raise exc
    try:
        yield
    finally:
        if when == "exit":
            raise exc


@pytest.mark.parametrize("stage", safe.LOCK_STAGES)
@pytest.mark.parametrize("error", safe.LOCK_ERRORS)
def test_only_failed_acquisition_has_safe_marker(stage, error):
    agent = object.__new__(safe.ClockRetirement)
    original = error("held by another process")
    with pytest.raises(safe.ApplicationLockDeferred) as caught:
        with agent.application_locks(failure(original, "enter"), stage):
            pytest.fail("Protected body must not run")
    assert caught.value.__cause__ is original and caught.value.stage == stage


@pytest.mark.parametrize("when", ("body", "exit"))
@pytest.mark.parametrize("error", safe.LOCK_ERRORS)
def test_busy_after_entry_is_never_safe_acquisition_failure(when, error):
    agent = object.__new__(safe.ClockRetirement)
    original = error("not an acquisition failure")
    with pytest.raises(error) as caught:
        with agent.application_locks(failure(original, when), "preservation"):
            if when == "body":
                raise original
    assert caught.value is original


@pytest.mark.parametrize("error", (OSError, RuntimeError))
def test_unknown_acquisition_error_requires_review(error):
    agent = object.__new__(safe.ClockRetirement)
    with pytest.raises(error):
        with agent.application_locks(
            failure(error("I/O or code failure"), "enter"), "preservation"
        ):
            pytest.fail("Entered body")


def record():
    return {
        "run_id": "b" * 32,
        "plan_id": "a" * 64,
        "state": "deferred",
        "phase": "finished",
        "exit_code": 75,
        "reason": "Busy",
        "deletion_attempted": True,
        "needs_attention": False,
        "deleted_checkpoints": 0,
        "deleted_this_run": 0,
        "completed_before_run": 0,
        "pre_application_lock_deferral": {
            "schema": 1,
            "run_id": "b" * 32,
            "plan_id": "a" * 64,
            "stage": "preservation",
            "exception_type": "Busy",
        },
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "running"),
        ("phase", "applying"),
        ("exit_code", 1),
        ("needs_attention", True),
        ("deleted_this_run", 1),
        ("deleted_checkpoints", 1),
        ("completed_before_run", 1),
        ("unfinished_retirement", True),
        ("retirement_journal", "/some/journal"),
        ("plan_id", "c" * 64),
        ("run_id", "c" * 32),
        ("reason", "ClockHold"),
        ("pre_application_lock_deferral", None),
    ],
)
def test_final_record_must_prove_safe_exit(field, value):
    good = record()
    assert safe.safely_deferred_before_application(good)
    good[field] = value
    assert not safe.safely_deferred_before_application(good)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", 2),
        ("run_id", "x"),
        ("stage", "mutation"),
        ("stage", []),
        ("exception_type", "OSError"),
        ("extra", True),
    ],
)
def test_unknown_or_malformed_certificate_cannot_exempt_prior_intent(field, value):
    r = record()
    r["pre_application_lock_deferral"][field] = value
    assert not safe.safely_deferred_before_application(r)


def enable_recurring(case, monkeypatch):
    settings, wall = case[2], case[3]
    allow_test_system_context(monkeypatch)
    monkeypatch.setattr(
        safe,
        "system_context",
        lambda *a: {
            "manager": "system",
            "service_verified": True,
            "scheduled": True,
            "trigger_at": wall[0].isoformat(),
        },
    )
    grant = safe.proposal(settings)
    grant.update(
        enabled=True, approved_at=wall[0].isoformat(), approval_reference="isolated recurring"
    )
    settings.authorization_file.write_bytes(json_bytes(grant))


def holder(path, shared=False):
    code = (
        "import fcntl,os,sys; f=os.open(sys.argv[1],os.O_RDONLY); "
        "fcntl.flock(f,fcntl.LOCK_SH if sys.argv[2]=='1' else fcntl.LOCK_EX); "
        "print('locked',flush=True); sys.stdin.read(); os.close(f)"
    )
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(path), str(int(shared))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout.readline().strip() == "locked"
    return process


@pytest.mark.parametrize("kind", ("maintenance", "repository", "checkpoint"))
def test_real_lock_after_plan_allows_new_plan_without_resetting_history(case, monkeypatch, kind):
    p, _, settings, _, _, _, _, host = case
    enable_recurring(case, monkeypatch)
    original = safe.ClockRetirement.plan
    held = []
    before = {str(f): f.read_bytes() for f in p.backup_dir.glob("*/*/backup.json")}
    archived = p.state_file.read_bytes()

    def plan(self):
        result = original(self)
        path = {
            "maintenance": p.store_root / "state/maintenance.lock",
            "repository": p.state_root / p.expected_repository_id / "archive.lock",
            "checkpoint": Path(result["candidates"][0]["entry"]["path"]).parent,
        }[kind]
        held.append(holder(path, shared=kind == "checkpoint"))
        return result

    with monkeypatch.context() as m:
        m.setattr(safe.ClockRetirement, "plan", plan)
        try:
            first = safe.execute(settings, "run", host_factory=host)
        finally:
            for proc in held:
                proc.communicate(timeout=10)
                assert proc.returncode == 0
    assert first["state"] == "deferred" and first["exit_code"] == 75, first
    assert first["deletion_attempted"] and first["deleted_this_run"] == 0
    assert safe.safely_deferred_before_application(first), first
    assert not list((settings.state_root / "retention/journals").glob("*.json"))
    assert before == {str(f): f.read_bytes() for f in p.backup_dir.glob("*/*/backup.json")}
    assert archived == p.state_file.read_bytes()
    old_report = Path(first["report"]).read_bytes()
    next_run = safe.execute(settings, "run", host_factory=host)
    assert next_run["state"] == "complete" and next_run["deleted_this_run"] == 2, next_run
    assert next_run["plan_id"] != first["plan_id"]
    assert Path(first["report"]).read_bytes() == old_report
    zero = safe.execute(settings, "run", host_factory=host)
    assert zero["state"] == "no_candidates" and zero["deleted_this_run"] == 0, zero
    assert zero["plan_id"] not in (first["plan_id"], next_run["plan_id"])
    assert archived == p.state_file.read_bytes()


def test_uncertified_prejournal_busy_still_blocks_next_automatic_plan(case, monkeypatch):
    enable_recurring(case, monkeypatch)
    with monkeypatch.context() as m:
        m.setattr(
            safe.ClockRetirement,
            "apply",
            lambda *a: (_ for _ in ()).throw(safe.archive.Busy("unknown phase")),
        )
        first = safe.execute(case[2], "run", host_factory=case[-1])
    assert first["deletion_attempted"] and not safe.safely_deferred_before_application(first)
    following = safe.execute(case[2], "run", host_factory=case[-1])
    assert following["reason"] == "unfinished_retirement" and following["needs_attention"], (
        following
    )


def fast_run(root, checkpoint, pipe=None, close_error=False, persist_error=False):
    """Real runner/persistence with synthetic workload for kill/finalize boundaries."""
    binding = root / "binding.json"
    binding.write_text("{}")
    settings = SimpleNamespace(
        state_root=root / "state", retention_policy=root / "policy", clock_binding_file=binding
    )
    instant = datetime(2024, 1, 7, 6, 40, tzinfo=UTC)

    class Host:
        def __init__(self, binding):
            pass

        @contextmanager
        def lock(self):
            yield self

        def query(self):
            raise AssertionError("No actual provider")

    class Guard:
        def __init__(self, *args):
            pass

        def start(self):
            return self

        def check(self, **kwargs):
            return {"ready": True}

        def close(self):
            if close_error:
                raise OSError("monitor finalization failed")

    class Agent:
        def __init__(self, *args):
            self.root = settings.state_root / "retention"

        def plan(self):
            return {"plan_id": "a" * 64, "candidates": [{"synthetic": True}]}

    real_agent = safe.ClockRetirement

    # Agent.apply must reference the real class even while execute's factory is patched.
    def apply(self, pid):
        actual = object.__new__(real_agent)
        with actual.application_locks(failure(safe.archive.Busy("held"), "enter"), "preservation"):
            raise AssertionError("Apply entered")

    Agent.apply = apply
    original = retention.persist

    def persist(path, value):
        terminal = value.get("phase") == "finished"
        if terminal and persist_error:
            raise OSError("final report unavailable")
        if terminal and checkpoint == "before_final":
            pipe.send(str(path))
            signal.pause()
        original(path, value)
        if (value.get("phase") == "applying" and checkpoint == "after_intent") or (
            terminal and checkpoint == "after_final"
        ):
            pipe.send(str(path))
            signal.pause()

    with (
        patch.object(
            safe.retirement.Policy,
            "load",
            return_value=SimpleNamespace(archive_profile=root / "profile"),
        ),
        patch.object(safe.archive.Profile, "load", return_value=SimpleNamespace()),
        patch.object(safe, "authorize", return_value={}),
        patch.object(safe, "installed_service"),
        patch.object(
            safe,
            "system_context",
            return_value={
                "service_verified": True,
                "scheduled": True,
                "trigger_at": instant.isoformat(),
            },
        ),
        patch.object(safe.clock, "Guard", Guard),
        patch.object(safe, "now", return_value=instant),
        patch.object(safe, "ClockRetirement", Agent),
        patch.object(retention, "persist", persist),
    ):
        return safe.execute(settings, "run", host_factory=Host)


@pytest.mark.parametrize(
    "where,allowed", [("after_intent", False), ("before_final", False), ("after_final", True)]
)
def test_sigkill_only_durable_safe_finalization_allows_reevaluation(tmp_path, where, allowed):
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=fast_run, args=(tmp_path, where, child))
    process.start()
    try:
        assert parent.poll(15), "Child did not reach the specified boundary"
        path = Path(parent.recv())
        os.kill(process.pid, signal.SIGKILL)
        process.join(10)
        assert process.exitcode == -signal.SIGKILL
        report = json.loads(path.read_bytes())
        assert bool(safe.safely_deferred_before_application(report)) is allowed
        assert report["deletion_attempted"] and report["deleted_this_run"] == 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        parent.close()
        child.close()


def test_close_failure_keeps_request_for_review(tmp_path):
    report = fast_run(tmp_path, None, close_error=True)
    assert report["state"] == "failed" and not safe.safely_deferred_before_application(report)


def test_final_write_failure_leaves_original_intent(tmp_path):
    with pytest.raises(OSError, match="final report unavailable"):
        fast_run(tmp_path, None, persist_error=True)
    records = list((tmp_path / "state/runs").glob("*/run.json"))
    assert len(records) == 1
    report = json.loads(records[0].read_bytes())
    assert report["phase"] == "applying" and report["deletion_attempted"]
    assert not safe.safely_deferred_before_application(report)
