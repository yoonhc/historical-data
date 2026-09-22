"""Normalize bytes without modifying raw inputs or guessing OI clock offsets."""

import csv
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import timedelta

from .domain import DataError, Key, from_epoch, micros, number, utc
from .schema import row_key


@dataclass
class Batch:
    rows: list[dict]
    issues: list[dict] = field(default_factory=list)
    quarantine: bool = False
    query_complete: bool = True


def archive_csv(blob, member=None):
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        files = [m for m in zf.infolist() if not m.is_dir()]
        if len(files) != 1 or files[0].file_size > 128_000_000:
            raise DataError("Expected one bounded CSV member")
        if member and files[0].filename != member:
            raise DataError("Archive member does not match requested source")
        return list(csv.reader(io.StringIO(zf.read(files[0]).decode("utf-8-sig"))))


def common(key, ts, source_id, row_no, observed):
    return {
        "instrument_id": key.instrument_id,
        "timestamp": ts,
        "source_id": source_id,
        "source_row": row_no,
        "first_observed_at": observed,
        "source_published_at": None,
        "quality_flags": [],
    }


def validate_order(batch, key):
    keys = [row_key(r, key) for r in batch.rows]
    if len(keys) != len(set(keys)):
        raise DataError("Duplicate key inside one source; cannot silently deduplicate")
    count = sum(b < a for a, b in zip(keys, keys[1:]))
    if count:
        batch.issues.append({"kind": "source_order_reversed", "adjacent_count": count})
        for row in batch.rows:
            row["quality_flags"].append("source_order_reversed")
    batch.rows.sort(key=lambda r: row_key(r, key))
    return batch


def normalize_archive(blob, key: Key, source_id, observed, member=None):
    raw = archive_csv(blob, member)
    batch = Batch([])
    if key.dataset == "open_interest":
        if key.series != "archive" or not raw:
            raise DataError("Metrics require the archive OI series")
        header, *data = raw
        required = {"create_time", "symbol", "sum_open_interest", "sum_open_interest_value"}
        if not required <= set(header) or len(header) != len(set(header)):
            raise DataError("Invalid metrics header")
        for ordinal, values in enumerate(data, 2):
            if len(values) != len(header):
                raise DataError("Metrics row/header length mismatch")
            raw_row = dict(zip(header, values))
            if raw_row["symbol"] != key.symbol:
                raise DataError("Wrong metrics symbol")
            ts = utc(raw_row["create_time"])
            if micros(ts) % 300_000_000:
                raise DataError("Off-grid OI label")
            row = common(key, ts, source_id, ordinal, observed)
            row.update(
                quantity=number(raw_row["sum_open_interest"], nonnegative=True),
                notional_value=number(raw_row["sum_open_interest_value"], nonnegative=True),
                quantity_unit="source_units_unverified",
                value_currency="source_value_units_unverified",
                time_semantics="archive_label_unverified",
                reference_time=None,
            )
            row["quality_flags"].extend(["time_semantics_unverified", "units_unverified"])
            if row["quantity"] == 0 or row["notional_value"] == 0:
                batch.quarantine = True
                batch.issues.append({"kind": "zero_oi", "source_row": ordinal, "timestamp": ts})
            batch.rows.append(row)
    elif key.dataset.endswith("_bars"):
        # Spot archives before 2025 use ms; later use us. REST always requests ms.
        first_data = 1 if raw and raw[0][0] in {"open_time", "Open time"} else 0
        for ordinal, values in enumerate(raw[first_data:], first_data + 1):
            if len(values) != 12:
                raise DataError("Expected 12 kline fields")
            epoch_value = int(values[0])
            unit = "us" if key.market == "spot" and epoch_value >= 1_000_000_000_000_000 else "ms"
            ts = from_epoch(values[0], unit)
            if key.market == "spot" and (ts >= utc("2025-01-01")) != (unit == "us"):
                raise DataError("Spot archive unit does not match period contract")
            batch.rows.append(bar_row(values, key, source_id, ordinal, observed, unit))
    elif key.dataset == "funding_events":
        if not raw:
            return batch
        header, *data = raw
        for ordinal, values in enumerate(data, 2):
            r = dict(zip(header, values))
            ts = from_epoch(r["calc_time"])
            row = common(key, ts, source_id, ordinal, observed)
            row.update(
                funding_rate=number(r["last_funding_rate"]),
                event_type="Regular",
                settlement_mark_price=None,
            )
            batch.rows.append(row)
    else:
        raise DataError("No archive parser for this dataset")
    return validate_order(batch, key)


