"""Canonical Arrow schemas; timestamp is a label, not necessarily availability."""

import pyarrow as pa

from .domain import Key

TIME = pa.timestamp("us", tz="UTC")
VALUE = pa.decimal128(38, 18)
COMMON = [
    pa.field("instrument_id", pa.string(), False),
    pa.field("timestamp", TIME, False),
    pa.field("source_id", pa.string(), False),
    pa.field("source_row", pa.int64(), False),
    pa.field("first_observed_at", TIME, False),
    pa.field("source_published_at", TIME),
    pa.field("quality_flags", pa.list_(pa.string()), False),
]
OHLC = [pa.field(x, VALUE, False) for x in ("open", "high", "low", "close")]
VOLUMES = ["base_volume", "quote_volume", "taker_buy_base_volume", "taker_buy_quote_volume"]


def schema(key: Key):
    fields = list(COMMON)
    if key.dataset.endswith("_bars"):
        fields += [pa.field("bar_end", TIME, False), *OHLC]
        if key.dataset == "trade_bars":
            fields += [pa.field(x, VALUE, False) for x in VOLUMES]
            fields += [pa.field("trade_count", pa.int64(), False)]
    elif key.dataset == "funding_events":
        fields += [
            pa.field("funding_rate", VALUE, False),
            pa.field("event_type", pa.string(), False),
            pa.field("settlement_mark_price", VALUE),
        ]
    elif key.dataset == "open_interest":
        fields += [
            pa.field("quantity", VALUE, False),
            pa.field("notional_value", VALUE, False),
            pa.field("quantity_unit", pa.string(), False),
            pa.field("value_currency", pa.string(), False),
            pa.field("time_semantics", pa.string(), False),
            pa.field("reference_time", TIME),
        ]
    else:
        fields += [pa.field("metadata_json", pa.string(), False)]
    return pa.schema(fields, metadata={b"schema_version": b"1", b"dataset_key": key.id.encode()})


def row_key(row, key):
    return (row["timestamp"], row.get("event_type", ""))


def economic_row(row):
    return {
        k: v
        for k, v in row.items()
        if k
        not in {
            "source_id",
            "source_row",
            "first_observed_at",
            "source_published_at",
            "quality_flags",
        }
    }
