from datetime import timedelta

import httpx
import pytest
from conftest import kline
from marketdata.binance import Fetcher, SourceError, archive_units
from marketdata.domain import DataError, Key, micros, now, utc
from marketdata.pipeline import Collector


def test_retry_after_and_error_policy():
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "2"})
            if len(calls) == 1
            else httpx.Response(200, content=b"ok")
        )

    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)
    assert fetcher.fetch("https://example.com/test").body == b"ok"
    assert len(calls) == 2 and 2 in sleeps


def test_no_retry_for_access_restriction():
    fetcher = Fetcher(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(451))),
        sleep=lambda _: None,
    )
    with pytest.raises(SourceError) as exc:
        fetcher.fetch("https://example.com/test")
    assert exc.value.status == 451 and fetcher.requests == 1


def test_rest_pagination_and_empty_gap_not_successful_coverage(store):
    rows = [kline(utc("2024-01-01") + timedelta(minutes=i)) for i in range(4)]
    calls = []

    def handler(request):
        cursor = int(request.url.params["startTime"])
        calls.append(cursor)
        page = [row for row in rows if int(row[0]) >= cursor][:2]
        return httpx.Response(200, json=page)

    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)
    collector = Collector(store, fetcher)
    collector.rest(Key("trade_bars"), "2024-01-01", "2024-01-01T00:05")
    assert len(calls) == 3
    assert store.coverage(Key("trade_bars"), "2024-01-01", "2024-01-01T00:05")["missing"] == 1
    assert store.db.execute("SELECT count(*) FROM artifacts WHERE kind='rest'").fetchone()[0] == 3


def test_nonadvancing_rest_page_fails(store):
    fetcher = Fetcher(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[kline()]))),
        sleep=lambda _: None,
    )
    with pytest.raises(DataError, match="outside requested"):
        Collector(store, fetcher).rest(Key("trade_bars"), "2024-01-01", "2024-01-01T00:05")
    assert store.db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 0


def test_monthly_planner_has_no_overlap():
    units = list(archive_units(Key("trade_bars"), "2024-01-01", "2024-03-03"))
    assert len(units) == 4
    assert [u[2] for u in units] == [True, True, False, False]
    assert all(a[1] == b[0] for a, b in zip(units, units[1:]))


def test_writer_lock_and_funding_empty_query_semantics(store):
    from marketdata.store import Store

    other = Store(store.root)
    with store.writer():
        with pytest.raises(DataError, match="Another writer"):
            with other.writer():
                pass
    other.close()
    fetcher = Fetcher(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))),
        sleep=lambda _: None,
    )
    key = Key("funding_events", interval="event")
    Collector(store, fetcher).rest(key, "2024-01-01", "2024-01-02")
    coverage = store.coverage(key, "2024-01-01", "2024-01-02")
    assert coverage["rows"] == 0 and coverage["query_complete"]
    assert coverage["semantics"] == "source_query_coverage_not_proof_of_event_completeness"


def test_oi_latest_500_endpoint_cannot_skip_earlier_history(store):
    start = now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=10)
    rows = [
        {
            "symbol": "BTCUSDT",
            "timestamp": micros(start + timedelta(minutes=i * 5)) // 1000,
            "sumOpenInterest": "10",
            "sumOpenInterestValue": "1000",
        }
        for i in range(1100)
    ]

    def handler(request):
        lo, hi = int(request.url.params["startTime"]), int(request.url.params["endTime"])
        page = [row for row in rows if lo <= row["timestamp"] <= hi][-500:]
        return httpx.Response(200, json=page)

    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)
    key = Key("open_interest", interval="5m", series="rest")
    end = start + timedelta(minutes=1100 * 5)
    Collector(store, fetcher).rest(key, start, end)
    assert store.coverage(key, start, end)["complete"]
    assert fetcher.requests == 3


def test_empty_daily_revisions_have_distinct_identities(store):
    from marketdata.normalize import Batch

    key = Key("trade_bars")
    store.publish(key, Batch([]), "2024-01-01", "2024-01-03", "empty")
    records = store.revision_list(key, "2024-01-01", "2024-01-03")
    assert len(records) == 2 and len({r["id"] for r in records}) == 2
    assert [r["day"] for r in records] == ["2024-01-01", "2024-01-02"]


def test_unchanged_metadata_still_records_each_observation(store, monkeypatch):
    import marketdata.binance as binance

    receipts = iter([utc("2025-01-01T00:00"), utc("2025-01-01T00:01")])
    monkeypatch.setattr(binance, "now", lambda: next(receipts))
    fetcher = Fetcher(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"symbols": [{"symbol": "BTCUSDT"}]})
            )
        ),
        sleep=lambda _: None,
    )
    key = Key("instrument_metadata", interval="event")
    collector = Collector(store, fetcher)
    for _ in range(2):
        collector.rest(key, "2025-01-01", "2025-01-02")
    rows = store.read(key, "2025-01-01", "2025-01-02").to_pylist()
    assert [r["timestamp"] for r in rows] == [utc("2025-01-01T00:00"), utc("2025-01-01T00:01")]
    assert rows[0]["source_id"] == rows[1]["source_id"]
    assert rows[0]["first_observed_at"] < rows[1]["first_observed_at"]
