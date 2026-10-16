import json
import sqlite3
import subprocess
import sys
from datetime import timedelta

import httpx
import pytest
from conftest import zip_csv
from marketdata import backup
from marketdata.binance import archive_url
from marketdata.daily_schedule import Schedule, Scheduler, load_config, read_status
from marketdata.domain import DAY, DataError, Key, WriterBusy, json_bytes, micros, sha256, utc
from marketdata.operations import SyncPolicy
from marketdata.store import Store

DAY0 = "2024-01-14"
NOON = utc("2024-01-15T03:25:00Z")
EVENING = utc("2024-01-15T09:00:00Z")


class Clock:
    def __init__(self, value=EVENING):
        self.value = value

    def __call__(self):
        return self.value


class Upstream:
    def __init__(self):
        self.responses, self.calls = {}, []

    def handle(self, request):
        url = str(request.url)
        self.calls.append(url)
        response = self.responses.get(url, (404, b"not found", {}))
        if callable(response):
            response = response(request)
        if isinstance(response, Exception):
            raise response
        return httpx.Response(response[0], content=response[1], headers=response[2])

    def add(self, key, day=DAY0, *, price="100", short=False):
        url = archive_url(key, utc(day), False)
        if key.dataset == "open_interest":
            rows = [["create_time", "symbol", "sum_open_interest", "sum_open_interest_value"]]
            rows += [
                [str(utc(day) + timedelta(minutes=5 * i)), "BTCUSDT", "10", "1000"]
                for i in range(288)
            ]
        else:
            rows = []
            for i in range(86400 // key.step - int(short)):
                ts = utc(day) + timedelta(seconds=i * key.step)
                stamp = micros(ts) // 1000
                rows.append(
                    [
                        stamp,
                        price,
                        "105",
                        "95",
                        "101",
                        "2",
                        stamp + key.step * 1000 - 1,
                        "200",
                        3,
                        "1",
                        "100",
                        "0",
                    ]
                )
        blob = zip_csv(rows, url.split("/")[-1][:-4] + ".csv")
        self.responses[url] = (200, blob, {})
        self.responses[url + ".CHECKSUM"] = (
            200,
            f"{sha256(blob)}  {url.split('/')[-1]}".encode(),
            {},
        )
        return url


@pytest.fixture
def env(store, monkeypatch):
    clock, upstream = Clock(), Upstream()
    monkeypatch.setattr("marketdata.binance.now", clock)
    monkeypatch.setattr("marketdata.pipeline.now", clock)
    client = httpx.Client(transport=httpx.MockTransport(upstream.handle))

    def build(keys, *, start=DAY0, refresh=3, fault=None, readiness=None, **options):
        return Scheduler(
            store,
            keys,
            SyncPolicy(start=start, refresh_days=refresh),
            Schedule(**options),
            client=client,
            clock=clock,
            sleep=lambda _: None,
            fault=fault,
            readiness=readiness,
        )

    yield clock, upstream, build
    client.close()


def tasks(runner, purpose=None):
    return [
        r for r in runner.ledger.rows("daily_tasks") if purpose is None or r["purpose"] == purpose
    ]


def test_noon_and_evening_select_only_due_incomplete_targets(store, env):
    clock, upstream, build = env
    spot = Key("trade_bars", market="spot", interval="1h")
    good = Key("trade_bars", interval="1h")
    late = Key("mark_price_bars", interval="1h")
    spot_url, good_url = upstream.add(spot), upstream.add(good)
    runner = build([spot, good, late])
    clock.value = NOON
    report = runner.run()
    assert not report["data_complete"]
    assert upstream.calls == [spot_url, spot_url + ".CHECKSUM"]
    assert len(tasks(runner, "acquire")) == 3
    clock.value = EVENING
    runner.run()
    assert upstream.calls.count(spot_url) == 1
    assert upstream.calls.count(good_url) == 1
    waiting = next(t for t in tasks(runner, "acquire") if t["key"] == late.asdict())
    assert waiting["state"] == "waiting_source"
    assert utc(waiting["next_at"]) == utc("2024-01-15T11:00Z")
    # Same wakeup, repeated process invocation: no more network traffic.
    count = len(upstream.calls)
    build([spot, good, late]).run()
    assert len(upstream.calls) == count
    late_url = upstream.add(late)
    clock.value = utc("2024-01-15T11:00Z")
    report = build([spot, good, late]).run()
    assert report["data_complete"]
    assert upstream.calls[-2:] == [late_url, late_url + ".CHECKSUM"]


def test_old_missing_day_survives_refresh_window_and_restarts(store, env):
    clock, upstream, build = env
    key = Key("mark_price_bars", interval="1h")
    runner = build([key], refresh=1)
    runner.run()
    first = tasks(runner, "acquire")[0]
    for offset in range(1, 6):
        clock.value = EVENING + offset * DAY
        build([key], refresh=1).run()
    old = next(t for t in tasks(runner, "acquire") if t["day"] == DAY0)
    assert old["id"] == first["id"] and old["state"] == "waiting_source"
    assert old["review_required"] and old["publication_checks"] == 6
    assert utc(old["next_at"]) == EVENING + 6 * DAY
    upstream.add(key)
    clock.value += DAY
    build([key], refresh=1).run()
    assert next(t for t in tasks(runner, "acquire") if t["day"] == DAY0)["state"] == "complete"


def test_restart_coalesces_missed_slots_instead_of_replaying_them(store, env):
    clock, upstream, build = env
    key = Key("mark_price_bars", interval="1h")
    runner = build([key])
    runner.run()
    clock.value = utc("2024-01-15T14:59Z")  # 23:59 KST, missed 20/22h slots
    build([key]).run()
    assert len(upstream.calls) == 2
    assert utc(tasks(runner, "acquire")[0]["next_at"]) == utc("2024-01-16T09:00Z")


def test_refresh_is_once_per_cycle_and_acquisition_coalesces(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    url = upstream.add(key)
    runner = build([key])
    runner.run()
    assert all(t["state"] == "complete" for t in tasks(runner))
    assert len(upstream.calls) == 2
    for hour in (11, 13, 14):
        clock.value = utc(f"2024-01-15T{hour}:00Z")
        build([key]).run()
    assert len(upstream.calls) == 2
    clock.value = EVENING + DAY
    upstream.add(key, "2024-01-15")
    runner.run()
    assert upstream.calls.count(url) == 2  # next cycle really checks the old file once
    runner.run()
    assert upstream.calls.count(url) == 2


@pytest.mark.parametrize("phase", ["after_publish", "before_complete"])
def test_commit_before_state_recovers_without_request_or_revision_duplication(store, env, phase):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)

    def crash(where, attempt):
        if where == phase:
            raise KeyboardInterrupt("simulated abrupt exit")

    runner = build([key], fault=crash)
    with pytest.raises(KeyboardInterrupt):
        runner.run()
    before = store.db.execute("SELECT count(*) FROM revisions").fetchone()[0]
    assert before == 1
    requests = len(upstream.calls)
    restarted = build([key])
    assert restarted.run()["data_complete"]
    assert len(upstream.calls) == requests
    assert store.db.execute("SELECT count(*) FROM revisions").fetchone()[0] == before
    assert restarted.ledger.rows("daily_attempts")[0]["state"] == "recovered_complete"


def test_download_only_crash_is_not_complete(store, env, monkeypatch):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)
    original = Store.publish

    def crash(*args, **kwargs):
        raise KeyboardInterrupt("before publication")

    monkeypatch.setattr(Store, "publish", crash)
    with pytest.raises(KeyboardInterrupt):
        build([key]).run()
    assert store.db.execute("SELECT count(*) FROM active").fetchone()[0] == 0
    monkeypatch.setattr(Store, "publish", original)
    runner = build([key])
    assert not runner.run()["data_complete"]  # persisted interruption cooldown
    clock.value += timedelta(minutes=5)
    assert runner.run()["data_complete"]
    assert len(upstream.calls) == 4


