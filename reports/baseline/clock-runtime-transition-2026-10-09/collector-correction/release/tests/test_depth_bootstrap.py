import gzip
import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from marketdata import depth
from marketdata import depth_stream as live
from marketdata.binance import Fetcher, SourceError
from marketdata.domain import utc
from test_depth import BASE, capture, connector_factory, fetcher_for, snapshot, update


def receipts(store):
    records = list(store.db.execute("SELECT id FROM artifacts WHERE kind='depth_bootstrap'"))
    return [json.loads(gzip.decompress(store.raw(r["id"])[0])) for r in records]


def test_rest_request_follows_received_updates_and_bootstrap_preserves_receipt_times(
    store, monkeypatch
):
    clock = SimpleNamespace(value=0.0, latest=99)
    monkeypatch.setattr(live.time, "monotonic", lambda: clock.value)
    monkeypatch.setattr(live, "now", lambda: BASE + timedelta(seconds=clock.value))

    @contextmanager
    def connector(*args, **kwargs):
        def recv(timeout):
            clock.value += 0.1
            clock.latest += 1
            return json.dumps(update(clock.latest))

        yield SimpleNamespace(recv=recv)

    calls = []

    def rest(request):
        # Before buffering, this source would return 99 and the first WS U would
        # be 100, so the strict futures bridge would fail. Do not relax that rule.
        calls.append(clock.latest)
        return httpx.Response(200, json=snapshot(clock.latest))

    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(rest)), retries=0)
    try:
        report = live.capture(
            store,
            connector=connector,
            fetcher=fetcher,
            seconds=4,
            verify_seconds=3600,
            min_free_gib=0,
            top=2,
        )
    finally:
        fetcher.close()
    assert calls[0] >= 100 and len(calls) == 1
    assert report["complete"] and report["sync_losses"] == 0
    assert report["bootstrap_messages"] == 1
    assert report["messages"] == report["persisted_messages"]
    replay = depth.replay(store)
    assert replay["valid"] and replay["replay_matches_live"]
    prefix = receipts(store)[0]
    rows = [
        r
        for seg in store.db.execute("SELECT * FROM event_segments ORDER BY first_id")
        for r in store._revision_table(seg).to_pylist()
        if r["kind"] == "update"
    ]
    assert len(prefix) == report["bootstrap_messages"]
    for original, canonical in zip(prefix, rows, strict=False):
        assert canonical["received_at"] == utc(original["received_at"])
        assert canonical["final_update_id"] == json.loads(original["message"])["u"]
    ids = [r["final_update_id"] for r in rows]
    assert ids == list(range(100, 100 + len(ids)))


def test_bootstrap_raw_survives_rest_failure_before_anchor_publication(store, monkeypatch):
    connector = connector_factory(monkeypatch, [[update()]])
    fetcher, calls = fetcher_for([httpx.Response(429, content=b"limited")])
    try:
        with pytest.raises(SourceError):
            live.capture(store, seconds=9, connector=connector, fetcher=fetcher, min_free_gib=0)
    finally:
        fetcher.close()
    assert len(calls) == 1
    assert json.loads(receipts(store)[0][0]["message"]) == update()
    assert depth.status(store)["state"] == "failed"
    assert not store.db.execute("SELECT * FROM event_segments").fetchone()


def test_ctrl_c_during_rest_retains_prefix_without_claiming_a_complete_book(store, monkeypatch):
    connector = connector_factory(monkeypatch, [[update()]], step=0.1)
    calls = []

    def interrupted(request):
        calls.append(str(request.url))
        raise KeyboardInterrupt()

    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(interrupted)), retries=0)
    try:
        with pytest.raises(KeyboardInterrupt):
            live.capture(store, seconds=9, connector=connector, fetcher=fetcher, min_free_gib=0)
    finally:
        fetcher.close()
    assert len(calls) == 1 and len(receipts(store)[0]) == 1
    assert depth.status(store)["state"] == "interrupted"
    assert not depth.status(store)["healthy"]


@pytest.mark.parametrize("message", ["small", b"a binary frame"])
def test_bootstrap_reads_one_event_without_waiting_and_leaves_later_events_queued(
    store, monkeypatch, message
):
    monkeypatch.setattr(live.time, "monotonic", lambda: 0)
    queue = iter([message, "next frame"])
    socket = SimpleNamespace(recv=lambda timeout: next(queue))
    result = live.buffer_before_anchor(store, socket, "test", 0, 10)
    assert len(result) == len(receipts(store)[0]) == 1
    assert socket.recv(timeout=0) == "next frame"
    expected = message.decode() if isinstance(message, bytes) else message
    assert result[0]["message"] == expected


def test_capture_drains_received_prefix_when_duration_expires_during_bootstrap(store, monkeypatch):
    report = capture(store, monkeypatch, [[update()]], [snapshot()], seconds=1)
    assert report["messages"] == report["persisted_messages"] == 1
    assert depth.replay(store)["replay_matches_live"]


def test_silent_bootstrap_has_a_deadline_and_never_requests_an_anchor(store, monkeypatch):
    clock = SimpleNamespace(value=0)
    monkeypatch.setattr(live.time, "monotonic", lambda: clock.value)

    def recv(timeout):
        clock.value += timeout
        raise TimeoutError()

    with pytest.raises(OSError, match="No depth messages"):
        live.buffer_before_anchor(store, SimpleNamespace(recv=recv), "test", 0, 10)
    assert clock.value == 2 and not receipts(store)
