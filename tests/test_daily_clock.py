# ruff: noqa: F811 -- imported pytest fixtures are injected by parameter name.
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path

import pytest
from marketdata import job_clock as jc
from marketdata.binance import SourceError
from marketdata.domain import Key, utc
from test_clock_decisions import Samples, context
from test_daily_schedule import EVENING, env, tasks  # noqa: F401


def setup(env, **tracking):
    clock, upstream, build = env
    clock.value = EVENING + timedelta(seconds=2)
    s = Samples(clock.value.isoformat())
    c, events = context(s, **tracking)
    key = Key("trade_bars", interval="1h")
    url = upstream.add(key)
    runner = build([key])
    return clock, upstream, runner, key, url, s, c


def test_old_reference_real_storage_then_zero_duplicate_requests(store, env):
    clock, upstream, runner, key, url, s, c = setup(env)
    with jc.using(c):
        first = runner.run()
        second = runner.run()
    assert first["runner_ok"] and first["data_complete"]
    assert not second["processed"] and upstream.calls == [url, url + ".CHECKSUM"]
    assert store.validate()["ok"]
    assert first["clock_observation"]["reference_age_seconds"] == 7200


def test_first_eligibility_wait_is_not_failure_and_original_target_resumes(env):
    clock, upstream, runner, key, url, s, c = setup(env, root_dispersion=3)
    with jc.using(c), pytest.raises(jc.ClockDeferred, match="deadline"):
        runner.run()
    assert not upstream.calls and not runner.ledger.rows("daily_attempts")
    ids = {t["id"] for t in tasks(runner, "acquire")}
    assert ids
    clock.value += timedelta(seconds=3)
    s.advance(3)
    c.restart_observation()
    with jc.using(c):
        result = runner.run()
    assert result["data_complete"] and ids == {t["id"] for t in tasks(runner, "acquire")}
    assert all(t["transient_failures"] == 0 for t in tasks(runner))


@pytest.mark.parametrize("header", ["3600", "http-date"])
def test_persisted_server_cooldown_uses_upper_and_lower_bounds(env, header):
    clock, upstream, runner, key, url, s, c = setup(env)
    requested = clock.value + timedelta(seconds=3600)
    value = format_datetime(requested, usegmt=True) if header == "http-date" else header
    upstream.responses[url] = (429, b"limited", {"retry-after": value})
    with jc.using(c):
        runner.run()
    row = runner.ledger.rows("daily_cooldowns")[0]
    assert utc(row["until"]) >= requested
    # A point whose center equals the deadline still cannot authorize a request.
    clock.value = utc(row["until"])
    s.wall = clock.value.timestamp()
    c.restart_observation()
    calls = len(upstream.calls)
    with jc.using(c), pytest.raises(jc.ClockDeferred):
        runner.run()
    assert len(upstream.calls) == calls
    clock.value += timedelta(seconds=1)
    s.advance(1)
    c.restart_observation()
    upstream.add(key)
    with jc.using(c):
        assert runner.run()["data_complete"]


def test_429_received_then_clock_loss_preserves_limit_without_spending_failure_budget(env):
    clock, upstream, runner, key, url, s, c = setup(env)
    query = c.query
    lost = [False]

    def limited(request):
        lost[0] = True
        return 429, b"limited", {"retry-after": "7200"}

    upstream.responses[url] = limited

    def query_current():
        if lost[0]:
            raise jc.ClockDeferred("provider_query_timeout")
        return query()

    c.query = query_current
    with jc.using(c), pytest.raises(jc.ClockDeferred):
        runner.run()
    held = runner.ledger.rows("daily_cooldowns")[0]
    assert held["pending_clock_cooldown"] and held["headers"]["retry-after"] == "7200"
    attempt = runner.ledger.rows("daily_attempts")[0]
    assert attempt["requests"][0]["status"] == 429 and attempt["state"] == "deferred_clock"
    assert tasks(runner, "acquire")[0]["transient_failures"] == 0
    lost[0] = False
    c.restart_observation()
    with jc.using(c):
        runner.run()
    assert len(upstream.calls) == 1
    with jc.using(c), pytest.raises(SourceError):
        runner.check_cooldown(url)
    assert utc(runner.ledger.rows("daily_cooldowns")[0]["until"]).timestamp() >= s.wall + 7200
    assert not runner.ledger.rows("daily_attempts")[0].get("pending_failure")


