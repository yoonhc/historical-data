from datetime import timedelta
from threading import Event, Thread

import httpx
import pytest
from conftest import kline, zip_csv
from marketdata.backfill import backfill_set
from marketdata.binance import Fetcher, Response, SourceError
from marketdata.domain import DataError, Key, WriterBusy, micros, utc
from marketdata.pipeline import Collector
from test_operations import Source, publish_range


def test_monthly_fallback_releases_writer_and_resumes_without_duplicates(store):
    key = Key("trade_bars", interval="1h")
    source = Source(store, interrupt_day="2024-01-04")
    calls = []

    def archive(key, start, end, **options):
        calls.append((start, end, options))
        if options["monthly"]:
            return [{"state": "waiting_source"}]
        return source.rest(key, start, end)

    source.archive = archive
    released = []

    def checkpoint(report):
        with store.writer():
            released.append(report[-1]["batches"][-1]["state"])

    with pytest.raises(KeyboardInterrupt):
        backfill_set(
            source, [key], "2024-01-01", "2024-02-01", checkpoint=checkpoint, sleep=lambda _: None
        )
    assert released[0] == "fallback_daily"
    assert store.coverage(key, "2024-01-01", "2024-01-04")["complete"]
    source.interrupt_day = None
    source.calls.clear()
    result = backfill_set(source, [key], "2024-01-01", "2024-02-01", sleep=lambda _: None)
    assert result[0]["complete"]
    assert min(a for _, a, _ in source.calls) == utc("2024-01-04")
    assert result[0]["coverage"]["rows"] == 31 * 24
    assert all(options["fallback_daily"] is False for _, _, options in calls)


def test_waiting_scheduled_writer_gets_lock_between_batches(store):
    key = Key("trade_bars", interval="1h")
    source = Source(store)
    acquired, started = Event(), Event()

    def scheduled():
        started.set()
        with store.writer(timeout=2):
            acquired.set()

    thread = Thread(target=scheduled)
    original = source.archive

    def archive(*args, **kwargs):
        if not started.is_set():
            thread.start()
            assert started.wait(1)
            assert not acquired.is_set()
        return original(*args, **kwargs)

    source.archive = archive

    def pause(_):
        assert acquired.wait(2)

    result = backfill_set(source, [key], "2024-01-01", "2024-01-03", sleep=pause)
    thread.join(timeout=2)
    assert acquired.is_set() and result[0]["complete"]
    with store.writer():
        with pytest.raises(WriterBusy):
            with store.writer(timeout=0.01):
                pytest.fail("Concurrent writer acquired the lock")


def test_blocked_month_is_split_and_bad_day_never_auto_accepted(store):
    key = Key("trade_bars", interval="1h")
    store.issue(key, "2024-01-13", "source", "conflict", {})
    source = Source(store)
    report = backfill_set(source, [key], "2024-01-01", "2024-02-01", sleep=lambda _: None)[0]
    assert not report["complete"]
    assert report["coverage"]["missing"] == 24
    assert report["batches"][0]["state"] == "split_blocked_month"
    assert all(a.date().isoformat() != "2024-01-13" for _, a, _ in source.calls)
    assert store.db.execute("SELECT resolved FROM issues").fetchone()[0] == 0


def test_failed_dataset_does_not_prevent_other_historical_sources(store):
    bad, good = Key("trade_bars", interval="1h"), Key("mark_price_bars", interval="1h")
    source = Source(store, fail_keys=[bad.id])
    results = backfill_set(source, [bad, good], "2024-01-01", "2024-01-03", sleep=lambda _: None)
    assert not results[0]["complete"] and results[1]["complete"]
    assert results[0]["batches"][0]["error_type"] == SourceError.__name__


def test_coverage_streams_days_and_merges_gaps_across_midnight(store, monkeypatch):
    key = Key("trade_bars", interval="1h")
    start, end = utc("2024-01-01T12:00"), utc("2024-01-03T12:00")
    publish_range(store, key, start, end, omit=(10, 11, 12, 13, 47))
    # Full-range materialization is forbidden, including during snapshot creation.
    monkeypatch.setattr(store, "read", lambda *a, **k: pytest.fail("Unbounded read"))
    coverage = store.coverage(key, start, end)
    assert coverage["rows"] == 43 and coverage["expected"] == 48 and coverage["missing"] == 5
    assert coverage["gap_ranges_us"] == [
        [micros(start + timedelta(hours=10)), micros(start + timedelta(hours=14))],
        [micros(end - timedelta(hours=1)), micros(end)],
    ]
    with pytest.raises(DataError, match="gaps"):
        store.create_snapshot([key], start, end)
    snapshot = store.create_snapshot([key], start, end, allow_gaps=True)
    assert store.snapshot_manifest(snapshot)["coverage"][0] == {**coverage, "key": key.asdict()}


def test_funding_query_coverage_and_empty_days_remain_event_semantics(store, monkeypatch):
    key = Key("funding_events", interval="event")
    publish_range(store, key, "2024-01-01", "2024-01-03")
    monkeypatch.setattr(store, "read", lambda *a, **k: pytest.fail("Unbounded read"))
    report = store.coverage(key, "2024-01-01", "2024-01-03")
    assert report["rows"] == 6 and report["query_complete"] and report["complete"]
    assert "not_proof_of_event_completeness" in report["semantics"]


