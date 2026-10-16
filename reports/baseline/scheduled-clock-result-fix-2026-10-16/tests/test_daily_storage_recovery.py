"""Failure meaning and interruption boundaries use the real temporary ledger."""

# ruff: noqa: F811 -- pytest imports fixtures and injects parameters with their names.
import copy
import sqlite3
from datetime import timedelta

import pytest
from conftest import store  # noqa: F401
from marketdata import job_clock as jc
from marketdata.binance import SourceError
from marketdata.domain import ConflictError, DataError, WriterBusy
from test_daily_clock import setup
from test_daily_schedule import env, tasks  # noqa: F401


class ValidationSubclass(DataError):
    pass


class SourceSubclass(SourceError):
    pass


class UnknownFailure(Exception):
    pass


@pytest.mark.parametrize(
    "exc,legacy",
    [
        (OSError("disk failure"), False),
        (OSError("disk failure"), True),
        (ValueError("invalid setting"), False),
        (KeyError("unknown field"), False),
        (sqlite3.OperationalError("database error"), False),
        (DataError("validation"), False),
        (DataError("validation"), True),
        (ValidationSubclass("validation subclass"), False),
        (ConflictError("conflict"), False),
        (WriterBusy("writer busy"), False),
        (SourceError("server", 503, url="https://example.invalid/archive"), False),
        (
            SourceSubclass(
                "rate limited",
                429,
                url="https://example.invalid/archive",
                headers={"retry-after": "7200"},
            ),
            False,
        ),
        (UnknownFailure("unexpected"), False),
        (UnknownFailure("unexpected"), True),
    ],
)
def test_failure_meaning_survives_repeated_clock_deferral(env, exc, legacy):
    clock, upstream, runner, key, url, samples, context = setup(env)
    with runner.ledger.exclusive():
        runner.plan(clock())
    task = tasks(runner, "acquire")[0]
    direct = copy.deepcopy(task)
    direct["id"] = "direct-case"
    direct_attempt = dict(
        id="direct-attempt", task_id=direct["id"], state="running", requests=[], inputs=[]
    )
    with jc.using(context):
        runner.failure(direct, direct_attempt, exc)
    attempt = dict(
        id="deferred-attempt", task_id=task["id"], state="running", requests=[], inputs=[]
    )
    task.update(state="running", attempts=[attempt["id"]])
    runner.ledger.save_many([("daily_tasks", task), ("daily_attempts", attempt)])
    query = context.query
    context.query = lambda: (_ for _ in ()).throw(jc.ClockDeferred("provider_query_timeout"))
    with jc.using(context), pytest.raises(jc.ClockDeferred):
        runner.failure(task, attempt, exc)
    if legacy:
        attempt["pending_failure"].pop("category")
        runner.ledger.save("daily_attempts", attempt)
    context.restart_observation()
    with jc.using(context), runner.ledger.exclusive(), pytest.raises(jc.ClockDeferred):
        runner.recover()
    held = next(a for a in runner.ledger.rows("daily_attempts") if a["id"] == attempt["id"])
    assert held["pending_failure"]["type"] == type(exc).__name__
    context.query = query
    context.restart_observation()
    with jc.using(context), runner.ledger.exclusive():
        runner.recover()
        runner.recover()
    recovered = next(t for t in tasks(runner) if t["id"] == task["id"])
    for name in (
        "state",
        "transient_failures",
        "validation_failures",
        "publication_checks",
        "review_required",
    ):
        assert recovered[name] == direct[name], name
    result = next(a for a in runner.ledger.rows("daily_attempts") if a["id"] == attempt["id"])
    assert result["error"] == direct_attempt["error"]
    assert "pending_failure" not in result and not upstream.calls
    if isinstance(exc, SourceError) and exc.status == 429:
        from marketdata.domain import utc

        assert utc(runner.ledger.rows("daily_cooldowns")[0]["until"]) >= clock() + timedelta(
            seconds=7200
        )


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
    assert all(
        t["state"] == "complete" and t["proof"] == completed["proof"] for t in tasks(restarted)
    )
