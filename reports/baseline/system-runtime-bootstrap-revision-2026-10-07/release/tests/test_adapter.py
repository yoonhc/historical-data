from datetime import timedelta
from decimal import Decimal

import pyarrow.feather as feather
import pytest
from conftest import kline, source_file
from marketdata.domain import DataError, Key, utc
from marketdata.pipeline import Collector
from marketdata_freqtrade.export import export_snapshot, native_table, verify_export
from marketdata_freqtrade.features import align_open_interest


def test_spot_export_aggregation_idempotence_and_integrity(store, tmp_path):
    key = Key("trade_bars", "spot")
    start, end = utc("2024-01-01"), utc("2024-01-01T01:00")
    rows = [kline(start + timedelta(minutes=i)) for i in range(60)]
    collector = Collector(store)
    collector.import_archive(source_file(tmp_path, rows), key, start, end)
    snapshot = store.create_snapshot([key], start, end)
    output = tmp_path / "export"
    first = export_snapshot(store, snapshot, output, ["1m", "5m", "1h"])
    assert export_snapshot(store, snapshot, output, ["1m", "5m", "1h"]) == first
    candles = feather.read_table(output / "data/BTC_USDT-1h.feather").to_pylist()
    assert candles == [
        {"date": start, "open": 100.0, "high": 105.0, "low": 95.0, "close": 101.0, "volume": 120.0}
    ]
    assert verify_export(output)["export_id"] == first["export_id"]
    with pytest.raises(DataError, match="different export"):
        export_snapshot(store, snapshot, output, ["5m"])
    (output / "data/BTC_USDT-1h.feather").write_bytes(b"broken")
    with pytest.raises(DataError, match="hash mismatch"):
        verify_export(output)
    collector.fetcher.close()


def test_futures_requires_risk_data_and_gaps_are_rejected(store, tmp_path):
    key = Key("trade_bars")
    collector = Collector(store)
    collector.import_archive(
        source_file(tmp_path, [kline()]), key, "2024-01-01", "2024-01-01T00:01"
    )
    snapshot = store.create_snapshot([key], "2024-01-01", "2024-01-01T00:01")
    with pytest.raises(DataError, match="requires.*mark"):
        export_snapshot(store, snapshot, tmp_path / "export", ["1m"])
    sparse = store.create_snapshot([key], "2024-01-01", "2024-01-02", allow_gaps=True)
    with pytest.raises(DataError, match="complete snapshot"):
        export_snapshot(store, sparse, tmp_path / "export", ["1m"])
    collector.fetcher.close()


def oi(ts, observed, archive=False):
    return {
        "timestamp": utc(ts),
        "reference_time": None if archive else utc(ts),
        "first_observed_at": utc(observed),
        "quantity": Decimal("10"),
        "notional_value": Decimal("1000"),
        "time_semantics": "archive_label_unverified" if archive else "rest_period_end",
    }


def test_oi_causal_availability_lag_and_staleness():
    row = oi("2024-01-01T00:05", "2024-01-01T00:07")
    decisions = ["2024-01-01T00:05", "2024-01-01T00:06", "2024-01-01T00:07", "2024-01-01T00:11"]
    modeled = align_open_interest(
        [row],
        decisions,
        mode="historical_assumption",
        publication_lag_seconds=60,
        max_age_seconds=300,
    )
    observed = align_open_interest(
        [row], decisions, mode="observed", publication_lag_seconds=60, max_age_seconds=300
    )
    assert [r is not None for r in modeled] == [False, True, True, False]
    assert [r is not None for r in observed] == [False, False, True, False]


def test_oi_archive_clock_is_explicit_and_never_automatically_shifted():
    row = oi("2024-01-01", "2026-01-01", archive=True)
    args = {"mode": "historical_assumption", "max_age_seconds": 600, "publication_lag_seconds": 0}
    with pytest.raises(DataError, match="clock-shift"):
        align_open_interest([row], ["2024-01-01"], **args)
    result = align_open_interest(
        [row], ["2024-01-01", "2024-01-01T00:05"], archive_shift_seconds=300, **args
    )
    assert result[0] is None
    assert result[1]["timestamp"] == utc("2024-01-01")
    assert result[1]["reference_time_assumed"] == utc("2024-01-01T00:05")
    with pytest.raises(DataError, match="mix"):
        align_open_interest([row, oi("2024-01-01", "2026-01-01")], [], **args)


def test_late_old_oi_does_not_replace_newer_reference():
    rows = [oi("2024-01-01T00:00", "2024-01-01T00:09"), oi("2024-01-01T00:05", "2024-01-01T00:06")]
    aligned = align_open_interest(
        rows, ["2024-01-01T00:10"], mode="observed", publication_lag_seconds=0, max_age_seconds=600
    )
    assert aligned[0]["reference_time"] == utc("2024-01-01T00:05")


def test_funding_native_alignment_does_not_hide_intrahour_events():
    import json

    import pyarrow as pa
    from marketdata.normalize import normalize_rest
    from marketdata.schema import schema

    key = Key("funding_events", interval="event")
    raw = [{"symbol": "BTCUSDT", "fundingTime": 1704067200006, "fundingRate": "-0.0001"}]
    rows = normalize_rest(json.dumps(raw).encode(), key, "source", utc("2026-01-01")).rows
    table = pa.Table.from_pylist(rows, schema=schema(key))
    assert native_table(table, key)["date"][0].as_py() == utc("2024-01-01")
    assert rows[0]["timestamp"] == utc("2024-01-01T00:00:00.006")
    rows[0]["timestamp"] += timedelta(minutes=30)
    with pytest.raises(DataError, match="hour boundary"):
        native_table(pa.Table.from_pylist(rows, schema=schema(key)), key)