def test_historical_rest_oi_is_rejected_before_any_collection(store):
    source = Source(store)
    with pytest.raises(DataError, match="retention"):
        backfill_set(
            source, [Key("open_interest", interval="5m", series="rest")], "2023-01-01", "2024-01-01"
        )
    assert not source.calls


def test_explicit_rest_backfill_before_archive_availability_uses_bounded_days(store):
    source = Source(store)
    key = Key("trade_bars", interval="1h")

    def archive(*args, **kwargs):
        pytest.fail("Pre-archive REST backfill must not request archive files")

    source.archive = archive
    result = backfill_set(
        source, [key], "2019-09-09", "2019-09-12", source="rest", sleep=lambda _: None
    )[0]
    assert result["complete"] and result["coverage"]["rows"] == 72
    assert len(source.calls) == 3
    assert all(stop - begin == timedelta(days=1) for _, begin, stop in source.calls)


@pytest.mark.parametrize("fallback, requests", [(False, 1), (True, 32)])
def test_archive_transport_can_defer_daily_fallback_to_batch_runner(store, fallback, requests):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as client:
        fetcher = Fetcher(client, sleep=lambda _: None)
        result = Collector(store, fetcher).archive(
            Key("trade_bars"), "2024-01-01", "2024-02-01", fallback_daily=fallback
        )
        assert fetcher.requests == requests
        assert all(r["state"] == "waiting_source" for r in result)
        assert len(result) == (31 if fallback else 1)


def test_timestamp_only_coverage_still_detects_canonical_file_corruption(store):
    key = Key("trade_bars", interval="1h")
    publish_range(store, key, "2024-01-01", "2024-01-02")
    record = store.revision_list(key, "2024-01-01", "2024-01-02")[0]
    (store.root / record["path"]).write_bytes(b"corrupted")
    with pytest.raises(DataError, match="hash mismatch"):
        store.coverage(key, "2024-01-01", "2024-01-02")
    report = store.validate()
    assert not report["ok"] and len(report["failures"]) == 1


def test_cooperative_archive_releases_writer_during_network_parse_and_between_days(
    store, monkeypatch
):
    import marketdata.pipeline as pipeline
    from marketdata.domain import sha256

    key = Key("trade_bars", market="spot")
    start, end = utc("2024-01-01"), utc("2024-01-04")
    rows = [kline(start + timedelta(minutes=i)) for i in range(3 * 1440)]
    blob = zip_csv(rows, "BTCUSDT-1m-2024-01-01.csv")
    # Exercise a single source containing several days via the normal ingest method.
    parsed, released, published = [], [], []
    original_normalize, original_publish = pipeline.normalize_archive, store.publish

    def normalize(*args, **kwargs):
        with store.writer():
            parsed.append(True)
        return original_normalize(*args, **kwargs)

    def publish(key, batch, begin, stop, *args, **kwargs):
        assert stop - begin == timedelta(days=1) and len(batch.rows) == 1440
        with pytest.raises(WriterBusy):
            with store.writer():
                pass
        published.append(begin)
        return original_publish(key, batch, begin, stop, *args, **kwargs)

    def sleep(_):
        with store.writer():
            released.append(True)

    monkeypatch.setattr(pipeline, "normalize_archive", normalize)
    monkeypatch.setattr(store, "publish", publish)
    monkeypatch.setattr(pipeline.time, "sleep", sleep)
    collector = Collector(store, cooperative=True)
    with store.writer():
        identity = store.put_raw(blob, "test:archive", "archive", start)
    collector._ingest_archive(
        blob, key, start, end, identity, start, "BTCUSDT-1m-2024-01-01.csv", False
    )
    assert parsed and len(published) == len(released) == 3
    assert store.coverage(key, start, end)["complete"]
    assert collector.writer_max_seconds > 0
    assert store.raw(identity)[0] == blob and sha256(blob) == identity


def test_cooperative_backfill_fetches_without_lock_and_resumes_after_partial_publication(
    store, monkeypatch
):
    from marketdata.domain import sha256

    start, end = utc("2024-01-01"), utc("2024-02-01")
    key = Key("trade_bars", market="spot", interval="1h")
    # Kline source must use the requested interval's close timestamp.
    rows = []
    for i in range(31 * 24):
        stamp = start + timedelta(hours=i)
        item = kline(stamp)
        item[6] = str(micros(stamp + timedelta(hours=1)) // 1000 - 1)
        rows.append(item)
    blob = zip_csv(rows, "BTCUSDT-1h-2024-01.csv")
    calls = []

    class Transport:
        def fetch(self, url, **kwargs):
            with store.writer():
                calls.append(url)
            body = (
                f"{sha256(blob)}  BTCUSDT-1h-2024-01.zip\n".encode()
                if url.endswith("CHECKSUM")
                else blob
            )
            return Response(body, url, end, {})

    collector = Collector(store, Transport(), cooperative=True)
    publish = store.publish

    def interrupt(key, batch, begin, stop, *args, **kwargs):
        if begin == start + timedelta(days=2):
            raise KeyboardInterrupt()
        return publish(key, batch, begin, stop, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "publish", interrupt)
        with pytest.raises(KeyboardInterrupt):
            backfill_set(collector, [key], start, end, sleep=lambda _: None)
    assert store.coverage(key, start, start + timedelta(days=2))["complete"]
    result = backfill_set(collector, [key], start, end, sleep=lambda _: None)[0]
    assert result["complete"] and result["coverage"]["rows"] == 31 * 24
    assert len(calls) == 4  # Original ZIP/checksum then retry; no synthetic daily fallback.
    assert store.db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 31
