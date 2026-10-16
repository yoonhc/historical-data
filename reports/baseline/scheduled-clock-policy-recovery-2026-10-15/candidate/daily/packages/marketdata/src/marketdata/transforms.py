"""Consumer-independent candle aggregation; missing input is never synthesized."""

from datetime import timedelta
from decimal import Decimal, localcontext

import pyarrow as pa

from .domain import INTERVALS, DataError, Key, json_bytes, micros, sha256
from .schema import VOLUMES, schema


def resample(table, key: Key, interval: str):
    if not key.dataset.endswith("_bars"):
        raise DataError("Only price bars can be resampled")
    target_step = INTERVALS.get(interval)
    if not target_step or target_step < key.step or target_step % key.step:
        raise DataError("Target interval must be a multiple of the input interval")
    target = Key(key.dataset, key.market, key.symbol, interval, key.series)
    if key.interval == interval:
        return table
    groups = {}
    for row in table.to_pylist():
        bucket = micros(row["timestamp"]) // (target_step * 1_000_000)
        groups.setdefault(bucket, []).append(row)
    output = []
    for items in groups.values():
        items.sort(key=lambda r: r["timestamp"])
        first, last = items[0], items[-1]
        expected = target_step // key.step
        if (
            len(items) != expected
            or micros(first["timestamp"]) % (target_step * 1_000_000)
            or any(
                b["timestamp"] - a["timestamp"] != timedelta(seconds=key.step)
                for a, b in zip(items, items[1:])
            )
        ):
            raise DataError("Incomplete input candle group; resampling would hide a gap")
        row = dict(first)
        row.update(
            bar_end=last["bar_end"],
            open=first["open"],
            close=last["close"],
            high=max(r["high"] for r in items),
            low=min(r["low"] for r in items),
            first_observed_at=max(r["first_observed_at"] for r in items),
            source_published_at=None,
            source_row=-1,
            source_id="derived:"
            + sha256(
                json_bytes(
                    {
                        "operation": "ohlcv_resample_v1",
                        "interval": interval,
                        "inputs": [(r["source_id"], r["source_row"]) for r in items],
                    }
                )
            ),
            quality_flags=sorted({flag for r in items for flag in r["quality_flags"]}),
        )
        if key.dataset == "trade_bars":
            with localcontext() as ctx:
                ctx.prec = 80
                for name in VOLUMES:
                    row[name] = sum((r[name] for r in items), Decimal(0))
            row["trade_count"] = sum(r["trade_count"] for r in items)
        output.append(row)
    return pa.Table.from_pylist(output, schema=schema(target))
