"""Persist WebSocket rate-limit cooldowns across service restarts."""

import json
from datetime import timedelta

from .binance import SourceError
from .domain import DataError, json_bytes, now, utc
from .oi_probe import retry_delay
from .store import atomic_write


def check_cooldown(store, name):
    path = store.root / f"state/{name}-ws-cooldown.json"
    if path.exists():
        report = json.loads(path.read_bytes())
        if now() < utc(report["until"]):
            raise DataError(f"{name} WebSocket cooldown until {report['until']}")


def rate_limited(store, name, response):
    received = now()
    error = SourceError(
        "WebSocket handshake rate limited",
        status=response.status_code,
        headers={k.lower(): v for k, v in response.headers.raw_items()},
    )
    report = {
        "received_at": received,
        "status": error.status,
        "headers": error.headers,
        "until": received + timedelta(seconds=retry_delay(error, received)),
    }
    atomic_write(store.root / f"state/{name}-ws-cooldown.json", json_bytes(report))
    raise DataError(f"{name} WebSocket cooldown until {report['until']}")
