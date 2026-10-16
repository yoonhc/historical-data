"""Capture-level recovery tests with a deterministic clock and public transports."""

import gzip
import json
import runpy
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from marketdata import binance, depth
from marketdata import depth_stream as live
from marketdata.binance import Fetcher, SourceError
from marketdata.domain import DataError, utc
from test_depth import BASE, snapshot, update


class Source:
    def __init__(self, monkeypatch, reply, *, silent_after_first=False):
        self.ms = 0
        self.latest = 100
        self.connection = 0
        self.calls = []
        self.messages = []
        self.reply = reply
        self.silent_after_first = silent_after_first
        monkeypatch.setattr(live.time, "monotonic", lambda: self.ms / 1000)
        monkeypatch.setattr(live.time, "sleep", self.sleep)
        for module in (live, depth, binance):
            monkeypatch.setattr(module, "now", lambda: BASE + timedelta(milliseconds=self.ms))
        self.fetcher = Fetcher(
            httpx.Client(transport=httpx.MockTransport(self.fetch)), retries=0, sleep=self.sleep
        )

    def sleep(self, seconds):
        self.ms += round(seconds * 1000)

    def fetch(self, request):
        assert request.url.params["limit"] == "1000"
        self.calls.append({"seconds": self.ms / 1000, "connection": self.connection})
        payload = self.reply(self)
        return payload if isinstance(payload, httpx.Response) else httpx.Response(200, json=payload)

    @contextmanager
    def connector(self, *args, **kwargs):
        self.connection += 1

        def recv(timeout):
            if self.silent_after_first and self.messages:
                self.sleep(timeout)
                raise TimeoutError
            self.ms += 100
            self.latest += 1
            message = json.dumps(update(self.latest))
            self.messages.append(message)
            return message

        yield SimpleNamespace(recv=recv)

    def capture(self, store, *, seconds=4, **kwargs):
        try:
            return live.capture(
                store,
                connector=self.connector,
                fetcher=self.fetcher,
                seconds=seconds,
                verify_seconds=3600,
                min_free_gib=0,
                top=2,
                **kwargs,
            )
        finally:
            self.fetcher.client.close()


def assert_preserved(store, source, report):
    messages = []
    for segment in store.db.execute("SELECT * FROM event_segments ORDER BY first_id"):
        for row in store._revision_table(segment).to_pylist():
            if row["kind"] == "update":
                payload = json.loads(gzip.decompress(store.raw(row["source_id"])[0]))
                messages.append(payload[row["source_row"]]["message"])
    assert messages == source.messages
    assert len(messages) == report["messages"] == report["persisted_messages"]
    assert report["state"] == "partial" and not report["complete"]
    replayed = depth.replay(store)
    assert replayed["replay_matches_live"] and not replayed["valid"]
    verify = runpy.run_path(str(Path(__file__).parents[1] / "scripts/verify_depth_capture.py"))[
        "verify"
    ]
    assert verify(store, report["id"], replayed=replayed)["raw_to_canonical_equal"]


def test_first_retry_recovers_on_same_socket_after_one_second_and_keeps_failed_epoch(
    store, monkeypatch
):
    source = Source(monkeypatch, lambda s: snapshot(90 if len(s.calls) == 1 else s.latest))
    report = source.capture(store)
    assert report["lagged_retry_policy"] == "same_socket_backoff_v1"
    assert report["connections"] == 1 and len(source.calls) == 2
    assert source.calls[1]["seconds"] - source.calls[0]["seconds"] == pytest.approx(1)
    assert report["lagged_retry_requests"] == report["lagged_snapshots"] == 1
    first, second = report["epochs"]
    assert first["end_reason"] == "lagged_snapshot" and first["final"]["error"]
    assert first["lagged_retry"]["actual_wait_seconds"] == pytest.approx(1)
    assert second["retry_of_epoch"] == 0 and second["final"]["book"]["synced"]
    assert report["unreconstructed_messages"] == 11
    assert_preserved(store, source, report)


def test_persistent_lag_increases_wait_and_caps_it_without_reconnecting_or_losing_raw(
    store, monkeypatch
):
    source = Source(monkeypatch, lambda s: snapshot(90))
    report = source.capture(store, seconds=70)
    attempted = [
        e["lagged_retry"] for e in report["epochs"] if e.get("lagged_retry", {}).get("requested_at")
    ]
    assert [e["delay_seconds"] for e in attempted] == [1, 2, 4, 8, 10, 10, 10, 10, 10]
    assert all(
        e["delay_seconds"] <= e["actual_wait_seconds"] < e["delay_seconds"] + 0.11
        for e in attempted
    )
    assert report["connections"] == 1 and report["lagged_retry_requests"] == 9
    assert report["unreconstructed_messages"] == report["messages"]
    assert all(not e["final"]["book"]["synced"] for e in report["epochs"])
    assert_preserved(store, source, report)


def test_rolling_budget_defers_retry_while_updates_continue_to_be_saved(store, monkeypatch):
    monkeypatch.setattr(live, "LAGGED_RETRY_REQUESTS", 2)
    source = Source(monkeypatch, lambda s: snapshot(90))
    report = source.capture(store, seconds=64)
    retries = [c["seconds"] for c in source.calls[1:]]
    assert retries == pytest.approx([1.1, 3.2, 61.1])
    assert all(sum(t - 60 < candidate <= t for candidate in retries) <= 2 for t in retries)
    assert report["epochs"][2]["lagged_retry"]["budget_delay_seconds"] > 50
    assert report["messages"] == 640 and report["connections"] == 1
    assert_preserved(store, source, report)


