"""Bounded public transport and Binance-specific source conventions."""

import time
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

import httpx

from .domain import DataError, Key, now, utc

VISION = "https://data.binance.vision/data"
SPOT = "https://api.binance.com"
FUTURES = "https://fapi.binance.com"


class SourceError(RuntimeError):
    def __init__(self, message, status=None, body=b"", url=""):
        super().__init__(message)
        self.status, self.body, self.url = status, body, url


@dataclass
class Response:
    body: bytes
    url: str
    received_at: object
    headers: dict


class Fetcher:
    def __init__(self, client=None, retries=3, max_bytes=64_000_000, sleep=time.sleep):
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_client = client is None
        self.retries = retries
        self.max_bytes = max_bytes
        self.sleep = sleep
        self.windows = {}
        self.requests = 0
        self.bytes = 0

    def close(self):
        if self.owns_client:
            self.client.close()

    def fetch(self, url, weight=1):
        host = httpx.URL(url).host
        for attempt in range(self.retries + 1):
            window = self.windows.setdefault(host, deque())
            current = time.monotonic()
            while window and window[0][0] <= current - 60:
                window.popleft()
            # Conservative shared weight budget for the single-writer collector.
            if sum(w for _, w in window) + weight > 1000:
                self.sleep(max(0, 60 - (current - window[0][0])))
                window.clear()
            if window:
                self.sleep(max(0, 0.1 - (time.monotonic() - window[-1][0])))
            window.append((time.monotonic(), weight))
            self.requests += 1
            try:
                with self.client.stream("GET", url) as response:
                    chunks, length = [], 0
                    for chunk in response.iter_bytes():
                        length += len(chunk)
                        if length > self.max_bytes:
                            raise SourceError(
                                "Response size limit exceeded", response.status_code, url=url
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    self.bytes += len(body)
                    if response.status_code == 200:
                        return Response(body, url, now(), dict(response.headers))
                    error = SourceError(
                        f"HTTP {response.status_code}: {url}", response.status_code, body, url
                    )
                    if response.status_code not in {429, 500, 502, 503, 504}:
                        raise error
                    if attempt == self.retries:
                        raise error
                    retry = response.headers.get("retry-after")
                    try:
                        delay = float(retry) if retry else 2**attempt
                    except ValueError:
                        delay = (parsedate_to_datetime(retry) - now()).total_seconds()
                    if delay > 60:
                        raise SourceError(
                            f"Source requests cooldown {delay}s; retry in next run",
                            response.status_code,
                            body,
                            url,
                        )
                    self.sleep(max(0, delay))
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise SourceError(f"Network error: {url}: {exc}", url=url) from exc
                self.sleep(2**attempt)
        raise AssertionError("Unreachable")


def archive_url(key: Key, start, monthly=False):
    start = utc(start)
    market = "spot" if key.market == "spot" else "futures/um"
    period = "monthly" if monthly else "daily"
    date = start.strftime("%Y-%m" if monthly else "%Y-%m-%d")
    category = {
        "trade_bars": "klines",
        "mark_price_bars": "markPriceKlines",
        "index_price_bars": "indexPriceKlines",
        "open_interest": "metrics",
        "funding_events": "fundingRate",
    }.get(key.dataset)
    if category is None:
        raise DataError("No archive source for this dataset")
    if key.dataset == "open_interest" and (monthly or key.series != "archive"):
        raise DataError("OI archive supports daily archive series only")
    if key.dataset.endswith("_bars"):
        suffix = f"{category}/{key.symbol}/{key.interval}/{key.symbol}-{key.interval}-{date}.zip"
    else:
        suffix = f"{category}/{key.symbol}/{key.symbol}-{category}-{date}.zip"
    return f"{VISION}/{market}/{period}/{suffix}"


def next_month(value):
    value = utc(value)
    return value.replace(
        year=value.year + (value.month == 12),
        month=1 if value.month == 12 else value.month + 1,
        day=1,
    )


def archive_units(key, start, end, monthly=True):
    start, end = utc(start), utc(end)
    while start < end:
        month_end = next_month(start)
        use_month = (
            monthly and key.dataset != "open_interest" and start.day == 1 and month_end <= end
        )
        stop = month_end if use_month else start + timedelta(days=1)
        yield start, min(stop, end), use_month
        start = stop


def rest_url(key: Key, start=None, end=None):
    base = SPOT if key.market == "spot" else FUTURES
    prefix = "/api/v3" if key.market == "spot" else "/fapi/v1"
    if key.dataset == "instrument_metadata":
        return base + prefix + "/exchangeInfo", 20
    params = {"symbol": key.symbol}
    if key.dataset.endswith("_bars"):
        endpoint = {
            "trade_bars": "klines",
            "mark_price_bars": "markPriceKlines",
            "index_price_bars": "indexPriceKlines",
        }[key.dataset]
        if key.dataset == "index_price_bars":
            params = {"pair": key.symbol}
        params.update(interval=key.interval, limit=1000)
        url = base + prefix + "/" + endpoint
        weight = 5
    elif key.dataset == "funding_events":
        params["limit"] = 1000
        url, weight = base + "/fapi/v1/fundingRate", 1
    elif key.dataset == "open_interest" and key.series == "rest":
        params.update(period="5m", limit=500)
        url, weight = base + "/futures/data/openInterestHist", 1
    else:
        raise DataError("Invalid REST dataset/series")
    params.update(
        startTime=int(utc(start).timestamp() * 1000), endTime=int(utc(end).timestamp() * 1000) - 1
    )
    return url + "?" + urlencode(params), weight
