"""Early renewal against an independent full exchange book, plus quota/deadline cases."""

import json
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from marketdata import depth
from marketdata import depth_stream as live
from marketdata.binance import Fetcher, SourceError
from marketdata.depth_refresh import EXTRA_REQUESTS, RefreshChecks, headroom
from marketdata.domain import DataError, micros, utc
from test_depth import BASE, row, snapshot


class Exchange:
    """Full market state is mutated before generating messages and fresh REST data."""

    def __init__(self, monkeypatch, *, shock=False, mismatch=False, future=False):
        self.ms, self.identity, self.receipts, self.connections = 0, 1000, [], 0
        self.bids = {p: Decimal(1) for p in range(1, 1001)}
        self.asks = {p: Decimal(1) for p in range(1001, 10001)}
        self.shock, self.mismatch, self.future = shock, mismatch, future
        self.calls = []
        monkeypatch.setattr(live.time, "monotonic", lambda: self.ms / 1000)
        monkeypatch.setattr(live.time, "sleep", self.sleep)
        monkeypatch.setattr(live, "now", lambda: BASE + timedelta(milliseconds=self.ms))
        self.fetcher = Fetcher(
            httpx.Client(transport=httpx.MockTransport(self.fetch)), retries=0, sleep=self.sleep
        )

    def sleep(self, seconds):
        self.ms += round(seconds * 1000)

    def fetch(self, request):
        self.calls.append(self.ms / 1000)
        identity = self.identity + (50 if self.future and len(self.calls) > 1 else 0)
        payload = {
            "lastUpdateId": identity,
            "T": micros(BASE) // 1000 + identity,
            "E": micros(BASE) // 1000 + identity + 1,
            "bids": [[str(p), str(self.bids[p])] for p in sorted(self.bids, reverse=True)[:100]],
            "asks": [[str(p), str(self.asks[p])] for p in sorted(self.asks)[:100]],
        }
        if self.mismatch and len(self.calls) == 2:
            payload["bids"][0][1] = "9"
        return httpx.Response(200, json=payload)

    @contextmanager
    def connector(self, *args, **kwargs):
        self.connections += 1

        def recv(timeout):
            self.ms += 100
            previous = self.identity
            self.identity += 1
            remove = 90 if self.shock and len(self.receipts) == 1 else (0 if self.shock else 2)
            prices = sorted(self.asks)[:remove]
            for p in prices:
                del self.asks[p]
            raw = {
                "e": "depthUpdate",
                "s": "BTCUSDT",
                "st": 1,
                "U": self.identity,
                "u": self.identity,
                "pu": previous,
                "T": micros(BASE) // 1000 + self.identity,
                "E": micros(BASE) // 1000 + self.identity + 1,
                "b": [],
                "a": [[str(p), "0"] for p in prices],
            }
            self.receipts.append(raw)
            return json.dumps(raw)

        yield SimpleNamespace(recv=recv)

    def capture(self, store, **kwargs):
        try:
            return live.capture(
                store,
                seconds=kwargs.pop("seconds", 6),
                top=20,
                verify_seconds=10,
                min_free_gib=0,
                connector=self.connector,
                fetcher=self.fetcher,
                **kwargs,
            )
        finally:
            self.fetcher.client.close()


def assert_exact_top(report, exchange):
    actual = report["epochs"][-1]["final"]["book"]
    assert actual["top_complete"]
    assert actual["last_update_id"] == exchange.identity
    assert actual["bids"] == [
        (Decimal(p), exchange.bids[p]) for p in sorted(exchange.bids, reverse=True)[:20]
    ]
    assert actual["asks"] == [(Decimal(p), exchange.asks[p]) for p in sorted(exchange.asks)[:20]]


def test_early_checks_prevent_a_five_second_range_exhaustion_with_exact_exchange_top(
    store, monkeypatch
):
    exchange = Exchange(monkeypatch)
    report = exchange.capture(store)
    assert report["complete"] and report["sync_losses"] == report["coverage_resets"] == 0
    assert 1 < exchange.calls[1] - exchange.calls[0] < 3
    assert 2 <= report["coverage_check_requests"] <= 5
    assert report["messages"] == report["persisted_messages"] == len(exchange.receipts)
    assert all(b - a >= 1 for a, b in zip(exchange.calls, exchange.calls[1:]))
    assert_exact_top(report, exchange)
    replay = depth.replay(store)
    assert replay["valid"] and replay["replay_matches_live"] and replay["verification_complete"]
    # With the initial 100-level snapshot, these independent deletions remove
    # its entire ask range within five seconds. No historical REST is fabricated.
    initial_limit = 1102
    assert not [p for p in exchange.asks if p <= initial_limit]


def test_sudden_top_shortfall_is_explicit_and_reanchors_on_same_socket(store, monkeypatch):
    exchange = Exchange(monkeypatch, shock=True)
    report = exchange.capture(store, seconds=2)
    assert report["state"] == "partial" and report["connections"] == 1
    assert report["coverage_top_shortfalls"] == report["coverage_resets"] == 1
    old, new = report["epochs"]
    assert old["end_reason"] == "incomplete_top"
    assert old["final"]["book"]["synced"] and not old["final"]["book"]["top_complete"]
    assert old["coverage_shortfall"]["sides"]["asks"]["levels"] == 10
    assert new["reason"] == "same_socket_after_incomplete_top"
    assert report["messages"] == report["persisted_messages"] == len(exchange.receipts)
    assert_exact_top(report, exchange)
    replay = depth.replay(store)
    assert replay["replay_matches_live"] and not replay["valid"]


