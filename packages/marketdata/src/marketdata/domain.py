"""Market identities, exact values and UTC time contracts (no platform dependencies)."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
DAY = timedelta(days=1)
INTERVALS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
DATASETS = {
    "trade_bars",
    "mark_price_bars",
    "index_price_bars",
    "funding_events",
    "open_interest",
    "instrument_metadata",
}
PARSER_VERSION = "1"
POLICY_VERSION = "1"


class DataError(ValueError):
    """Input or stored data failed a declared contract."""


class ConflictError(DataError):
    pass


def utc(value: str | datetime) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def now() -> datetime:
    return datetime.now(UTC)


def micros(value: datetime) -> int:
    return (utc(value) - EPOCH) // timedelta(microseconds=1)


def from_epoch(value, unit="ms") -> datetime:
    n = Decimal(str(value))
    if not n.is_finite() or n != n.to_integral_value():
        raise DataError(f"Invalid epoch: {value}")
    return EPOCH + timedelta(microseconds=int(n) * (1000 if unit == "ms" else 1))


def number(value, *, nonnegative=False, positive=False) -> Decimal:
    try:
        d = Decimal(str(value))
        if not d.is_finite() or (positive and d <= 0) or (nonnegative and d < 0):
            raise DataError(f"Invalid numeric value: {value}")
        with localcontext() as ctx:
            ctx.prec = 80
            fixed = d.quantize(Decimal("1e-18"))
            if fixed != d or abs(d) >= Decimal("1e20"):
                raise DataError(f"Value outside decimal(38,18): {value}")
        return fixed
    except InvalidOperation as exc:
        raise DataError(f"Invalid decimal: {value}") from exc


def json_default(value):
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def json_bytes(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=json_default, ensure_ascii=False
    ).encode()


def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class Key:
    dataset: str
    market: str = "linear_perpetual"
    symbol: str = "BTCUSDT"
    interval: str = "1m"
    series: str = "primary"

    def __post_init__(self):
        if self.dataset not in DATASETS or self.market not in {"spot", "linear_perpetual"}:
            raise DataError("Unsupported dataset or market")
        if self.symbol != "BTCUSDT":
            raise DataError(
                "Version 1 supports BTCUSDT only; units must be verified before extension"
            )
        if self.market == "spot" and self.dataset not in {"trade_bars", "instrument_metadata"}:
            raise DataError("This dataset requires the futures market")
        if self.dataset.endswith("_bars"):
            if self.interval not in INTERVALS or self.series != "primary":
                raise DataError("Invalid bar interval/series")
        elif self.dataset == "open_interest":
            if self.interval != "5m" or self.series not in {"archive", "rest"}:
                raise DataError("OI requires explicit archive/rest series and 5m period")
        elif self.interval != "event" or self.series != "primary":
            raise DataError("Event datasets require interval=event, series=primary")

    @property
    def instrument_id(self):
        return f"binance:{self.market}:{self.symbol}"

    @property
    def id(self):
        return ":".join((self.instrument_id, self.dataset, self.interval, self.series))

    @property
    def step(self):
        return INTERVALS.get(self.interval)

    def asdict(self):
        return asdict(self)


def day_ranges(start, end):
    start, end = utc(start), utc(end)
    if start >= end:
        raise DataError("Expected start < end")
    cursor = start
    while cursor < end:
        boundary = cursor.replace(hour=0, minute=0, second=0, microsecond=0) + DAY
        stop = min(end, boundary)
        yield cursor, stop
        cursor = stop


def full_days(start, end):
    start, end = utc(start), utc(end)
    if any((d.hour, d.minute, d.second, d.microsecond) != (0, 0, 0, 0) for d in (start, end)):
        raise DataError("Archive collection requires UTC midnight boundaries")
    return day_ranges(start, end)
