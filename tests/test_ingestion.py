import json
from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import kline, source_file, zip_csv
from marketdata.domain import ConflictError, DataError, Key, number, utc
from marketdata.normalize import normalize_archive, normalize_rest
from marketdata.pipeline import Collector
from marketdata.transforms import resample


def test_spot_microsecond_transition():
    key = Key("trade_bars", "spot")
    for day, unit in (("2024-12-31", "ms"), ("2025-01-01", "us")):
        batch = normalize_archive(zip_csv([kline(day, unit=unit)]), key, "raw", utc("2026-01-01"))
        assert batch.rows[0]["timestamp"] == utc(day)
        assert batch.rows[0]["bar_end"] == utc(day) + timedelta(minutes=1)
    with pytest.raises(DataError, match="period contract"):
        normalize_archive(zip_csv([kline("2025-01-01", unit="ms")]), key, "raw", utc("2026-01-01"))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e20", "0.0000000000000000001"])
def test_decimal_rejects_loss(value):
    with pytest.raises(DataError):
        number(value)


def test_negative_funding_is_an_event():
    key = Key("funding_events", interval="event")
    batch = normalize_rest(
        json.dumps(
            [{"symbol": "BTCUSDT", "fundingTime": 1704067200000, "fundingRate": "-0.00012"}]
        ).encode(),
        key,
        "raw",
        utc("2026-01-01"),
    )
    assert batch.rows[0]["funding_rate"] == Decimal("-0.00012")
    assert "volume" not in batch.rows[0]


def test_price_bars_do_not_invent_volume():
    key = Key("mark_price_bars")
    row = normalize_archive(zip_csv([kline()]), key, "raw", utc("2026-01-01")).rows[0]
    assert "base_volume" not in row


def test_duplicate_source_keys_rejected():
    with pytest.raises(DataError, match="Duplicate"):
        normalize_archive(zip_csv([kline(), kline()]), Key("trade_bars"), "raw", utc("2026-01-01"))


def test_bad_ohlc_and_close_time():
    row = kline()
    row[2] = "90"
    with pytest.raises(DataError, match="OHLC"):
        normalize_archive(zip_csv([row]), Key("trade_bars"), "raw", utc("2026-01-01"))
    row = kline()
    row[6] = str(int(row[6]) + 1)
    with pytest.raises(DataError, match="close time"):
        normalize_archive(zip_csv([row]), Key("trade_bars"), "raw", utc("2026-01-01"))


def test_idempotent_import_and_snapshot_revision(store, tmp_path):
    key = Key("trade_bars")
    collector = Collector(store)
    path = source_file(tmp_path, [kline()])
    first = collector.import_archive(path, key, "2024-01-01", "2024-01-01T00:01")
    snapshot = store.create_snapshot([key], "2024-01-01", "2024-01-01T00:01")
    repeated = collector.import_archive(path, key, "2024-01-01", "2024-01-01T00:01")
    assert first == repeated
    assert store.db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 1
    source_file(tmp_path, [kline(price="102")])
    with pytest.raises(ConflictError):
        collector.import_archive(path, key, "2024-01-01", "2024-01-01T00:01")
    assert not store.coverage(key, "2024-01-01", "2024-01-01T00:01")["complete"]
    collector.import_archive(path, key, "2024-01-01", "2024-01-01T00:01", accept_revisions=True)
    assert store.read(key, "2024-01-01", "2024-01-01T00:01")["open"][0].as_py() == Decimal("102")
    assert store.read(key, "2024-01-01", "2024-01-01T00:01", snapshot)["open"][
        0
    ].as_py() == Decimal("100")
    assert store.coverage(key, "2024-01-01", "2024-01-01T00:01")["complete"]
    collector.fetcher.close()


