from datetime import timedelta

import pytest
from conftest import zip_csv
from marketdata.domain import DAY, DataError, json_bytes, micros, utc
from marketdata.normalize import normalize_archive, normalize_rest
from marketdata.oi_quality import (
    ARCHIVE_OI,
    REST_OI,
    observation_report,
    parity_report,
    quality_report,
    write_report,
)

LABEL = utc("2026-09-22T12:00")


def receipt(index, offset, values=(), *, state="ok", clock_flags=(), legacy=False):
    requested = LABEL + timedelta(seconds=offset)
    received = requested + timedelta(seconds=2)
    rows = normalize_rest(
        json_bytes(
            [
                {
                    "symbol": "BTCUSDT",
                    "timestamp": micros(label) // 1000,
                    "sumOpenInterest": str(quantity),
                    "sumOpenInterestValue": str(quantity * 100),
                }
                for label, quantity in values
            ]
        ),
        REST_OI,
        f"source-{index}",
        received,
    ).rows
    return {
        "fetch_id": index,
        "source_id": f"source-{index}",
        "observed_at": received,
        "rows": rows if state == "ok" else None,
        "probe": None
        if legacy
        else {
            "version": 2,
            "state": state,
            "requested_at": requested,
            "query_start": LABEL,
            "query_end": requested,
            "clock_flags": list(clock_flags),
        },
    }


def test_bracket_uses_absent_request_start_and_errors_do_not_tighten_it():
    report = observation_report(
        [receipt(1, 1), receipt(2, 10, state="network_error"), receipt(3, 21, [(LABEL, 10)])],
        LABEL,
        LABEL + timedelta(seconds=30),
    )
    point = report["availability"][0]
    assert point["state"] == "bracketed"
    assert point["lower_delay_seconds"] == 1  # Not response receive time 3s.
    assert point["upper_delay_seconds"] == 23
    assert point["last_absent_fetch_id"] == 1 and point["first_present_fetch_id"] == 3
    assert report["counts"]["invalid_probes"] == 1


def test_first_presence_is_censored_and_legacy_receipts_cannot_supply_absence():
    report = observation_report(
        [
            receipt(1, 1, legacy=True),
            receipt(2, 10, state="http_error"),
            receipt(3, 21, [(LABEL, 10)]),
        ],
        LABEL,
        LABEL + timedelta(seconds=30),
    )
    point = report["availability"][0]
    assert point["state"] == "already_present_at_first_probe"
    assert point["lower_delay_seconds"] is None


def test_clock_error_is_not_absence_and_disappearance_does_not_rewrite_first_presence():
    report = observation_report(
        [
            receipt(1, 1, clock_flags=["local_clock_discontinuity"]),
            receipt(2, 10, [(LABEL, 10)]),
            receipt(3, 20),
            receipt(4, 30),
            receipt(5, 40, [(LABEL, 11)]),
            receipt(6, 50, [(LABEL, 10)]),
        ],
        LABEL,
        LABEL + timedelta(seconds=60),
    )
    point = report["availability"][0]
    assert point["first_present_fetch_id"] == 2 and point["last_absent_fetch_id"] is None
    kinds = [r["kind"] for r in report["anomalies"]]
    assert kinds.count("rest_disappeared") == 1
    assert kinds.count("rest_reappeared") == 1
    assert kinds.count("rest_value_changed") == 2
    assert report["counts"]["clock_rejected"] == 1


def test_still_absent_is_distinct_from_a_probe_outage():
    pending = observation_report([receipt(1, 75)], LABEL, LABEL + timedelta(seconds=90))
    assert pending["availability"][0]["state"] == "still_absent"
    assert pending["availability"][0]["observed_after_late_threshold"]
    outage = observation_report(
        [receipt(1, 75, state="network_error")], LABEL, LABEL + timedelta(seconds=150)
    )
    assert outage["availability"] == []
    assert outage["probe_gaps_over_45_seconds"]


def test_later_collector_response_resolves_missing_probe_label():
    report = observation_report(
        [receipt(1, 1), receipt(2, 35, [(LABEL, 10)], legacy=True)],
        LABEL,
        LABEL + timedelta(seconds=40),
    )
    point = report["availability"][0]
    assert point["state"] == "bracketed"
    assert point["lower_delay_seconds"] == 1 and point["upper_delay_seconds"] == 37
    assert point["first_present_evidence_type"] == "collector"