def bar_row(values, key, source_id, ordinal, observed, unit="ms"):
    ts = from_epoch(values[0], unit)
    if micros(ts) % (key.step * 1_000_000):
        raise DataError("Off-grid candle")
    end = ts + timedelta(seconds=key.step)
    tick = timedelta(microseconds=1 if unit == "us" else 1000)
    if from_epoch(values[6], unit) != end - tick:
        raise DataError("Unexpected candle close time")
    row = common(key, ts, source_id, ordinal, observed)
    row.update(bar_end=end)
    row.update(
        {k: number(v, positive=True) for k, v in zip(("open", "high", "low", "close"), values[1:5])}
    )
    if not (
        row["low"]
        <= min(row["open"], row["close"])
        <= max(row["open"], row["close"])
        <= row["high"]
    ):
        raise DataError("Invalid OHLC relationship")
    if key.dataset == "trade_bars":
        row.update(
            base_volume=number(values[5], nonnegative=True),
            quote_volume=number(values[7], nonnegative=True),
            taker_buy_base_volume=number(values[9], nonnegative=True),
            taker_buy_quote_volume=number(values[10], nonnegative=True),
        )
        count = number(values[8], nonnegative=True)
        if count != int(count):
            raise DataError("Fractional trade count")
        row["trade_count"] = int(count)
        if (
            row["taker_buy_base_volume"] > row["base_volume"]
            or row["taker_buy_quote_volume"] > row["quote_volume"]
        ):
            raise DataError("Taker buy volume exceeds total")
    return row


def normalize_rest(blob, key: Key, source_id, observed):
    incoming = json.loads(blob)
    batch = Batch([])
    if key.dataset == "instrument_metadata":
        symbols = [r for r in incoming["symbols"] if r["symbol"] == key.symbol]
        if len(symbols) != 1:
            raise DataError("Instrument missing from exchange metadata")
        row = common(key, observed, source_id, 0, observed)
        row["metadata_json"] = json.dumps(symbols[0], sort_keys=True, separators=(",", ":"))
        return Batch([row])
    if not isinstance(incoming, list):
        raise DataError("Expected JSON list")
    for ordinal, raw in enumerate(incoming):
        if key.dataset.endswith("_bars"):
            row = bar_row(raw, key, source_id, ordinal, observed)
        elif key.dataset == "funding_events":
            if raw["symbol"] != key.symbol:
                raise DataError("Wrong funding symbol")
            row = common(key, from_epoch(raw["fundingTime"]), source_id, ordinal, observed)
            mark = raw.get("markPrice")
            row.update(
                funding_rate=number(raw["fundingRate"]),
                event_type=raw.get("rateType", "Regular"),
                settlement_mark_price=number(mark, positive=True) if mark else None,
            )
        elif key.dataset == "open_interest":
            if key.series != "rest" or raw["symbol"] != key.symbol:
                raise DataError("Wrong OI series/symbol")
            ts = from_epoch(raw["timestamp"])
            if micros(ts) % 300_000_000:
                raise DataError("Off-grid REST OI")
            row = common(key, ts, source_id, ordinal, observed)
            row.update(
                quantity=number(raw["sumOpenInterest"], nonnegative=True),
                notional_value=number(raw["sumOpenInterestValue"], nonnegative=True),
                quantity_unit="source_units_unverified",
                value_currency="source_value_units_unverified",
                time_semantics="rest_period_end",
                reference_time=ts,
            )
            row["quality_flags"].append("units_unverified")
            if row["quantity"] == 0 or row["notional_value"] == 0:
                batch.quarantine = True
                batch.issues.append({"kind": "zero_oi", "source_row": ordinal, "timestamp": ts})
        else:
            raise DataError("Unsupported REST dataset")
        batch.rows.append(row)
    return validate_order(batch, key)
