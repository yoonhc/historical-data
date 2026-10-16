from datetime import timedelta

import pyarrow.feather as feather
import pyarrow.parquet as pq
import pytest
from conftest import kline, source_file
from marketdata.domain import DataError, Key, utc
from marketdata.pipeline import Collector
from marketdata_freqtrade.export import export_snapshot, verify_export
from marketdata_freqtrade.window import export_window


@pytest.fixture
def pinned_days(store, tmp_path):
    key = Key("trade_bars", "spot")
    start, end = utc("2024-01-01"), utc("2024-01-03")
    collector = Collector(store)
    for day in (start, start + timedelta(days=1)):
        rows = [kline(day + timedelta(minutes=i), price=str(100 + i % 4)) for i in range(1440)]
        collector.import_archive(
            source_file(tmp_path, rows, f"{day.date()}.zip"), key, day, day + timedelta(days=1)
        )
    snapshot = store.create_snapshot([key], start, end)
    yield key, snapshot, start, end, collector
    collector.fetcher.close()


def test_streaming_matches_legacy_across_days_and_keeps_pinned_values(store, tmp_path, pinned_days):
    key, snapshot, start, end, collector = pinned_days
    legacy, streaming = tmp_path / "legacy", tmp_path / "streaming"
    old = export_snapshot(store, snapshot, legacy)
    changed = [kline(start + timedelta(minutes=i), price="104") for i in range(1440)]
    collector.import_archive(
        source_file(tmp_path, changed, "changed.zip"),
        key,
        start,
        start + timedelta(days=1),
        accept_revisions=True,
    )
    # The export must use immutable snapshot revisions, and must not take the writer lock.
    with store.writer():
        result = export_window(store, snapshot, streaming, start, end)
    for item in old["files"]:
        reader = feather.read_table if item["native"] else pq.read_table
        assert reader(legacy / item["path"]).equals(reader(streaming / item["path"]))
    assert verify_export(streaming) == result
    assert export_window(store, snapshot, streaming, start, end) == result
    assert export_window(store, snapshot, tmp_path / "repeat", start, end) == result


def test_explicit_window_trims_only_requested_edges(store, tmp_path, pinned_days):
    _, snapshot, start, end, _ = pinned_days
    output = tmp_path / "window"
    begin, finish = start + timedelta(hours=23), end - timedelta(hours=23)
    result = export_window(store, snapshot, output, begin, finish)
    rows = feather.read_table(output / "data/BTC_USDT-1h.feather").to_pylist()
    assert [r["date"] for r in rows] == [begin, begin + timedelta(hours=1)]
    assert result["start"] == begin.isoformat()
    assert utc(result["source_snapshot_start"]) == start
    with pytest.raises(DataError, match="different export"):
        export_window(store, snapshot, output, start, end)
    with pytest.raises(DataError, match="align"):
        export_window(store, snapshot, tmp_path / "unaligned", start + timedelta(minutes=1), end)
    with pytest.raises(DataError, match="inside"):
        export_window(store, snapshot, tmp_path / "outside", start - timedelta(hours=1), end)


def test_corruption_cleans_partial_export_and_incomplete_snapshot_rejected(
    store, tmp_path, pinned_days
):
    key, snapshot, start, end, _ = pinned_days
    sparse = store.create_snapshot([key], start, end + timedelta(days=1), allow_gaps=True)
    with pytest.raises(DataError, match="complete snapshot"):
        export_window(store, sparse, tmp_path / "sparse", start, end)
    last = store.revision_list(key, start, end, snapshot)[-1]
    (store.root / last["path"]).write_bytes(b"corrupted")
    with pytest.raises(DataError):
        export_window(store, snapshot, tmp_path / "broken", start, end)
    assert not (tmp_path / "broken").exists()
    assert not list(tmp_path.glob(".export-*"))