def test_early_check_still_rejects_a_real_price_quantity_mismatch(store, monkeypatch):
    exchange = Exchange(monkeypatch, mismatch=True)
    report = exchange.capture(store, seconds=4)
    assert report["sync_losses"] == 1 and report["connections"] == 2
    assert "disagrees" in report["epochs"][0]["final"]["error"]
    assert depth.replay(store)["replay_matches_live"]


def test_pending_future_check_blocks_additional_requests(store, monkeypatch):
    exchange = Exchange(monkeypatch, future=True)
    report = exchange.capture(store, seconds=2)
    assert len(exchange.calls) == 2
    assert report["coverage_check_requests"] == 1
    replay = depth.replay(store)
    assert replay["verification_pending"] == 1 and not replay["verification_complete"]


def test_deadline_during_check_flush_does_not_send_a_request(store, monkeypatch):
    exchange = Exchange(monkeypatch)
    persist = live.persist_messages

    def slow(*args):
        result = persist(*args)
        if exchange.ms >= 1300:
            exchange.ms += 6000
        return result

    monkeypatch.setattr(live, "persist_messages", slow)
    report = exchange.capture(store, seconds=3)
    assert len(exchange.calls) == 1 and report["coverage_check_requests"] == 0
    assert report["messages"] == report["persisted_messages"]


@pytest.mark.parametrize("status,minimum", [(429, 300), (418, 3600)])
def test_early_check_rate_limit_stops_requests_and_preserves_receipts(
    store, monkeypatch, status, minimum
):
    exchange = Exchange(monkeypatch)
    original = exchange.fetch

    def limited(request):
        if not exchange.calls:
            return original(request)
        exchange.calls.append(exchange.ms / 1000)
        return httpx.Response(status, content=b"limited early check")

    exchange.fetcher.client.close()
    exchange.fetcher = Fetcher(
        httpx.Client(transport=httpx.MockTransport(limited)), retries=0, sleep=exchange.sleep
    )
    with pytest.raises(SourceError):
        exchange.capture(store)
    report = json.loads((store.root / "state/depth-latest.json").read_bytes())
    assert report["state"] == "failed" and report["coverage_check_requests"] == 1
    assert report["messages"] == report["persisted_messages"] == len(exchange.receipts)
    assert len(exchange.calls) == 2 and exchange.connections == 1
    cooldown = json.loads((store.root / "state/depth-rest-cooldown.json").read_bytes())
    assert utc(cooldown["until"]) >= BASE + timedelta(milliseconds=exchange.ms, seconds=minimum)
    with pytest.raises(DataError, match="REST cooldown"):
        live.snapshot(store, exchange.fetcher, report["id"], 0, 10000, "check")
    assert len(exchange.calls) == 2


def test_deadline_during_shortfall_flush_keeps_partial_without_new_anchor(store, monkeypatch):
    exchange = Exchange(monkeypatch, shock=True)
    persist = live.persist_messages

    def slow(*args):
        result = persist(*args)
        if exchange.ms >= 200:
            exchange.ms += 4000
        return result

    monkeypatch.setattr(live, "persist_messages", slow)
    report = exchange.capture(store, seconds=2)
    assert len(exchange.calls) == 1 and report["state"] == "partial"
    assert report["coverage_top_shortfalls"] == 1
    assert not report["synchronized"]
    assert report["messages"] == report["persisted_messages"] == len(exchange.receipts)
    replay = depth.replay(store)
    assert replay["replay_matches_live"] and not replay["valid"]


def test_extra_quota_defers_without_sleep_and_periodic_checks_remain_available():
    book = depth.Book(row(snapshot(), "anchor"))
    del book.asks[Decimal(101)]
    del book.asks[Decimal(102)]
    controller = RefreshChecks()
    for index in range(EXTRA_REQUESTS):
        stamp = index + 1.0
        decision = controller.decision(
            book, 1, current=stamp, last_request=stamp - 1, periodic_seconds=10
        )
        assert decision["reason"] == "headroom"
        controller.claim(decision, stamp)
    assert controller.decision(book, 1, current=13, last_request=12, periodic_seconds=10) is None
    assert (
        controller.decision(book, 1, current=22, last_request=12, periodic_seconds=10)["reason"]
        == "periodic"
    )
    assert (
        controller.decision(book, 1, current=61, last_request=60, periodic_seconds=10)["reason"]
        == "headroom"
    )


def test_price_room_detects_risk_even_with_many_new_levels_inside_the_range():
    book = depth.Book(row(snapshot(), "anchor"))
    # Abundant levels near the boundary do not imply a wide known price range.
    book.asks = {Decimal(102) + Decimal(i) / 100: Decimal(1) for i in range(80, 100)}
    margin = headroom(book, 2)
    assert not margin["sides"]["asks"]["low_levels"]
    assert margin["sides"]["asks"]["low_price_room"] and margin["low"]


@pytest.mark.parametrize("elapsed", [0, 0.5, 0.999])
def test_low_headroom_never_bypasses_minimum_interval(elapsed):
    book = depth.Book(row(snapshot(), "anchor"))
    book.asks = {Decimal(103): Decimal(1)}
    assert (
        RefreshChecks().decision(book, 1, current=elapsed, last_request=0, periodic_seconds=10)
        is None
    )