def test_storage_commit_then_time_hold_recovers_without_redownload(env):
    clock, upstream, runner, key, url, s, c = setup(env)

    def hold(phase, attempt):
        if phase == "before_complete":
            raise jc.ClockDeferred("lost after catalog commit")

    runner.fault = hold
    with jc.using(c), pytest.raises(jc.ClockDeferred):
        runner.run()
    calls = list(upstream.calls)
    runner.fault = lambda *a: None
    c.restart_observation()
    with jc.using(c):
        result = runner.run()
    assert result["data_complete"] and upstream.calls == calls
    assert runner.ledger.rows("daily_attempts")[0]["state"] == "recovered_complete"
    assert tasks(runner, "acquire")[0]["transient_failures"] == 0


def test_real_transport_failure_is_counted_once_after_clock_recovers(env):
    clock, upstream, runner, key, url, s, c = setup(env)
    query = c.query
    lost = [False]
    upstream.responses[url] = (503, b"error", {})
    original = runner.failure

    def fail(task, attempt, exc):
        lost[0] = True
        return original(task, attempt, exc)

    runner.failure = fail
    c.query = lambda: (_ for _ in ()).throw(jc.ClockDeferred("unavailable")) if lost[0] else query()
    with jc.using(c), pytest.raises(jc.ClockDeferred):
        runner.run()
    assert tasks(runner, "acquire")[0]["transient_failures"] == 0
    runner.failure = original
    lost[0] = False
    c.restart_observation()
    with jc.using(c):
        runner.run()
        runner.run()
    assert tasks(runner, "acquire")[0]["transient_failures"] == 1


def test_time_records_survive_fresh_process_backup_restore_and_runtime_read(store, env, tmp_path):
    import subprocess
    import sys

    clock, upstream, runner, key, url, s, c = setup(env)
    with jc.using(c):
        assert runner.run()["data_complete"]
    original = runner.ledger.rows("daily_tasks")
    code = """
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]+'/packages/marketdata/src')
from marketdata import backup
from marketdata.store import Store
from marketdata.daily_schedule import read_status
root,cp,out=map(Path,sys.argv[2:])
s=Store(root)
try:backup.create_backup(s,cp,min_free_gib=0)
finally:s.close()
assert backup.verify_backup(cp)['ok']
backup.restore_backup(cp,out,min_free_gib=0)
s=Store(out)
try:
 records=[json.loads(r[0]) for r in s.db.execute('SELECT record FROM daily_tasks')]
 assert any(r.get('clock_observation',{}).get('reference_age_seconds')==7200 for r in records)
 assert s.validate()['ok'] and read_status(out)['data_complete']
 s.put_raw(b'after rollback','test:rollback','test')
finally:s.close()
"""
    r = subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-c",
            code,
            str(Path(__file__).resolve().parents[1]),
            str(store.root),
            str(tmp_path / "checkpoint"),
            str(tmp_path / "restored"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert runner.ledger.rows("daily_tasks") == original


def test_time_hold_before_HTTP_preserves_manual_grant_and_inner_job_reason(env):
    clock, upstream, runner, key, url, s, c = setup(env)
    with runner.ledger.exclusive():
        runner.plan(clock())
    task = tasks(runner, "acquire")[0]
    task["manual_retry"] = {"request_id": "isolated-approved-grant", "remaining": 1}
    runner.ledger.save("daily_tasks", task)
    original = c.point

    def before_http(reason):
        if reason == "http_request":
            raise jc.ClockDeferred("query lost before HTTP")
        return original(reason)

    c.point = before_http
    with jc.using(c), pytest.raises(jc.ClockDeferred):
        runner.run()
    assert not upstream.calls
    task = tasks(runner, "acquire")[0]
    assert task["manual_retry"]["remaining"] == 1 and task["transient_failures"] == 0
    assert {r[0] for r in runner.store.db.execute("SELECT state FROM jobs")} == {"deferred_clock"}
    c.point = original
    clock.value += timedelta(minutes=10)
    s.advance(600)
    c.restart_observation()
    with jc.using(c):
        assert runner.run()["data_complete"]
    assert tasks(runner, "acquire")[0]["manual_retry"]["remaining"] == 0
