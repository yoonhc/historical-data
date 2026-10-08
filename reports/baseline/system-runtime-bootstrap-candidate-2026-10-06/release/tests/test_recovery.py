from datetime import timedelta

import marketdata.store as storage
import pytest
from conftest import kline, source_file
from marketdata.domain import Key, utc
from marketdata.pipeline import Collector


def test_crash_after_parquet_write_can_resume_without_duplicate_rows(store, tmp_path, monkeypatch):
    collector = Collector(store)
    path = source_file(tmp_path, [kline()])
    key = Key("trade_bars")
    original_write = storage.atomic_write

    def interrupted_write(path, blob):
        original_write(path, blob)
        if path.suffix == ".parquet":
            raise OSError("simulated interruption before catalog commit")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "atomic_write", interrupted_write)
        with pytest.raises(OSError, match="interruption"):
            collector.import_archive(path, key, "2024-01-01", "2024-01-01T00:01")
    assert store.db.execute("SELECT count(*) FROM active").fetchone()[0] == 0
    collector.import_archive(path, key, "2024-01-01", "2024-01-01T00:01")
    collector.import_archive(path, key, "2024-01-01", "2024-01-01T00:01")
    assert len(store.read(key, "2024-01-01", "2024-01-01T00:01")) == 1
    assert len(list((store.root / "data/canonical").rglob("*.parquet"))) == 1
    collector.fetcher.close()


def test_authoritative_subrange_preserves_other_minutes(store, tmp_path):
    collector, key = Collector(store), Key("trade_bars")
    start = utc("2024-01-01")
    rows = [kline(start + timedelta(minutes=i)) for i in range(5)]
    path = source_file(tmp_path, rows)
    collector.import_archive(path, key, start, start + timedelta(minutes=5))
    source_file(tmp_path, [rows[2]])
    collector.import_archive(path, key, start + timedelta(minutes=2), start + timedelta(minutes=3))
    assert len(store.read(key, start, start + timedelta(minutes=5))) == 5
    collector.fetcher.close()


def test_partial_repair_cannot_clear_quarantined_oi_day(store, tmp_path):
    collector = Collector(store)
    key = Key("open_interest", interval="5m", series="archive")
    header = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value"]
    path = source_file(
        tmp_path,
        [header, ["2024-01-01 00:00:00", "BTCUSDT", "0", "0"]],
        "BTCUSDT-metrics-2024-01-01.zip",
    )
    collector.import_archive(path, key, "2024-01-01", "2024-01-02")
    collector.import_archive(path, key, "2024-01-01", "2024-01-02")
    assert store.db.execute("SELECT count(*) FROM issues").fetchone()[0] == 1
    source_file(tmp_path, [header, ["2024-01-01 00:05:00", "BTCUSDT", "10", "1000"]], path.name)
    collector.import_archive(
        path, key, "2024-01-01T00:05", "2024-01-01T00:10", accept_revisions=True
    )
    coverage = store.coverage(key, "2024-01-01T00:05", "2024-01-01T00:10")
    assert coverage["blocking_issues"] and not coverage["complete"]
    collector.fetcher.close()
