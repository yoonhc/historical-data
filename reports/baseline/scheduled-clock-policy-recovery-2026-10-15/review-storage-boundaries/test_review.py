"""Characterize the fixed candidate; expected findings are not corrected behavior.

Only temporary files/SQLite and fake time/HTTP are used. No candidate edits.
"""

import copy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest
from conftest import store  # noqa: F401
from marketdata import job_clock as jc
from test_daily_schedule import env, tasks  # noqa: F401
from test_daily_clock import setup

ROOT = Path(__file__).resolve().parents[1]


def contract():
    spec = importlib.util.spec_from_file_location(
        "review_request_contract", ROOT / "candidate/policy/request_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_record_bytes_connection_is_not_explicitly_closed(tmp_path, monkeypatch):
    module = contract()
    path = tmp_path / "record.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE daily_runs(id TEXT PRIMARY KEY, record TEXT)")
    db.execute("INSERT INTO daily_runs VALUES ('run', '{}')")
    db.commit()
    db.close()
    original, retained = sqlite3.connect, []

    def connect(*a, **kw):
        db = original(*a, **kw)
        retained.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        assert module.record_bytes({"table": "daily_runs", "path": str(path), "id": "run"}) == b"{}"
        # Keeping a reference removes GC timing from this lifecycle observation.
        assert retained[0].execute("SELECT 1").fetchone()[0] == 1
    finally:
        for db in retained:
            db.close()


@pytest.mark.parametrize("job,filename", [
    ("oi-probe", "oi-probe-latest.json"), ("network-observer", "latest.json")
])
def test_normal_later_latest_write_rejects_prior_result(tmp_path, job, filename):
    module = contract()
    path = tmp_path / filename
    result = {"probe_id": "A", "source_id": "source-A", "complete": True,
              "status": "ok", "finished_at": "2026-10-15T00:00:00Z", "failed_probes": []}
    path.write_text(json.dumps(result))
    sealed = module.seal_result({"job": job, "result": result, "files": [str(path)], "records": []})
    assert module.classify(job, 0, sealed, {}, [])["state"] == "complete"
    path.write_text(json.dumps({**result, "probe_id": "B", "finished_at": "2026-10-15T00:05:00Z"}))
    with pytest.raises(module.RequestError, match="Committed report changed"):
        module.classify(job, 0, sealed, {}, [])


def test_pending_failure_atomic_commit_and_exactly_one_accounting(env):
    clock, upstream, runner, key, url, samples, context = setup(env)
    query, original_failure = context.query, runner.failure
    upstream.responses[url] = (503, b"temporary server error", {})
    lost = [False]

    def fail(task, attempt, exc):
        lost[0] = True
        return original_failure(task, attempt, exc)

    def query_current():
        if lost[0]:
            raise jc.ClockDeferred("provider_query_timeout")
        return query()

    runner.failure, context.query = fail, query_current
    with jc.using(context), pytest.raises(jc.ClockDeferred):
        runner.run()
    assert tasks(runner, "acquire")[0]["transient_failures"] == 0
    assert runner.ledger.rows("daily_attempts")[0]["pending_failure"]["type"] == "SourceError"
    runner.failure, lost[0] = original_failure, False
    context.restart_observation()
    with runner.store.db:
        runner.store.db.execute("""
            CREATE TRIGGER interrupt_failure BEFORE UPDATE ON daily_attempts
            WHEN json_extract(NEW.record, '$.state')='retry_wait'
            BEGIN SELECT RAISE(ABORT, 'injected after task update'); END
        """)
    with jc.using(context), pytest.raises(sqlite3.IntegrityError):
        runner.run()
    # The task UPDATE was executed before the trigger. The transaction rolled it back.
    assert tasks(runner, "acquire")[0]["transient_failures"] == 0
    assert runner.ledger.rows("daily_attempts")[0].get("pending_failure")
    with runner.store.db:
        runner.store.db.execute("DROP TRIGGER interrupt_failure")
    calls = len(upstream.calls)
    with jc.using(context):
        runner.run()
        runner.run()
    assert tasks(runner, "acquire")[0]["transient_failures"] == 1
    assert not runner.ledger.rows("daily_attempts")[0].get("pending_failure")
    assert len(upstream.calls) == calls