def test_429_cooldown_is_durable_and_shared_only_for_same_host(store, env):
    clock, upstream, build = env
    a = Key("trade_bars", interval="1h")
    b = Key("mark_price_bars", interval="1h")
    url = archive_url(a, utc(DAY0), False)
    upstream.responses[url] = (429, b"slow", {"Retry-After": "3600"})
    runner = build([a, b])
    runner.run()
    assert len(upstream.calls) == 1
    build([a, b]).run()
    assert len(upstream.calls) == 1
    assert all(t["state"] == "cooldown" for t in tasks(runner, "acquire"))
    assert runner.ledger.rows("daily_cooldowns")[0]["id"] == "data.binance.vision"
    upstream.add(a)
    upstream.add(b)
    clock.value += timedelta(hours=1)
    assert build([a, b]).run()["data_complete"]


@pytest.mark.parametrize("value", [None, "bad", "Mon, 15 Jan 2024 10:00:00 GMT"])
def test_retry_after_optional_and_http_date(store, env, value):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    url = archive_url(key, utc(DAY0), False)
    headers = {"Retry-After": value} if value else {}
    upstream.responses[url] = (429, b"", headers)
    runner = build([key])
    runner.run()
    next_at = utc(tasks(runner, "acquire")[0]["next_at"])
    assert next_at >= EVENING + timedelta(minutes=15)
    assert len(upstream.calls) == 1


