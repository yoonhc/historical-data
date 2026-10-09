"""Synthetic full-market comparison through the actual capture/REST/replay pipeline.

No network, live store, or historical counterfactual REST is used. Market events
continue during simulated REST/flush delays. Closed-form full-market top levels
are checked independently at every synchronized event.
"""

import argparse
import hashlib
import json
import math
import time
from collections import deque
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from marketdata import binance, depth, depth_refresh
from marketdata import depth_stream as live
from marketdata.domain import json_bytes, micros, utc
from marketdata.store import Store

from scripts.verify_depth_capture import verify

BASE = utc("2024-01-01")
PRICE = 100000
SCENARIOS = {
    "quiet": {"seconds": 35, "latency_ms": 50, "flush_ms": 0},
    "steady": {"seconds": 70, "latency_ms": 50, "flush_ms": 0},
    "fast": {"seconds": 50, "latency_ms": 50, "flush_ms": 0},
    "oscillation": {"seconds": 35, "latency_ms": 50, "flush_ms": 0},
    "reserve_help": {"seconds": 30, "latency_ms": 50, "flush_ms": 0},
    "reserve_delay_risk": {"seconds": 25, "latency_ms": 250, "flush_ms": 0},
    "slow_rest": {"seconds": 30, "latency_ms": 700, "flush_ms": 0, "path": "reserve_help"},
    "slow_flush": {"seconds": 30, "latency_ms": 50, "flush_ms": 150, "path": "reserve_help"},
    "single_jump": {"seconds": 15, "latency_ms": 50, "flush_ms": 0},
    "critical_pressure": {"seconds": 45, "latency_ms": 50, "flush_ms": 0},
    "rotating": {
        "seconds": 45,
        "latency_ms": 50,
        "flush_ms": 0,
        "path": "oscillation",
        "rotate_seconds": 10,
    },
}


