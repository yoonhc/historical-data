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


def test_historical_short_bar_preserves_source_end_and_flag_for_archive_and_rest():
    raw = [
        1582112100000,
        "10150.00000000",
        "10150.00000000",
        "10147.51000000",
        "10148.93000000",
        "2.70658300",
        1582112132286,
        "27468.11354286",
        65,
        "1.06532700",
        "10812.94997016",
        "0",
    ]
    key = Key("trade_bars", "spot")
    archive = normalize_archive(zip_csv([raw]), key, "raw", utc("2026-01-01"))
    rest = normalize_rest(json.dumps([raw]).encode(), key, "raw", utc("2026-01-01"))
    for batch in (archive, rest):
        row = batch.rows[0]
        assert row["bar_end"] == utc("2020-02-19T11:35:32.287")
        assert row["bar_end"] - row["timestamp"] == timedelta(milliseconds=32287)
        assert row["quality_flags"] == ["short_source_bar"]
        assert batch.issues[0]["nominal_bar_end"] == utc("2020-02-19T11:36")
        assert not batch.quarantine
    raw[6] = raw[0] - 1
    with pytest.raises(DataError, match="close time"):
        normalize_rest(json.dumps([raw]).encode(), key, "raw", utc("2026-01-01"))


def test_short_microsecond_bar_preserves_one_microsecond_inclusive_end():
    raw = kline("2025-01-01", unit="us")
    raw[6] = str(int(raw[0]) + 12345678)
    batch = normalize_archive(zip_csv([raw]), Key("trade_bars", "spot"), "raw", utc("2026-01-01"))
    assert batch.rows[0]["bar_end"] == utc("2025-01-01T00:00:12.345679")
    assert "short_source_bar" in batch.rows[0]["quality_flags"]


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


def test_exact_archive_metrics_duplicates_preserve_source_and_audit_ordinals(store, tmp_path):
    key = Key("open_interest", interval="5m", series="archive")
    header = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value"]
    value = ["2020-09-01 00:00:00", "BTCUSDT", "39080.23100000", "456144339.23360443"]
    path = source_file(tmp_path, [header, value, value], "BTCUSDT-metrics-2020-09-01.zip")
    original = path.read_bytes()
    collector = Collector(store)
    try:
        collector.import_archive(path, key, "2020-09-01", "2020-09-02")
        rows = store.read(key, "2020-09-01", "2020-09-02").to_pylist()
        assert len(rows) == 1 and rows[0]["source_row"] == 2
        assert "exact_source_duplicate" in rows[0]["quality_flags"]
        assert store.raw(rows[0]["source_id"])[0] == original
        batch = normalize_archive(original, key, "source", utc("2026-01-01"))
        assert batch.issues == [
            {
                "kind": "exact_source_duplicate",
                "timestamp": utc("2020-09-01"),
                "kept_source_row": 2,
                "duplicate_source_row": 3,
            }
        ]
        # Exact duplicates of a zero observation must still quarantine the day.
        value[2] = "0"
        batch = normalize_archive(zip_csv([header, value, value]), key, "source", utc("2026-01-01"))
        assert batch.quarantine and len(batch.rows) == 1
    finally:
        collector.fetcher.close()


@pytest.mark.parametrize("column", [2, 4])
def test_metrics_duplicate_with_any_different_field_is_rejected(column):
    key = Key("open_interest", interval="5m", series="archive")
    header = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value", "ratio"]
    value = ["2020-09-01 00:00:00", "BTCUSDT", "10", "100", "1.2"]
    other = value.copy()
    other[column] = "20"
    with pytest.raises(DataError, match="Conflicting duplicate"):
        normalize_archive(zip_csv([header, value, other]), key, "source", utc("2026-01-01"))


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