def test_budget_survives_recovery_and_planned_new_connections(store, monkeypatch):
    monkeypatch.setattr(live, "LAGGED_RETRY_REQUESTS", 1)

    def reply(source):
        calls = sum(c["connection"] == source.connection for c in source.calls)
        return snapshot(90 if calls == 1 else source.latest)

    source = Source(monkeypatch, reply)
    report = source.capture(store, seconds=25, rotate_seconds=10)
    assert report["connections"] == 3 and report["lagged_retry_requests"] == 1
    assert len(source.calls) == 4
    assert report["epochs"][1]["final"]["book"]["synced"]
    assert report["epochs"][2]["lagged_retry"]["consecutive_failure"] == 1
    assert report["epochs"][2]["lagged_retry"]["budget_delay_seconds"] > 40
    assert_preserved(store, source, report)


def test_retry_clock_runs_even_if_no_further_frame_arrives(store, monkeypatch):
    source = Source(
        monkeypatch,
        lambda s: snapshot(90 if len(s.calls) == 1 else s.latest),
        silent_after_first=True,
    )
    report = source.capture(store)
    assert len(source.calls) == 2 and source.calls[1]["seconds"] == pytest.approx(1.1)
    assert report["messages"] == report["unreconstructed_messages"] == 1
    assert report["epochs"][-1]["final"]["book"]["synced"]
    assert_preserved(store, source, report)


def test_expiry_during_retry_flush_never_starts_another_anchor_or_empty_epoch(store, monkeypatch):
    source = Source(monkeypatch, lambda s: snapshot(90))
    original = live.persist_messages

    def slow_flush(*args):
        count = original(*args)
        if source.ms >= 1100 and count:
            source.ms += 2000
        return count

    monkeypatch.setattr(live, "persist_messages", slow_flush)
    report = source.capture(store, seconds=2)
    assert len(source.calls) == len(report["epochs"]) == 1
    assert report["lagged_retry_requests"] == 0
    assert report["epochs"][0]["end_reason"] == "duration"
    assert_preserved(store, source, report)


def test_retry_due_at_connection_rotation_is_not_started_on_the_old_socket(store, monkeypatch):
    def reply(source):
        if len(source.calls) == 1:
            source.ms += 8900
            return snapshot(90)
        return snapshot(source.latest)

    source = Source(monkeypatch, reply)
    report = source.capture(store, seconds=12, rotate_seconds=10)
    assert report["connections"] == len(source.calls) == 2
    assert report["lagged_retry_requests"] == 0
    assert report["epochs"][1]["reason"] == "planned_rotation"
    assert_preserved(store, source, report)


@pytest.mark.parametrize("status,minimum", [(429, 300), (418, 3600)])
def test_http_rate_limit_during_retry_preserves_raw_and_blocks_following_rest(
    store, monkeypatch, status, minimum
):
    source = Source(
        monkeypatch,
        lambda s: snapshot(90) if len(s.calls) == 1 else httpx.Response(status, content=b"limited"),
    )
    with pytest.raises(SourceError):
        source.capture(store)
    report = json.loads((store.root / "state/depth-latest.json").read_bytes())
    assert report["state"] == "failed" and report["lagged_retry_requests"] == 1
    assert report["messages"] == report["persisted_messages"] == len(source.messages)
    assert report["epochs"][0]["final"]["error"] and len(source.calls) == 2
    cooldown = json.loads((store.root / "state/depth-rest-cooldown.json").read_bytes())
    assert utc(cooldown["until"]) >= BASE + timedelta(milliseconds=source.ms, seconds=minimum)
    with pytest.raises(DataError, match="REST cooldown"):
        live.snapshot(store, source.fetcher, report["id"], 2, 100, "anchor")
    assert len(source.calls) == 2


def test_interrupt_during_retry_keeps_failed_epoch_and_received_updates(store, monkeypatch):
    def reply(source):
        if len(source.calls) == 1:
            return snapshot(90)
        raise KeyboardInterrupt

    source = Source(monkeypatch, reply)
    with pytest.raises(KeyboardInterrupt):
        source.capture(store)
    report = json.loads((store.root / "state/depth-latest.json").read_bytes())
    assert report["state"] == "interrupted" and not report["complete"]
    assert report["messages"] == report["persisted_messages"] == len(source.messages)
    assert report["epochs"][0]["final"]["error"] and report["lagged_retry_requests"] == 1
    assert len(source.calls) == 2


def test_legacy_journal_replay_does_not_depend_on_retry_timing_metadata(store, monkeypatch):
    source = Source(monkeypatch, lambda s: snapshot(90 if len(s.calls) == 1 else s.latest))
    report = source.capture(store)
    before = depth.replay(store)
    for key in list(report):
        if key.startswith("lagged_retry_"):
            report.pop(key)
    for epoch in report["epochs"]:
        epoch.pop("lagged_retry", None)
        epoch.pop("retry_of_epoch", None)
    depth.save_collection(store, report, "partial")
    assert depth.replay(store) == before
