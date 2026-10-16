import json
from datetime import timedelta

import pytest
from marketdata.binance import Response, SourceError
from marketdata.domain import json_bytes, micros, utc
from marketdata.oi_probe import probe_once

START = utc("2026-09-22T12:00:00.100")


def oi(label="2026-09-22T12:00", quantity="10"):
    return {
        "symbol": "BTCUSDT",
        "timestamp": micros(utc(label)) // 1000,
        "sumOpenInterest": quantity,
        "sumOpenInterestValue": "1000",
    }


class Fetch:
    def __init__(self, store, body=b"[]", error=None):
        self.store, self.body, self.error = store, body, error
        self.calls = 0

    def fetch(self, url, weight):
        self.calls += 1
        # The HTTP request must not hold the canonical writer.
        with self.store.writer():
            pass
        if self.error:
            raise self.error
        return Response(self.body, url, START + timedelta(seconds=0.2), {})


def run(store, fetcher):
    ticks = iter([0.0, 0.2])
    return probe_once(store, fetcher, clock=lambda: START, monotonic=lambda: next(ticks))


def test_empty_success_is_retained_per_request_without_publishing(store):
    first = run(store, Fetch(store))
    second = run(store, Fetch(store))
    assert first["complete"] and not first["target_present"]
    assert first["source_id"] == second["source_id"]
    assert first["probe_id"] != second["probe_id"]
    assert store.db.execute("SELECT COUNT(*) FROM fetches").fetchone()[0] == 2
    assert store.db.execute("SELECT COUNT(*) FROM active").fetchone()[0] == 0
    assert "endTime=1790078400099" in first["url"]
    assert store.raw(first["source_id"])[0] == b"[]"


@pytest.mark.parametrize("body", [json_bytes([oi(quantity="0")]), json_bytes([oi(), oi()]), b"{}"])
def test_invalid_response_is_never_absence_evidence(store, body):
    result = run(store, Fetch(store, body))
    assert result["state"] == "invalid_response" and not result["complete"]
    assert "target_present" not in result
    metadata = json.loads(store.db.execute("SELECT metadata FROM fetches").fetchone()[0])
    assert metadata["oi_probe"]["state"] == "invalid_response"


def test_rate_limit_cooldown_honors_retry_after_and_does_not_fake_observations(store):
    source = Fetch(
        store, error=SourceError("rate limit", 429, b"limited", headers={"retry-after": "600"})
    )
    result = run(store, source)
    assert result["state"] == "http_error" and not result["complete"]
    assert utc(result["cooldown_until"]) == START + timedelta(seconds=600)
    assert run(store, source)["state"] == "cooldown"
    assert source.calls == 1
    assert store.db.execute("SELECT COUNT(*) FROM fetches").fetchone()[0] == 1


def test_clock_jump_marks_receipt_unusable_for_delay(store):
    ticks = iter([0.0, 20.0])
    result = probe_once(
        store, Fetch(store, json_bytes([oi()])), clock=lambda: START, monotonic=lambda: next(ticks)
    )
    assert result["state"] == "ok" and result["target_present"]
    assert not result["complete"]
    assert result["clock_flags"] == ["local_clock_discontinuity"]


def test_exact_boundary_does_not_create_false_absence_evidence(store):
    source = Fetch(store)
    result = probe_once(store, source, clock=lambda: utc("2026-09-22T12:00"))
    assert result["state"] == "boundary_wait" and source.calls == 0
    assert store.db.execute("SELECT COUNT(*) FROM fetches").fetchone()[0] == 0