def test_transport_retry_has_bounded_inner_and_outer_budgets(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.responses[archive_url(key, utc(DAY0), False)] = httpx.ConnectError("unavailable")
    runner = build([key])
    for index in range(3):
        build([key]).run()
        assert len(upstream.calls) == (index + 1) * 4
        clock.value += timedelta(minutes=5)
    assert tasks(runner, "acquire")[0]["state"] == "needs_review"
    assert tasks(runner, "acquire")[0]["transient_failures"] == 3
    build([key]).run()
    assert len(upstream.calls) == 12


@pytest.mark.parametrize("status", [400, 401, 403])
def test_permanent_http_errors_do_not_spin(store, env, status):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.responses[archive_url(key, utc(DAY0), False)] = (status, b"error", {})
    runner = build([key])
    runner.run()
    runner.run()
    assert len(upstream.calls) == 1
    assert tasks(runner, "acquire")[0]["state"] == "needs_review"


def test_checksum_unavailable_and_bad_pair_are_separate(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    url = upstream.add(key)
    upstream.responses.pop(url + ".CHECKSUM")
    runner = build([key])
    runner.run()
    assert tasks(runner, "acquire")[0]["state"] == "waiting_source"
    clock.value = utc("2024-01-15T11:00Z")
    upstream.responses[url + ".CHECKSUM"] = (200, b"bad checksum", {})
    runner.run()
    assert tasks(runner, "acquire")[0]["state"] == "validation_retry"
    clock.value = utc("2024-01-15T13:00Z")
    runner.run()
    assert tasks(runner, "acquire")[0]["state"] == "needs_review"
    attempts = runner.ledger.rows("daily_attempts")
    assert [r["id"] for r in attempts[-1]["inputs"]] == [r["id"] for r in attempts[-2]["inputs"]]
    assert attempts[-1]["inputs"][0]["received_at"] != attempts[-2]["inputs"][0]["received_at"]


def test_changed_bad_revision_never_overwrites_prior_good_data(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)
    runner = build([key])
    runner.run()
    before = store.db.execute("SELECT revision_id FROM active WHERE day=?", (DAY0,)).fetchone()[0]
    upstream.add(key, price="102")
    upstream.add(key, "2024-01-15")
    clock.value += DAY
    runner.run()
    assert (
        store.db.execute("SELECT revision_id FROM active WHERE day=?", (DAY0,)).fetchone()[0]
        == before
    )
    assert any(t["state"] == "needs_review" for t in tasks(runner, "refresh"))
    assert store.db.execute("SELECT count(*) FROM issues WHERE kind='conflict'").fetchone()[0] > 0


def test_clock_hold_and_lock_do_not_spend_network_retry_budget(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    runner = build([key], readiness=lambda: {"ready": False, "reason": "strict gate"})
    runner.run()
    assert not upstream.calls
    task = tasks(runner, "acquire")[0]
    assert task["state"] == "deferred_clock" and task["attempts"] == []
    with runner.ledger.exclusive():
        with pytest.raises(WriterBusy):
            build([key]).run()


def test_metadata_late_observation_is_not_fabricated_historical_snapshot(store, env):
    clock, upstream, build = env
    key = Key("instrument_metadata", market="spot", interval="event")
    url = "https://api.binance.com/api/v3/exchangeInfo"
    upstream.responses[url] = (503, b"wait", {})
    clock.value = NOON
    runner = build([key])
    runner.run()
    clock.value += DAY
    upstream.responses[url] = (
        200,
        json_bytes({"symbols": [{"symbol": "BTCUSDT", "status": "TRADING"}]}),
        {},
    )
    runner.run()
    observations = tasks(runner)
    assert [t["state"] for t in observations] == ["missed_observation", "complete"]
    rows = store.read(key, "2024-01-15", "2024-01-17").to_pylist()
    assert len(rows) == 1 and rows[0]["timestamp"] == clock.value


def test_rest_404_is_not_archive_waiting_source(store, env):
    clock, upstream, build = env
    key = Key("funding_events", interval="event")
    runner = build([key])
    runner.run()
    assert tasks(runner, "acquire")[0]["state"] == "needs_review"


def test_funding_query_manifest_and_terminal_empty_page(store, env):
    clock, upstream, build = env
    key = Key("funding_events", interval="event")
    from marketdata.binance import rest_url

    start, end = utc(DAY0), utc(DAY0) + DAY
    events = [
        {
            "symbol": "BTCUSDT",
            "fundingTime": micros(start + i * timedelta(hours=8)) // 1000,
            "fundingRate": "0.0001",
        }
        for i in range(3)
    ]
    upstream.responses[rest_url(key, start, end)[0]] = (200, json_bytes(events), {})
    upstream.responses[rest_url(key, start + timedelta(hours=16, milliseconds=1), end)[0]] = (
        200,
        b"[]",
        {},
    )
    runner = build([key])
    report = runner.run()
    assert report["data_complete"] and len(upstream.calls) == 2
    assert store.read(key, start, end).num_rows == 3
    assert tasks(runner, "acquire")[0]["proof"]["query_ids"]


def test_new_ledger_survives_existing_backup_restore_and_data_still_pending(store, env, tmp_path):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    runner = build([key])
    runner.run()
    archived, restored = tmp_path / "checkpoint", tmp_path / "restored"
    created = backup.create_backup(store, archived, min_free_gib=0)
    assert backup.verify_backup(archived)["backup_id"] == created["backup_id"]
    backup.restore_backup(archived, restored, min_free_gib=0)
    reopened = Store(restored)
    try:
        saved = json.loads(
            reopened.db.execute("SELECT record FROM daily_tasks LIMIT 1").fetchone()[0]
        )
        assert saved["state"] == "waiting_source" and saved["publication_checks"] == 1
        assert reopened.validate()["ok"]
        assert not read_status(restored)["data_complete"]
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "settings",
    [
        {"noon": "25:00"},
        {"evening": "09:00"},
        {"max_tasks": 999},
        {"retry_slots": ("22:00", "20:00")},
    ],
)
def test_invalid_schedule_rejected(settings):
    with pytest.raises(DataError):
        Schedule(**settings)


def test_repository_daily_scope_loads_unchanged():
    keys, policy, schedule = load_config("configs/daily.toml")
    assert len(keys) == 8 and policy.refresh_days == 3 and schedule.evening == "18:00"


def test_actual_sigkill_after_catalog_commit_resumes_without_http(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    code = """
import os, signal, sys, httpx
sys.path[:0] = ["packages/marketdata/src", "tests"]
from test_daily_schedule import Upstream, Clock, DAY0
from marketdata.daily_schedule import Scheduler
from marketdata.domain import Key
from marketdata.operations import SyncPolicy
from marketdata.store import Store
import marketdata.binance, marketdata.pipeline
clock=Clock(); marketdata.binance.now=clock; marketdata.pipeline.now=clock
upstream=Upstream(); key=Key("trade_bars", interval="1h"); upstream.add(key)
client=httpx.Client(transport=httpx.MockTransport(upstream.handle))
def fault(phase, attempt):
    if phase == "after_publish": os.kill(os.getpid(), signal.SIGKILL)
s=Store(sys.argv[1])
Scheduler(s,[key],SyncPolicy(start=DAY0),client=client,clock=clock,sleep=lambda _:None,fault=fault).run()
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(store.root)], capture_output=True
    )
    assert result.returncode == -9, result.stderr.decode()
    runner = build([key])
    assert runner.run()["data_complete"]
    assert not upstream.calls
    assert store.db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 1


def test_existing_complete_data_is_only_refreshed_when_eligible(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)
    runner = build([key])
    runner.run()
    clock.value += DAY - timedelta(hours=1)
    runner.run()
    assert len(upstream.calls) == 2
    # Today's future obligations keep aggregate completion false.
    assert not runner.run()["data_complete"]


def test_content_failure_and_downloaded_short_file_do_not_become_complete(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key, short=True)
    runner = build([key])
    runner.run()
    task = tasks(runner, "acquire")[0]
    assert task["state"] == "validation_retry"
    assert task["proof"] is None
    assert store.read(key, DAY0, "2024-01-15").num_rows == 23


def test_oi_uses_existing_grid_and_raw_quality_semantics(store, env):
    clock, upstream, build = env
    key = Key("open_interest", interval="5m", series="archive")
    upstream.add(key)
    runner = build([key])
    assert runner.run()["data_complete"]
    rows = store.read(key, DAY0, "2024-01-15").to_pylist()
    assert len(rows) == 288 and rows[0]["time_semantics"] == "archive_label_unverified"


def probe_config(tmp_path, **changes):
    values = dict(
        enabled=True,
        start_day=DAY0,
        end_day="2024-01-15",
        opens_kst="14:30",
        closes_kst="21:30",
        interval_seconds=300,
    )
    values.update(changes)
    path = tmp_path / "probe.toml"
    path.write_text(
        "[observation]\n" + "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
    )
    return path


def test_probe_tracks_each_pair_without_body_download_or_collection_credit(store, env, tmp_path):
    from marketdata.daily_probe import probe

    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    runner = build([key])
    cfg = probe_config(tmp_path)
    clock.value = utc("2024-01-15T05:30Z")
    first = probe(runner, cfg)
    assert first["requests"] == 2 and first["available_urls"] == 0
    assert probe(runner, cfg)["requests"] == 0
    clock.value += timedelta(minutes=5)
    url = archive_url(key, utc(DAY0), False)
    upstream.responses[url] = (200, b"body must not be imported", {"Age": "42"})
    assert probe(runner, cfg)["available_urls"] == 1
    clock.value += timedelta(minutes=5)
    upstream.responses[url + ".CHECKSUM"] = (200, b"not a checksum", {})
    report = probe(runner, cfg)
    assert report["available_urls"] == 2 and report["state"] == "finished"
    assert not report["data_completion_credit"]
    records = runner.ledger.rows("daily_probe_targets")
    assert all(r["last_404"] and r["first_200"] for r in records)
    assert all(e["method"] == "HEAD" for r in records for e in r["observations"])
    assert store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM active").fetchone()[0] == 0


def test_probe_errors_never_count_as_404_and_window_expires(store, env, tmp_path):
    from marketdata.daily_probe import probe

    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    runner = build([key])
    cfg = probe_config(tmp_path)
    clock.value = utc("2024-01-15T05:30Z")
    url = archive_url(key, utc(DAY0), False)
    upstream.responses[url] = httpx.ReadTimeout("timeout")
    upstream.responses[url + ".CHECKSUM"] = (503, b"", {})
    probe(runner, cfg)
    assert all(r["last_404"] is None for r in runner.ledger.rows("daily_probe_targets"))
    clock.value = utc("2024-01-15T13:00Z")
    assert probe(runner, cfg)["state"] == "finished"
    assert all(r["right_censored"] for r in runner.ledger.rows("daily_probe_targets"))


@pytest.mark.parametrize("changes", [{"end_day": "2024-01-23"}, {"interval_seconds": 1}])
def test_probe_scope_is_bounded(store, env, tmp_path, changes):
    from marketdata.daily_probe import probe

    _, _, build = env
    with pytest.raises(DataError):
        probe(build([Key("trade_bars")]), probe_config(tmp_path, **changes))


def test_probe_disabled_means_no_network(store, env, tmp_path):
    from marketdata.daily_probe import probe

    _, upstream, build = env
    assert (
        probe(build([Key("trade_bars")]), probe_config(tmp_path, enabled=False))["state"]
        == "disabled"
    )
    assert not upstream.calls


def test_complete_flag_without_catalog_proof_is_rejected(store, env):
    _, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)
    runner = build([key])
    assert runner.run()["data_complete"]
    with store.writer(), store.db:
        store.db.execute("DELETE FROM queries")
    report = build([key]).run()
    assert not report["data_complete"] and report["review_required"]
    assert all(t["state"] == "needs_review" for t in tasks(runner))
    assert len(upstream.calls) == 2


def test_recovery_rechecks_contents_instead_of_trusting_saved_intent(store, env):
    _, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)

    def fault(phase, attempt):
        if phase == "after_publish":
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        build([key], fault=fault).run()
    path = store.db.execute("SELECT path FROM revisions").fetchone()[0]
    (store.root / path).write_bytes(b"damaged")
    with pytest.raises(DataError, match="Canonical hash mismatch"):
        build([key]).run()
    assert len(upstream.calls) == 2


def test_new_refresh_cycle_cannot_reset_incomplete_retry_budget(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    old_url = upstream.add(key)
    runner = build([key])
    runner.run()
    clock.value += DAY
    upstream.add(key, "2024-01-15")
    upstream.responses[old_url] = httpx.ConnectError("offline")
    runner.run()
    original = next(
        t for t in tasks(runner, "refresh") if t["day"] == DAY0 and t["state"] == "retry_wait"
    )
    # Cross another calendar day: a new refresh must not reset the old task's budget.
    clock.value += DAY
    upstream.add(key, "2024-01-16")
    runner.run()
    clock.value += timedelta(minutes=5)
    runner.run()
    failed = next(t for t in tasks(runner) if t["id"] == original["id"])
    assert failed["state"] == "needs_review" and failed["transient_failures"] == 3
    calls = upstream.calls.count(old_url)
    assert calls == 13  # initial successful fetch + 3 x 4 bounded requests
    runner.run()
    assert upstream.calls.count(old_url) == calls


def test_refresh_does_not_jump_over_existing_future_retry_time(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    url = upstream.add(key)
    runner = build([key])
    runner.run()
    clock.value += DAY
    upstream.add(key, "2024-01-15")
    upstream.responses[url] = (404, b"not yet", {})
    runner.run()
    pending = next(t for t in tasks(runner, "refresh") if t["state"] == "waiting_source")
    pending["next_at"] = EVENING + 2 * DAY + timedelta(hours=2)
    runner.ledger.save("daily_tasks", pending)
    count = upstream.calls.count(url)
    clock.value += DAY
    upstream.add(key, "2024-01-16")
    runner.run()
    assert upstream.calls.count(url) == count
    clock.value += timedelta(hours=2)
    upstream.add(key)
    runner.run()
    assert upstream.calls.count(url) == count + 1
    assert all(t["state"] == "complete" for t in tasks(runner) if t["day"] == DAY0)


def test_http_weight_window_is_shared_across_dataset_attempts(store, env):
    _, upstream, build = env
    keys = [Key("trade_bars", interval="1h"), Key("mark_price_bars", interval="1h")]
    for key in keys:
        upstream.add(key)
    runner = build(keys)
    runner.run()
    assert len(runner.request_windows["data.binance.vision"]) == 4


def test_probe_requires_midnight_boundaries(store, env, tmp_path):
    from marketdata.daily_probe import probe

    _, _, build = env
    cfg = probe_config(tmp_path, start_day="2024-01-14T00:01Z", end_day="2024-01-15T00:01Z")
    with pytest.raises(DataError):
        probe(build([Key("trade_bars")]), cfg)


def test_config_change_never_silently_executes_removed_pending_target(store, env):
    clock, upstream, build = env
    old, keep = Key("trade_bars", interval="1h"), Key("mark_price_bars", interval="1h")
    build([old, keep]).run()
    clock.value += timedelta(hours=2)
    upstream.add(old)
    upstream.add(keep)
    runner = build([keep])
    runner.run()
    removed = next(t for t in tasks(runner, "acquire") if t["key"] == old.asdict())
    assert removed["state"] == "needs_review"
    assert removed["reason"] == "pending_target_removed_from_configuration"
    assert upstream.calls.count(archive_url(old, utc(DAY0), False)) == 1


def test_populated_scheduler_store_survives_backup_restore_without_refetch(store, env, tmp_path):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)
    runner = build([key])
    assert runner.run()["data_complete"]
    archived, restored = tmp_path / "checkpoint-filled", tmp_path / "restored-filled"
    backup.create_backup(store, archived, min_free_gib=0)
    backup.verify_backup(archived)
    backup.restore_backup(archived, restored, min_free_gib=0)
    reopened = Store(restored)
    try:

        def reject(request):
            pytest.fail("A verified restored task must not refetch the same cycle")

        with httpx.Client(transport=httpx.MockTransport(reject)) as client:
            replay = Scheduler(reopened, [key], runner.policy, client=client, clock=clock)
            assert replay.run()["data_complete"]
        assert reopened.validate()["ok"]
    finally:
        reopened.close()


def test_failed_state_transaction_cannot_drop_retry_count_on_recovery(store, env):
    _, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.responses[archive_url(key, utc(DAY0), False)] = httpx.ConnectError("offline")
    runner = build([key])
    with runner.ledger.exclusive(), store.db:
        store.db.execute("""
            CREATE TRIGGER interrupt_failure BEFORE UPDATE ON daily_attempts
            WHEN json_extract(NEW.record, '$.state')='retry_wait'
            BEGIN SELECT RAISE(ABORT, 'injected state commit failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError):
        runner.run()
    with store.db:
        store.db.execute("DROP TRIGGER interrupt_failure")
    task = tasks(runner, "acquire")[0]
    assert task["state"] == "running" and task["transient_failures"] == 0
    runner.run()
    task = tasks(runner, "acquire")[0]
    assert task["state"] == "retry_wait" and task["transient_failures"] == 1
    assert len(upstream.calls) == 4


def test_old_collector_can_fill_pending_day_without_deleting_new_ledger(store, env):
    from marketdata.binance import Fetcher
    from marketdata.pipeline import Collector

    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    runner = build([key], refresh=0)
    runner.run()
    previous = tasks(runner, "acquire")[0]
    upstream.add(key)
    with httpx.Client(transport=httpx.MockTransport(upstream.handle)) as client:
        old = Collector(store, Fetcher(client=client, sleep=lambda _: None), cooperative=True)
        old.archive(key, utc(DAY0), utc(DAY0) + DAY, monthly=False)
    count = len(upstream.calls)
    assert runner.run()["data_complete"]
    assert len(upstream.calls) == count
    recovered = tasks(runner, "acquire")[0]
    assert recovered["id"] == previous["id"] and recovered["attempts"] == previous["attempts"]


def test_legacy_status_follows_new_tasks_and_noop_keeps_previous_run_time(store, env):
    from marketdata.operations import status

    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    runner = build([key])
    runner.run()
    previous = dict(store.db.execute("SELECT * FROM sync_runs").fetchone())
    assert (
        status(store, [key], runner.policy, current=clock())["datasets"][0]["last_run"]["state"]
        == "partial"
    )
    upstream.add(key)
    clock.value += timedelta(hours=2)
    runner.run()
    observed = status(store, [key], runner.policy, current=clock())
    assert observed["healthy"] and observed["datasets"][0]["last_run"]["state"] == "complete"
    count = store.db.execute("SELECT count(*) FROM sync_runs").fetchone()[0]
    clock.value += timedelta(minutes=5)
    assert runner.run()["run_outcome"] == "no_due_tasks"
    assert store.db.execute("SELECT count(*) FROM sync_runs").fetchone()[0] == count
    assert (
        dict(store.db.execute("SELECT * FROM sync_runs WHERE id=?", (previous["id"],)).fetchone())
        == previous
    )