def test_earlier_known_presence_prevents_false_first_publication_claim():
    report = observation_report(
        [
            receipt(1, 1, [(LABEL, 10)], legacy=True),
            receipt(2, 20),
            receipt(3, 35, [(LABEL, 10)]),
        ],
        LABEL,
        LABEL + timedelta(seconds=40),
    )
    point = report["availability"][0]
    assert point["first_present_fetch_id"] == 1 and point["lower_delay_seconds"] is None
    assert {r["kind"] for r in report["anomalies"]} == {"rest_disappeared", "rest_reappeared"}


def test_ambiguous_old_query_boundary_never_establishes_absence_bound():
    old = receipt(1, 1)
    old["probe"]["version"] = 1
    report = observation_report(
        [old, receipt(2, 20, [(LABEL, 10)])], LABEL, LABEL + timedelta(seconds=30)
    )
    assert report["counts"]["boundary_policy_excluded"] == 1
    assert report["availability"][0]["lower_delay_seconds"] is None


def populate(store, *, missing_rest=False):
    begin = utc("2026-09-20")
    archive = [["create_time", "symbol", "sum_open_interest", "sum_open_interest_value"]]
    rest = []
    for i in range(288):
        stamp, value = begin + timedelta(minutes=5 * i), i + 1
        archive.append([stamp.isoformat(), "BTCUSDT", str(value), str(value * 100)])
        if not (missing_rest and i == 100):
            rest.append(
                {
                    "symbol": "BTCUSDT",
                    "timestamp": micros(stamp + timedelta(minutes=5)) // 1000,
                    "sumOpenInterest": str(value),
                    "sumOpenInterestValue": str(value * 100),
                }
            )
    observed = begin + 2 * DAY
    for key, blob, normalizer in (
        (ARCHIVE_OI, zip_csv(archive), normalize_archive),
        (REST_OI, json_bytes(rest), normalize_rest),
    ):
        identity = store.put_raw(blob, "test:source", "test", observed)
        batch = normalizer(blob, key, identity, observed)
        store.publish(
            key,
            batch,
            begin,
            begin + DAY + timedelta(minutes=5) if key == REST_OI else begin + DAY,
            identity,
        )
    return begin, begin + DAY


def test_daily_comparison_checks_day_edge_and_never_shifts_canonical_rows(store, tmp_path):
    begin, end = populate(store)
    report = quality_report(store, begin, end, as_of=LABEL)
    totals = {r["shift_seconds"]: r for r in report["parity"]["totals"]}
    assert totals[300]["exact"] == totals[300]["expected"] == 288
    assert totals[0]["exact"] == 0
    assert not report["parity"]["anomalies"]
    assert store.read(ARCHIVE_OI, begin, end)["timestamp"][0].as_py() == begin
    assert report["observations"]["availability"] == []
    output = write_report(report, tmp_path / "reports")
    assert write_report(report, tmp_path / "reports") == output
    (output / "parity.csv").write_text("corrupted")
    with pytest.raises(DataError, match="refusing to overwrite"):
        write_report(report, tmp_path / "reports")


def test_missing_and_quarantined_data_are_not_counted_as_value_mismatches(store):
    begin, end = populate(store, missing_rest=True)
    first = parity_report(store, begin, end)
    matched = next(r for r in first["totals"] if r["shift_seconds"] == 300)
    assert matched["exact"] == 287 and matched["missing_rest"] == 1 and matched["mismatch"] == 0
    assert first["anomalies"][0]["archive"]["source_id"]
    store.issue(ARCHIVE_OI, begin.date().isoformat(), "raw", "quarantined_zero_oi", {})
    blocked = parity_report(store, begin, end)
    matched = next(r for r in blocked["totals"] if r["shift_seconds"] == 300)
    assert matched["comparable"] == 0 and matched["blocked"] == 288


def test_different_reference_hypothesis_changes_alerts_not_source_values(store):
    begin, end = populate(store)
    report = parity_report(store, begin, end, reference_shift=0)
    assert len(report["anomalies"]) == 288
    assert next(r for r in report["totals"] if r["shift_seconds"] == 300)["exact"] == 288