class Interrupted(BaseException):
    pass


@pytest.mark.parametrize("boundary", ["after_task", "before_attempt"])
def test_recovery_completion_second_interruption_is_idempotent(env, monkeypatch, boundary):
    clock, upstream, runner, key, url, samples, context = setup(env)

    def hold(phase, attempt):
        if phase == "before_complete":
            raise jc.ClockDeferred("post_storage_time_hold")

    runner.fault = hold
    with jc.using(context), pytest.raises(jc.ClockDeferred):
        runner.run()
    before = runner.ledger.rows("daily_attempts")[0]
    attempt_id = before["id"]
    task_id = before["task_id"]
    original_save = runner.ledger.save

    def crash(table, value):
        if boundary == "before_attempt" and table == "daily_attempts" and value["id"] == attempt_id:
            raise Interrupted()
        original_save(table, value)
        if boundary == "after_task" and table == "daily_tasks" and value["id"] == task_id:
            raise Interrupted()

    context.restart_observation()
    monkeypatch.setattr(runner.ledger, "save", crash)
    with runner.ledger.exclusive(), jc.using(context), pytest.raises(Interrupted):
        runner.recover()
    held = tasks(runner, "acquire")[0]
    assert held["state"] == "complete"
    assert runner.ledger.rows("daily_attempts")[0]["state"] == "deferred_clock"
    calls = len(upstream.calls)
    revisions = runner.store.db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
    # Fresh Scheduler reads the committed task and unfinalized attempt again.
    restarted = env[2]([key])
    with jc.using(context):
        assert restarted.run()["data_complete"]
        assert restarted.run()["data_complete"]
    completed = tasks(restarted, "acquire")[0]
    assert completed["proof"] == held["proof"]
    assert completed["transient_failures"] == completed["validation_failures"] == 0
    assert len(upstream.calls) == calls
    assert runner.store.db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == revisions
    attempts = restarted.ledger.rows("daily_attempts")
    assert len(attempts) == 1 and attempts[0]["id"] == attempt_id
    assert attempts[0]["state"] == "recovered_complete"
    assert all(t["state"] == "complete" and t["proof"] == completed["proof"] for t in tasks(restarted))


def test_pending_oserror_is_reconstructed_as_dataerror(env):
    clock, upstream, runner, key, url, samples, context = setup(env)
    with runner.ledger.exclusive():
        runner.plan(clock())
    task = tasks(runner, "acquire")[0]
    direct = copy.deepcopy(task)
    direct["id"] = "direct-case"
    direct_attempt = {"id": "direct-attempt", "task_id": direct["id"], "state": "running",
                      "requests": [], "inputs": []}
    with jc.using(context):
        runner.failure(direct, direct_attempt, OSError("disk failure"))
    assert direct["state"] == "needs_review" and direct["validation_failures"] == 0

    attempt = {"id": "deferred-attempt", "task_id": task["id"], "state": "running",
               "requests": [], "inputs": []}
    task.update(state="running", attempts=[attempt["id"]])
    runner.ledger.save_many([("daily_tasks", task), ("daily_attempts", attempt)])
    query = context.query
    context.query = lambda: (_ for _ in ()).throw(jc.ClockDeferred("provider_query_timeout"))
    with jc.using(context), pytest.raises(jc.ClockDeferred):
        runner.failure(task, attempt, OSError("disk failure"))
    assert next(a for a in runner.ledger.rows("daily_attempts") if a["id"] == attempt["id"])["pending_failure"]["type"] == "OSError"
    context.query = query
    context.restart_observation()
    with runner.ledger.exclusive(), jc.using(context):
        runner.recover()
    recovered = next(t for t in tasks(runner) if t["id"] == task["id"])
    assert recovered["state"] == "validation_retry" and recovered["validation_failures"] == 1
    assert not upstream.calls
