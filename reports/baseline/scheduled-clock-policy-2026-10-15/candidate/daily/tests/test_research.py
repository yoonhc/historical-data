from datetime import timedelta

import pytest
from conftest import zip_csv
from marketdata.domain import DataError, Key, utc
from marketdata.normalize import normalize_archive
from marketdata.research import available_ranges, research_report
from test_operations import publish_range


def test_available_ranges_clip_merge_and_align_all_input_grids():
    start, end = utc("2024-01-01"), utc("2024-01-02")
    blocked = [
        [start - timedelta(days=1), start + timedelta(minutes=2)],
        [start + timedelta(minutes=10), start + timedelta(minutes=12)],
        [start + timedelta(minutes=11), start + timedelta(minutes=17)],
        [end, end + timedelta(days=1)],
    ]
    assert available_ranges(start, end, blocked, 300) == [
        [start + timedelta(minutes=5), start + timedelta(minutes=10)],
        [start + timedelta(minutes=20), end],
    ]


def test_research_profiles_keep_core_and_oi_quality_separate(store):
    start, end = utc("2024-01-01"), utc("2024-01-04")
    spot = Key("trade_bars", market="spot")
    futures = Key("trade_bars")
    funding = Key("funding_events", interval="event")
    oi = Key("open_interest", interval="5m", series="archive")
    for key in [spot, futures, funding]:
        publish_range(store, key, start, end)
    values = [["create_time", "symbol", "sum_open_interest", "sum_open_interest_value"]]
    values.extend(
        [(start + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M:%S"), "BTCUSDT", "10", "1000"]
        for i in range(3 * 288)
    )
    member = "BTCUSDT-metrics-2024-01-01.csv"
    blob = zip_csv(values, member)
    source_id = store.put_raw(blob, "test:archive", "archive")
    batch = normalize_archive(blob, oi, source_id, end, member)
    store.publish(oi, batch, start, end, source_id)
    store.issue(oi, "2024-01-02", "zero", "quarantined_zero_oi", {})
    report = research_report(store, [spot, futures, funding, oi], start, end)
    assert report["profiles"]["core"]["complete"]
    assert report["profiles"]["core"]["contiguous_ranges"] == [[start, end]]
    combined = report["profiles"]["core_with_archive_oi"]
    assert not combined["complete"] and combined["covered_days"] == 2
    assert combined["contiguous_ranges"] == [[start, utc("2024-01-02")], [utc("2024-01-03"), end]]
    assert combined["oi_clock_and_units_verified"] is False
    assert "unverified" in combined["interpretation"]
    oi_ledger = next(r for r in report["datasets"] if r["key"] == oi.asdict())
    assert oi_ledger["missing_rows"] == 0 and oi_ledger["blocked_days"] == ["2024-01-02"]
    assert not oi_ledger["monthly"][0]["complete"]


def test_research_funding_holes_use_query_ranges_not_invented_events(store):
    key = Key("funding_events", interval="event")
    publish_range(store, key, "2024-01-01", "2024-01-02")
    publish_range(store, key, "2024-01-03", "2024-01-04")
    report = research_report(store, [key], "2024-01-01", "2024-01-04")
    dataset = report["datasets"][0]
    assert dataset["rows"] == 6 and dataset["missing_rows"] is None
    assert dataset["missing_ranges"] == [[utc("2024-01-02"), utc("2024-01-03")]]
    assert report["profiles"]["futures"]["covered_days"] == 2


def test_monthly_coverage_joins_boundary_gaps_without_losing_counts(store):
    key = Key("trade_bars", interval="1h")
    start, end = utc("2024-01-31"), utc("2024-02-02")
    publish_range(store, key, start, end, omit=(23, 24))
    report = research_report(store, [key], start, end)
    dataset = report["datasets"][0]
    assert dataset["expected_rows"] == 48 and dataset["rows"] == 46
    assert dataset["missing_rows"] == 2 and len(dataset["monthly"]) == 2
    assert dataset["missing_ranges"] == [[utc("2024-01-31T23:00"), utc("2024-02-01T01:00")]]


def test_present_metadata_cannot_imply_historical_research_coverage(store):
    with pytest.raises(DataError, match="historical"):
        research_report(
            store, [Key("instrument_metadata", interval="event")], "2024-01-01", "2024-01-04"
        )


def test_auxiliary_price_gap_does_not_block_price_funding_statistics(store):
    trade = Key("trade_bars", interval="1h")
    mark = Key("mark_price_bars", interval="1h")
    funding = Key("funding_events", interval="event")
    start, end = utc("2024-01-01"), utc("2024-01-04")
    publish_range(store, trade, start, end)
    publish_range(store, funding, start, end)
    publish_range(store, mark, start, end, omit=(24,))
    report = research_report(store, [trade, mark, funding], start, end)
    assert report["profiles"]["price_funding"]["complete"]
    assert not report["profiles"]["futures"]["complete"]
    assert len(report["profiles"]["core"]["contiguous_ranges"]) == 2
    for begin, stop in report["profiles"]["core"]["contiguous_ranges"]:
        identity = store.create_snapshot([trade, mark, funding], begin, stop)
        assert all(c["complete"] for c in store.snapshot_manifest(identity)["coverage"])