def position(name, tick):
    ms = tick * 100
    if name == "quiet":
        return round(10 * math.sin(ms / 2000))
    if name == "steady":
        return ms * 80 // 1000
    if name == "fast":
        return ms * 200 // 1000
    if name == "single_jump":
        return 1200 if ms >= 5000 else 0
    if name == "critical_pressure":
        return (ms // 2000) * 600
    if name == "reserve_help" and ms >= 21000:
        return 650 if ms < 21200 else 950 if ms < 21400 else 1250
    if name == "reserve_delay_risk" and ms >= 18000:
        return 300 if ms < 18100 else 700 if ms < 18300 else 1100
    waves = 10 if name == "reserve_delay_risk" else 12 if name == "reserve_help" else 100
    return (min(waves, ms // 1500) % 2) * 260


class Market:
    def __init__(self, name):
        self.config = SCENARIOS[name]
        self.path = self.config.get("path", name)
        self.ms = self.tick = self.received = self.comparisons = self.max_queue = 0
        self.queue = None
        self.calls = []
        self.min_known_levels = 1000
        self.top_incomplete_events = 0

    def advance(self, ms):
        until = self.ms + round(ms)
        while (self.tick + 1) * 100 <= until:
            old = position(self.path, self.tick)
            self.tick += 1
            new = position(self.path, self.tick)
            if self.queue is not None:
                prices = range(PRICE + min(old, new) + 1, PRICE + max(old, new) + 1)
                increasing = new > old
                raw = {
                    "e": "depthUpdate",
                    "s": "BTCUSDT",
                    "st": 1,
                    "U": 1000 + self.tick,
                    "u": 1000 + self.tick,
                    "pu": 999 + self.tick,
                    "T": micros(BASE) // 1000 + self.tick * 100,
                    "E": micros(BASE) // 1000 + self.tick * 100,
                    "b": [[str(p), "1" if increasing else "0"] for p in prices],
                    "a": [[str(p), "0" if increasing else "1"] for p in prices],
                }
                self.queue.append(raw)
                self.max_queue = max(self.max_queue, len(self.queue))
                assert len(self.queue) <= 128, (
                    "Synthetic receive queue exceeded real configured bound"
                )
        self.ms = until

    def sleep(self, seconds):
        self.advance(seconds * 1000)

    @contextmanager
    def connector(self, *args, **kwargs):
        assert self.queue is None
        self.queue = deque()

        def recv(timeout):
            if not self.queue:
                self.advance((self.tick + 1) * 100 - self.ms)
            self.received += 1
            return json.dumps(self.queue.popleft())

        try:
            yield SimpleNamespace(recv=recv)
        finally:
            self.queue = None

    def fetch(self, request):
        start = self.ms
        self.advance(self.config["latency_ms"])
        best = PRICE + position(self.path, self.tick)
        self.calls.append(
            {"started_ms": start, "finished_ms": self.ms, "update_id": 1000 + self.tick}
        )
        payload = {
            "lastUpdateId": 1000 + self.tick,
            "T": micros(BASE) // 1000 + self.tick * 100,
            "E": micros(BASE) // 1000 + self.tick * 100,
            "bids": [[str(best - n), "1"] for n in range(1000)],
            "asks": [[str(best + 1 + n), "1"] for n in range(1000)],
        }
        return httpx.Response(200, json=payload)

    def check_book(self, session):
        book = session.book
        if book is None or not book.synced:
            return
        self.min_known_levels = min(self.min_known_levels, len(book.bids), len(book.asks))
        if min(len(book.bids), len(book.asks)) < session.top:
            self.top_incomplete_events += 1
            return
        best = PRICE + position(self.path, book.last - 1000)
        expected_bids = [(best - n, 1) for n in range(session.top)]
        expected_asks = [(best + 1 + n, 1) for n in range(session.top)]
        view = book.view(session.top)
        assert view["bids"] == expected_bids and view["asks"] == expected_asks
        self.comparisons += 1


def run(output, scenario):
    output.mkdir(parents=True, exist_ok=False)
    market, store = Market(scenario), Store(output / "store")
    fetcher = binance.Fetcher(
        httpx.Client(transport=httpx.MockTransport(market.fetch)), retries=0, sleep=market.sleep
    )
    original_take, original_persist = depth.Session.take, live.persist_messages

    def take(session, row):
        original_take(session, row)
        market.check_book(session)

    def persist(*args):
        received = original_persist(*args)
        if received:
            market.advance(market.config["flush_ms"])
        return received

    capture_wall = time.perf_counter()
    capture_cpu = time.process_time()
    try:
        with ExitStack() as stack:
            for obj, attr, replacement in [
                (live.time, "monotonic", lambda: market.ms / 1000),
                (live.time, "sleep", market.sleep),
                (live, "now", lambda: BASE + timedelta(milliseconds=market.ms)),
                (binance, "now", lambda: BASE + timedelta(milliseconds=market.ms)),
                (depth, "now", lambda: BASE + timedelta(milliseconds=market.ms)),
                (depth.Session, "take", take),
                (live, "persist_messages", persist),
            ]:
                stack.enter_context(patch.object(obj, attr, replacement))
            report = live.capture(
                store,
                seconds=market.config["seconds"],
                verify_seconds=10,
                rotate_seconds=market.config.get("rotate_seconds", 900),
                flush_seconds=5,
                top=20,
                min_free_gib=0,
                connector=market.connector,
                fetcher=fetcher,
            )
        capture_cost = {
            "wall_seconds": time.perf_counter() - capture_wall,
            "cpu_seconds": time.process_time() - capture_cpu,
            "report_bytes": len(json_bytes(report)),
        }
        replay = depth.replay(store, report["id"])
        verification = verify(store, report["id"], replayed=replay)
        assert report["messages"] == report["persisted_messages"] == market.received
        assert verification["raw_to_canonical_equal"] and verification["replay_matches_live"]
        contexts = [
            json.loads(r[0])["refresh_context"]
            for r in store.db.execute("SELECT metadata FROM fetches")
            if "refresh_context" in json.loads(r[0])
        ]
        extra = [x for x in contexts if x["reason"] == "headroom"]
        if "refresh_decisions" in report:
            assert len(extra) == report["coverage_check_requests"]
            assert len(contexts) - len(extra) == report["periodic_check_requests"]
        max_extra = 0
        for entry in extra:
            window = [
                x
                for x in extra
                if 0 <= (utc(entry["requested_at"]) - utc(x["requested_at"])).total_seconds() < 60
            ]
            assert len(window) <= 12
            max_extra = max(max_extra, len(window))
        result = {
            "scenario": scenario,
            "parameters": market.config,
            "capture_cost": capture_cost,
            "state": report["state"],
            "messages": report["messages"],
            "connections": report["connections"],
            "coverage_resets": report["coverage_resets"],
            "top_shortfalls": report.get("coverage_top_shortfalls"),
            "extra_requests": report["coverage_check_requests"],
            "periodic_requests": report["periodic_check_requests"],
            "rest_verifications": {
                **{
                    key: replay[key]
                    for key in (
                        "verification_checks",
                        "verification_equal",
                        "verification_pending",
                        "verification_uncomparable",
                    )
                },
                "verification_mismatch": sum(
                    c["comparable"] and not c["equal"]
                    for epoch in replay["epochs"].values()
                    for c in epoch["checks"]
                ),
            },
            "unreconstructed_messages": report["unreconstructed_messages"],
            "rolling_extra_budget": {
                "maximum_total": max_extra,
                "verified_from_durable_request_contexts": "refresh_decisions" in report,
            },
            "all_rest_calls": market.calls,
            "exact_full_market_comparisons": market.comparisons,
            "minimum_known_levels": market.min_known_levels,
            "maximum_receive_queue": market.max_queue,
            "deferral_counts": report.get("refresh_decisions", {}).get("primary_reason_counts"),
            "verification": verification,
            "source_hashes": {
                str(Path(m.__file__)): hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                for m in (depth, depth_refresh, live)
            },
            "scope": "synthetic full market and simulated delays; no network or historical counterfactual responses; comparison does not establish production efficacy",
        }
        for name, data in [
            ("capture.json", report),
            ("verification.json", verification),
            ("request-contexts.json", contexts),
            ("result.json", result),
        ]:
            (output / name).write_bytes(json_bytes(data))
        print(
            json.dumps(
                {k: result[k] for k in ("scenario", "state", "coverage_resets", "extra_requests")}
            )
        )
        return result
    finally:
        fetcher.client.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    args = parser.parse_args()
    run(args.output.resolve(), args.scenario)


if __name__ == "__main__":
    main()
