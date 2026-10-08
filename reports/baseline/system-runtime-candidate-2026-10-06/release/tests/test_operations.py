from datetime import timedelta
from types import SimpleNamespace

import pytest
from marketdata.binance import SourceError
from marketdata.cli import main
from marketdata.domain import DAY, DataError, Key, json_bytes, micros, utc
from marketdata.normalize import normalize_rest
from marketdata.operations import (
    SyncPolicy,
    inventory,
    plan_tasks,
    register_target,
    scope_start,
    status,
    update,
    window_end,
)

CURRENT = utc("2024-01-15T12:00")


def publish_range(store, key, start, end, *, omit=(), observed=CURRENT):
    start, end = utc(start), utc(end)
    raw = []
    if key.dataset == "instrument_metadata":
        raw = {"symbols": [{"symbol": "BTCUSDT"}]}
    else:
        cursor, index = start, 0
        step = key.step or 8 * 3600
        while cursor < end:
            if index not in omit:
                stamp = micros(cursor) // 1000
                if key.dataset == "open_interest":
                    raw.append(
                        {
                            "symbol": "BTCUSDT",
                            "timestamp": stamp,
                            "sumOpenInterest": "10",
                            "sumOpenInterestValue": "1000",
                        }
                    )
                elif key.dataset == "funding_events":
                    raw.append({"symbol": "BTCUSDT", "fundingTime": stamp, "fundingRate": "0.0001"})
                else:
                    raw.append(
                        [
                            stamp,
                            "100",
                            "105",
                            "95",
                            "101",
                            "2",
                            stamp + step * 1000 - 1,
                            "200",
                            3,
                            "1",
                            "100",
                            "0",
                        ]
                    )
            cursor += timedelta(seconds=step)
            index += 1
    blob = json_bytes(raw)
    identity = store.put_raw(blob, "test:source", "rest", observed)
    batch = normalize_rest(blob, key, identity, observed)
    if key.dataset == "instrument_metadata":
        start = observed.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + DAY
    return store.publish(key, batch, start, end, identity)


class Source:
    def __init__(self, store, *, fail_keys=(), interrupt_day=None):
        self.store, self.calls = store, []
        self.fail_keys, self.interrupt_day = set(fail_keys), interrupt_day
        self.fetcher = SimpleNamespace(close=lambda: None)

    def archive(self, key, start, end, **kwargs):
        return self.rest(key, start, end)

    def rest(self, key, start, end, **kwargs):
        self.calls.append((key.id, utc(start), utc(end)))
        if key.id in self.fail_keys:
            raise SourceError("upstream unavailable", status=503)
        if self.interrupt_day == utc(start).date().isoformat():
            raise KeyboardInterrupt()
        return publish_range(self.store, key, start, end)


def run(store, source, keys, policy, current=CURRENT):
    with store.writer():
        return update(source, keys, policy, current=current)


def test_long_outage_and_older_internal_hole_both_recovered(store):
    key = Key("trade_bars", interval="1h")
    publish_range(store, key, "2024-01-01", "2024-01-02")
    publish_range(store, key, "2024-01-05", "2024-01-06", omit=(7,))
    publish_range(store, key, "2024-01-12", "2024-01-13")
    source = Source(store)
    policy = SyncPolicy(start="2024-01-01")
    result = run(store, source, [key], policy)[0]
    assert result["complete"]
    assert result["coverage"]["missing_rows"] == 0
    called_days = {a.date().isoformat() for _, a, _ in source.calls}
    assert "2024-01-02" in called_days and "2024-01-05" in called_days
    assert "2024-01-01" not in called_days  # Complete old day is not re-downloaded.
    assert result["coverage_before"]["missing_rows"] == 12 * 24 - 23
    report = status(store, [key], policy, current=CURRENT)
    assert report["healthy"]
    assert report["datasets"][0]["last_run"]["state"] == "complete"


def test_interrupted_run_resumes_from_committed_days(store):
    key = Key("trade_bars", interval="1h")
    policy = SyncPolicy(start="2024-01-01", refresh_days=0)
    source = Source(store, interrupt_day="2024-01-04")
    with pytest.raises(KeyboardInterrupt):
        run(store, source, [key], policy)
    assert store.db.execute("SELECT state FROM sync_runs").fetchone()[0] == "interrupted"
    resumed = Source(store)
    result = run(store, resumed, [key], policy)[0]
    assert result["complete"]
    assert min(a for _, a, _ in resumed.calls) == utc("2024-01-04")
    assert store.read(key, "2024-01-01", "2024-01-15").num_rows == 14 * 24