def test_missing_bar_is_not_filled_and_resampling_refuses(store, tmp_path):
    key = Key("trade_bars")
    collector = Collector(store)
    rows = [kline(utc("2024-01-01") + timedelta(minutes=i)) for i in (0, 1, 3, 4)]
    collector.import_archive(source_file(tmp_path, rows), key, "2024-01-01", "2024-01-01T00:05")
    coverage = store.coverage(key, "2024-01-01", "2024-01-01T00:05")
    assert coverage["missing"] == 1 and not coverage["complete"]
    with pytest.raises(DataError, match="gaps"):
        store.create_snapshot([key], "2024-01-01", "2024-01-01T00:05")
    with pytest.raises(DataError, match="Incomplete"):
        resample(store.read(key, "2024-01-01", "2024-01-01T00:05"), key, "5m")
    collector.fetcher.close()


def test_resampling_exact_totals(store, tmp_path):
    key = Key("trade_bars")
    collector = Collector(store)
    rows = [kline(utc("2024-01-01") + timedelta(minutes=i)) for i in range(5)]
    collector.import_archive(source_file(tmp_path, rows), key, "2024-01-01", "2024-01-01T00:05")
    result = resample(store.read(key, "2024-01-01", "2024-01-01T00:05"), key, "5m").to_pylist()
    assert len(result) == 1
    assert result[0]["base_volume"] == Decimal("10")
    assert result[0]["trade_count"] == 15
    assert result[0]["bar_end"] == utc("2024-01-01T00:05")
    collector.fetcher.close()


def test_bad_checksum_never_published(store, tmp_path):
    path = source_file(tmp_path, [kline()])
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(DataError, match="CHECKSUM"):
        Collector(store).import_archive(path, Key("trade_bars"), "2024-01-01", "2024-01-02")
    assert store.db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 0


def test_oi_label_retained_unordered_flag_and_zero_quarantine(store, tmp_path):
    key = Key("open_interest", interval="5m", series="archive")
    header = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value"]
    rows = [
        header,
        ["2024-01-01 00:05:00", "BTCUSDT", "10", "100"],
        ["2024-01-01 00:00:00", "BTCUSDT", "11", "110"],
    ]
    path = source_file(tmp_path, rows, "BTCUSDT-metrics-2024-01-01.zip")
    collector = Collector(store)
    collector.import_archive(path, key, "2024-01-01", "2024-01-02")
    result = store.read(key, "2024-01-01", "2024-01-02").to_pylist()
    assert result[0]["timestamp"] == utc("2024-01-01")
    assert result[0]["reference_time"] is None
    assert result[0]["source_row"] == 3
    assert "source_order_reversed" in result[0]["quality_flags"]
    rows[1][2] = "0"
    source_file(tmp_path, rows, path.name)
    output = collector.import_archive(path, key, "2024-01-01", "2024-01-02")
    assert output[0]["state"] == "quarantined"
    assert store.coverage(key, "2024-01-01", "2024-01-02")["blocking_issues"]
    assert len(store.read(key, "2024-01-01", "2024-01-02")) == 2  # Older revision retained.
    collector.fetcher.close()


def test_oi_series_cannot_be_mixed():
    with pytest.raises(DataError):
        Key("open_interest", interval="5m")


def test_canonical_integrity_and_snapshot_bounds(store, tmp_path):
    key = Key("trade_bars")
    collector = Collector(store)
    collector.import_archive(
        source_file(tmp_path, [kline()]), key, "2024-01-01", "2024-01-01T00:01"
    )
    snapshot = store.create_snapshot([key], "2024-01-01", "2024-01-01T00:01")
    with pytest.raises(DataError, match="outside snapshot"):
        store.read(key, "2024-01-01", "2024-01-02", snapshot)
    record = store.db.execute("SELECT * FROM revisions").fetchone()
    (store.root / record["path"]).write_bytes(b"corrupted")
    assert not store.validate()["ok"]
    with pytest.raises(DataError, match="hash mismatch"):
        store.read(key, "2024-01-01", "2024-01-01T00:01", snapshot)
    collector.fetcher.close()
