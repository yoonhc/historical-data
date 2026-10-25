"""Record individual OI HTTP observations without publishing or shifting OI values."""

from . import job_clock as jc

import fcntl
import json
import math
import time
from datetime import timedelta
from email.utils import parsedate_to_datetime
from uuid import uuid4

import httpx

from .binance import Fetcher, SourceError, rest_url
from .domain import DataError, Key, WriterBusy, json_bytes, micros, now, sha256, utc
from .normalize import normalize_rest
from .operations import floor_grid
from .store import atomic_write

REST_OI = Key("open_interest", interval="5m", series="rest")
PROBE_VERSION = 2


def parse_probe_rows(body, identity, received, start, end):
    batch = normalize_rest(body, REST_OI, identity, received)
    if batch.quarantine:
        raise DataError("Zero OI in observation; not usable as presence/absence evidence")
    if any(not start <= r["timestamp"] < end for r in batch.rows):
        raise DataError("OI response outside the explicit query interval")
    expected = int((end - start).total_seconds() // 300) + 1
    if expected >= 500 or len(batch.rows) > expected:
        raise DataError("OI query may be truncated by the page limit")
    return batch.rows


def clock_flags(started, received, elapsed, headers):
    flags = []
    if received < started or abs((received - started).total_seconds() - elapsed) > 2:
        flags.append("local_clock_discontinuity")
    # HTTP Date is only a coarse cross-check, not a source publication timestamp.
    if headers.get("date"):
        try:
            stamp = utc(parsedate_to_datetime(headers["date"]))
            if not started - timedelta(seconds=5) <= stamp <= received + timedelta(seconds=5):
                flags.append("http_date_outside_local_request_window")
        except (TypeError, ValueError):
            flags.append("invalid_http_date")
    return flags


def retry_delay(error, received):
    minimum = 3600 if error.status == 418 else 300 if error.status == 429 else 30
    value = error.headers.get("retry-after")
    if value:
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (utc(parsedate_to_datetime(value)) - received).total_seconds()
            except (TypeError, ValueError):
                seconds = minimum
        if math.isfinite(seconds) and seconds > minimum:
            return seconds
    return minimum


def probe_once(store, fetcher=None, *, clock=now, monotonic=time.monotonic):
    """No hidden retries; keep both successful empty bodies and failed requests.

    A separate probe lock spans HTTP; the canonical writer is acquired only to
    persist evidence. HTTP timing never includes waiting for the canonical writer.
    """
    with (store.root / "state/oi-probe.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WriterBusy("Another OI probe is running") from exc
        latest = store.root / "state/oi-probe-latest.json"
        observation = jc.active().point('oi_probe_target') if jc.active() else None
        if observation:
            observation.grid(300)
        current = observation.lower if observation else utc(clock())
        requested = observation.center if observation else current
        if latest.exists():
            previous = json.loads(latest.read_bytes())
            pending = previous.get('pending_clock_cooldown')
            if pending and observation:
                error = SourceError('retained cooldown', pending['status'], headers=pending['headers'])
                previous['cooldown_until'] = observation.upper + timedelta(seconds=retry_delay(error, observation.upper))
                previous.pop('pending_clock_cooldown')
                previous['cooldown_clock_observation'] = observation.evidence
                atomic_write(latest, json_bytes(previous))
            until = previous.get("cooldown_until")
            if until and current < utc(until):
                return {"state": "cooldown", "cooldown_until": until, "complete": False}
        target = floor_grid(current, 300)
        # Keep the target strictly INSIDE the API query, never exactly on its endTime.
        # Early v1 observations used an upper boundary whose inclusion was ambiguous.
        if micros(current) - micros(target) < 2000:
            return {"state": "boundary_wait", "complete": False}
        start, end = target - timedelta(hours=1), current
        url, weight = rest_url(REST_OI, start, end)
        own = fetcher is None
        client = httpx.Client(timeout=10, follow_redirects=False) if own else None
        fetcher = fetcher or Fetcher(client, retries=0, max_bytes=1_000_000)
        receipt = {
            "version": PROBE_VERSION,
            **({"clock_observation": observation.evidence} if observation else {}),
            "query_policy": "target_strictly_inside_request_time_end",
            "probe_id": uuid4().hex,
            "requested_at": requested,
            "query_start": start,
            "query_end": end,
            "target_label": target,
            "page_limit": 500,
        }
        tick = monotonic()
        try:
            response = fetcher.fetch(url, weight=weight)
            body, received, headers = response.body, utc(response.received_at), response.headers
            receipt.update(state="ok", status=200)
        except SourceError as exc:
            body, received, headers = exc.body, utc(clock()), exc.headers
            receipt.update(
                state="http_error" if exc.status else "network_error",
                status=exc.status,
                error=str(exc),
                cooldown_until=received + timedelta(seconds=retry_delay(exc, received)),
                **({'pending_clock_cooldown': {'status': exc.status, 'headers': dict(exc.headers)}} if jc.active() else {}),
            )
        finally:
            if client:
                client.close()
        elapsed = monotonic() - tick
        observation_failure = None
        receipt.update(received_at=received, elapsed_seconds=elapsed)
        receipt["clock_flags"] = clock_flags(requested, received, elapsed, headers)
        if jc.active():
            try:
                receipt['clock_at_receive'] = jc.active().point('oi_probe_receive').evidence
                receipt['utc_quality_known'] = True
            except (jc.ClockDeferred, jc.ClockFailure) as exc:
                receipt['utc_quality_known'] = False
                receipt['clock_flags'].append('current_clock_unavailable')
                receipt['clock_reason'] = str(exc)
                if isinstance(exc, jc.ClockFailure):
                    observation_failure = exc
                    receipt['clock_failure'] = True
        if receipt["state"] == "ok":
            try:
                rows = parse_probe_rows(body, sha256(body), received, start, end)
                receipt["rows"] = len(rows)
                receipt["target_present"] = any(r["timestamp"] == target for r in rows)
            except (DataError, ValueError, KeyError, TypeError) as exc:
                receipt.update(state="invalid_response", error=str(exc))
        receipt["complete"] = receipt["state"] == "ok" and not receipt["clock_flags"]
        with store.writer(timeout=60):
            source_id = store.put_raw(
                body,
                url,
                "oi_probe",
                received,
                {"oi_probe": receipt, "headers": dict(headers), "status": receipt["status"]},
            )
        result = {**receipt, "source_id": source_id, "url": url}
        atomic_write(latest, json_bytes(result))
        if observation_failure is not None:
            # Keep a received response and its server cooldown even if monitoring
            # failed. Propagate that failure after releasing the data writer.
            raise observation_failure
        return result