def test_unclean_exit_is_marked_interrupted_under_next_writer_lock(store):
    key = Key("trade_bars", interval="1h")
    stamp = utc("2024-01-01").isoformat()
    with store.db:
        store.db.execute(
            "INSERT INTO sync_runs VALUES(?,?,?,?,?,?,?,?,?)",
            ("orphan", key.id, stamp, stamp, stamp, stamp, None, "running", "{}"),
        )
    run(store, Source(store), [key], SyncPolicy(start="2024-01-14", refresh_days=0))
    assert (
        store.db.execute("SELECT state FROM sync_runs WHERE id='orphan'").fetchone()[0]
        == "interrupted"
    )


def test_failure_isolated_per_dataset_and_is_not_reported_complete(store):
    bad, good = Key("trade_bars", interval="1h"), Key("mark_price_bars", interval="1h")
    results = run(
        store,
        Source(store, fail_keys=[bad.id]),
        [bad, good],
        SyncPolicy(start="2024-01-14", refresh_days=0),
    )
    assert results[0]["state"] == "failed" and not results[0]["complete"]
    assert results[1]["complete"]
    report = status(store, [bad, good], current=CURRENT)
    assert not report["healthy"]
    assert report["datasets"][0]["last_run"]["errors"][0]["type"] == "SourceError"


def test_batch_limit_keeps_recent_data_and_drains_old_backlog(store):
    key = Key("trade_bars", interval="1h")
    policy = SyncPolicy(start="2024-01-01", refresh_days=1, max_days_per_run=3)
    source = Source(store)
    first = run(store, source, [key], policy)[0]
    assert [a for _, a, _ in source.calls] == [
        utc("2024-01-14"),
        utc("2024-01-01"),
        utc("2024-01-02"),
    ]
    assert first["plan"]["deferred_days"] == 11 and not first["complete"]
    source.calls.clear()
    second = run(store, source, [key], policy)[0]
    assert utc("2024-01-03") in [a for _, a, _ in source.calls]
    assert second["coverage"]["missing_rows"] < first["coverage"]["missing_rows"]


def test_historical_import_does_not_expand_explicit_operational_scope(store):
    key = Key("trade_bars", interval="1h")
    publish_range(store, key, "2023-01-01", "2023-01-02")
    store.issue(key, "2023-01-01", "old", "quarantined_zero_oi", {})
    store.issue(key, "2023-01-01", "other", "conflict", {})
    policy = SyncPolicy(start="2024-01-14", refresh_days=0)
    source = Source(store)
    assert run(store, source, [key], policy)[0]["complete"]
    assert all(a >= utc("2024-01-14") for _, a, _ in source.calls)
    report = status(store, [key], current=CURRENT)
    assert report["healthy"]
    assert report["datasets"][0]["historical_blocking_issues"] == [
        {"kind": "conflict", "days": 1},
        {"kind": "quarantined_zero_oi", "days": 1},
    ]
    assert report["datasets"][0]["historical_blocked_days"] == 1


def test_scoped_blocking_issue_is_visible_and_never_auto_accepted(store):
    key = Key("trade_bars", interval="1h")
    store.issue(key, "2024-01-13", "raw", "conflict", {})
    source = Source(store)
    result = run(store, source, [key], SyncPolicy(start="2024-01-13", refresh_days=0))[0]
    assert result["state"] == "partial"
    assert result["coverage"]["blocked_days"] == ["2024-01-13"]
    assert [a for _, a, _ in source.calls] == [utc("2024-01-14")]
    assert store.db.execute("SELECT resolved FROM issues").fetchone()[0] == 0


def test_coverage_cache_invalidated_by_new_active_revision(store):
    key = Key("trade_bars", interval="1h")
    publish_range(store, key, "2024-01-14", "2024-01-15", omit=(3,))
    assert inventory(store, key, "2024-01-14", "2024-01-15", cache=True)["missing_rows"] == 1
    publish_range(store, key, "2024-01-14", "2024-01-15")
    assert inventory(store, key, "2024-01-14", "2024-01-15")["complete"]
    store.issue(key, "2024-01-14", "raw", "conflict", {})
    assert not inventory(store, key, "2024-01-14", "2024-01-15")["complete"]


def test_oi_backfill_respects_retention_and_reports_expired_holes(store):
    key = Key("open_interest", interval="5m", series="rest")
    policy = SyncPolicy(start="2023-12-01")
    coverage = inventory(store, key, utc(policy.start), window_end(key, CURRENT))
    plan = plan_tasks(key, policy, coverage, CURRENT)
    assert plan["expired_missing_ranges"]
    assert all(a > CURRENT - timedelta(days=29) for a, _ in plan["ranges"])
    assert all(b <= window_end(key, CURRENT) for _, b in plan["ranges"])


