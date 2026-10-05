import json
from datetime import timedelta

import pytest
from marketdata import aggtrade_stream, depth_stream, stream_guard
from marketdata.domain import DataError, now
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response


@pytest.mark.parametrize("module,name", [(aggtrade_stream, "aggtrade"), (depth_stream, "depth")])
@pytest.mark.parametrize("code,delay", [(429, 300), (418, 3600)])
def test_rate_limit_survives_process_restart_without_network(
    store, monkeypatch, module, name, code, delay
):
    current = now()
    monkeypatch.setattr(stream_guard, "now", lambda: current)
    calls = []

    def connector(*args, **kwargs):
        calls.append(True)
        raise InvalidStatus(Response(code, "limited", Headers(), bytearray()))

    with pytest.raises(DataError, match="cooldown"):
        module.capture(store, connector=connector, seconds=1, min_free_gib=0)
    report = json.loads((store.root / f"state/{name}-ws-cooldown.json").read_bytes())
    assert report["until"] == (current + timedelta(seconds=delay)).isoformat()
    count = store.db.execute("SELECT count(*) FROM event_collections").fetchone()[0]
    with pytest.raises(DataError, match="cooldown"):
        module.capture(store, connector=connector, seconds=1, min_free_gib=0)
    assert len(calls) == 1
    assert store.db.execute("SELECT count(*) FROM event_collections").fetchone()[0] == count
    monkeypatch.setattr(stream_guard, "now", lambda: current + timedelta(seconds=delay))
    stream_guard.check_cooldown(store, name)


def test_websocket_retry_after_can_extend_minimum_delay(store, monkeypatch):
    current = now()
    monkeypatch.setattr(stream_guard, "now", lambda: current)
    response = Response(429, "limited", Headers({"Retry-After": "7200"}), bytearray())
    with pytest.raises(DataError):
        stream_guard.rate_limited(store, "aggtrade", response)
    report = json.loads((store.root / "state/aggtrade-ws-cooldown.json").read_bytes())
    assert report["until"] == (current + timedelta(hours=2)).isoformat()