def test_oi_internal_hole_is_recovered_after_latest_timestamp(store):
    key = Key("open_interest", interval="5m", series="rest")
    publish_range(store, key, "2024-01-15", CURRENT, omit=(3,))
    source = Source(store)
    result = run(store, source, [key], SyncPolicy(start="2024-01-15"))[0]
    assert result["complete"]
    assert any(a <= utc("2024-01-15T00:15") < b for _, a, b in source.calls)
    assert result["coverage"]["missing_rows"] == 0


def test_oi_bootstrap_scope_is_persisted_instead_of_sliding_forward(store):
    key = Key("open_interest", interval="5m", series="rest")
    policy = SyncPolicy(bootstrap_days=28)
    end = window_end(key, CURRENT)
    start = scope_start(store, key, policy, end)
    assert start == end - timedelta(days=28)
    register_target(store, key, policy, start, CURRENT)
    assert scope_start(store, key, policy, end + DAY) == start


def test_oi_closed_period_included_after_grace_without_querying_future(store):
    key = Key("open_interest", interval="5m", series="rest")
    assert window_end(key, CURRENT + timedelta(seconds=44)) == CURRENT
    current = CURRENT + timedelta(seconds=45)
    assert window_end(key, current) == CURRENT + timedelta(minutes=5)
    source = Source(store)
    report = run(store, source, [key], SyncPolicy(start="2024-01-15"), current=current)[0]
    assert report["complete"]
    assert all(end <= current for _, _, end in source.calls)
    assert store.read(key, "2024-01-15", "2024-01-15T12:05")["timestamp"][-1].as_py() == CURRENT


def test_funding_coverage_recovers_unqueried_days_without_inventing_schedule(store):
    key = Key("funding_events", interval="event")
    publish_range(store, key, "2024-01-01", "2024-01-02")
    publish_range(store, key, "2024-01-14", "2024-01-15")
    result = run(store, Source(store), [key], SyncPolicy(start="2024-01-01", refresh_days=0))[0]
    assert result["complete"]
    assert result["coverage"]["missing_rows"] is None
    assert (
        result["coverage"]["semantics"] == "source_query_coverage_not_proof_of_event_completeness"
    )


def test_status_reports_staleness_separately_from_covered_history(store):
    key = Key("trade_bars", interval="1h")
    policy = SyncPolicy(start="2024-01-14", refresh_days=0, stale_after_seconds=60)
    run(store, Source(store), [key], policy)
    report = status(store, [key], current=CURRENT)
    assert report["datasets"][0]["coverage"]["complete"]
    assert report["datasets"][0]["state"] == "stale"
    assert not report["healthy"]


def test_failed_refresh_cannot_look_healthy_because_old_data_is_complete(store):
    key = Key("trade_bars", interval="1h")
    publish_range(store, key, "2024-01-14", "2024-01-15")
    source = Source(store)
    source.archive = lambda *args, **kwargs: [{"state": "waiting_source"}]
    result = run(store, source, [key], SyncPolicy(start="2024-01-14"))[0]
    assert result["coverage"]["complete"] and result["state"] == "partial"
    report = status(store, [key], current=CURRENT)
    assert not report["healthy"] and report["datasets"][0]["state"] == "partial"


def test_metadata_observation_is_not_a_claim_of_historical_coverage(store):
    key = Key("instrument_metadata", interval="event")
    assert run(store, Source(store), [key], SyncPolicy(start="2023-01-01"))[0]["complete"]
    report = status(store, [key], current=CURRENT)
    assert report["healthy"]
    assert (
        report["datasets"][0]["coverage"]["semantics"] == "observation_only_no_historical_inference"
    )


def test_status_cli_table_and_exit_code(store, tmp_path, monkeypatch, capsys):
    import marketdata.operations as operations

    monkeypatch.setattr(operations, "now", lambda: CURRENT)
    config = tmp_path / "daily.toml"
    config.write_text(
        '[operations]\nstart="2024-01-14"\nrefresh_days=0\n'
        '[[datasets]]\ndataset="trade_bars"\ninterval="1h"\n'
    )
    argv = ["--root", str(store.root), "status", "--config", str(config), "--table"]
    assert main(argv) == 2
    assert "empty" in capsys.readouterr().out
    run(
        store,
        Source(store),
        [Key("trade_bars", interval="1h")],
        SyncPolicy(start="2024-01-14", refresh_days=0),
    )
    assert main(argv) == 0
    assert "healthy" in capsys.readouterr().out


@pytest.mark.parametrize(
    "settings",
    [
        {"start": "2024-01-01T12:00"},
        {"bootstrap_days": 0},
        {"refresh_days": 32},
        {"overlap_minutes": -1},
        {"stale_after_seconds": 0},
    ],
)
def test_invalid_operational_policy_rejected(settings):
    with pytest.raises(DataError):
        SyncPolicy(**settings)
