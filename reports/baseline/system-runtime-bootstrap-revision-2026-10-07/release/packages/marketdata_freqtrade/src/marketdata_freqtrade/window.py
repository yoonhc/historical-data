"""Bounded-memory exports of explicit, aligned windows of immutable snapshots."""

import hashlib
import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from marketdata.domain import INTERVALS, DataError, Key, json_bytes, micros, sha256, utc
from marketdata.store import atomic_write
from marketdata.transforms import resample

from .export import native_table, verify_export

PROFILE = "freqtrade-2026.8-v3-daily"


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export_window(
    store,
    snapshot_id,
    destination,
    start,
    end,
    timeframes=("1m", "5m", "15m", "1h"),
    *,
    progress=None,
):
    snapshot = store.snapshot_manifest(snapshot_id)
    start, end = utc(start), utc(end)
    if not utc(snapshot["start"]) <= start < end <= utc(snapshot["end"]):
        raise DataError("Export window must be inside the pinned snapshot")
    if any(not item["complete"] for item in snapshot["coverage"]):
        raise DataError("Export requires complete snapshot coverage")
    timeframes = sorted(set(timeframes), key=lambda t: INTERVALS.get(t, 0))
    if not timeframes or any(t not in INTERVALS for t in timeframes):
        raise DataError("Unsupported export timeframes")
    keys = [Key(**item) for item in snapshot["keys"].values()]
    ids = {key.id for key in keys}
    if any(k.dataset == "trade_bars" and k.market == "linear_perpetual" for k in keys):
        required = {Key("mark_price_bars").id, Key("funding_events", interval="event").id}
        if not required <= ids:
            raise DataError("Futures export requires 1m mark prices and funding events in snapshot")
    intervals = set(timeframes)
    if any(k.dataset in {"mark_price_bars", "index_price_bars"} for k in keys):
        intervals.add("1h")
    if any(micros(bound) % (INTERVALS[t] * 1_000_000) for t in intervals for bound in (start, end)):
        raise DataError(
            "Export bounds must align with all requested timeframes and risk-price hours"
        )
    options = {
        "profile": PROFILE,
        "snapshot_id": snapshot_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timeframes": timeframes,
    }
    destination = Path(destination).resolve()
    if destination.exists():
        existing = verify_export(destination)
        if existing["options"] != options:
            raise DataError("Export destination already contains a different export")
        return existing
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".export-", dir=destination.parent))
    streams = {}

    def write(table, relative, *, native=False, **extra):
        if relative not in streams:
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            sink = pa.OSFile(str(path), "wb") if native else None
            writer = (
                pa.ipc.new_file(
                    sink, table.schema, options=pa.ipc.IpcWriteOptions(compression="zstd")
                )
                if native
                else pq.ParquetWriter(path, table.schema, compression="zstd")
            )
            streams[relative] = {
                "writer": writer,
                "sink": sink,
                "record": {
                    "path": relative,
                    "rows": 0,
                    "columns": table.column_names,
                    "native": native,
                    **extra,
                },
            }
        stream = streams[relative]
        stream["writer"].write_table(table)
        stream["record"]["rows"] += len(table)

    try:
        for key in sorted(keys, key=lambda k: k.id):
            count = 0
            for table in store.iter_tables(key, start, end, snapshot_id):
                count += len(table)
                if key.dataset.endswith("_bars"):
                    candle = {"mark_price_bars": "mark", "index_price_bars": "index"}.get(
                        key.dataset
                    ) or ("spot" if key.market == "spot" else "futures")
                    requested = timeframes if key.dataset == "trade_bars" else ["1h"]
                    for interval in requested:
                        bars = resample(table, key, interval)
                        pair = "BTC/USDT" if key.market == "spot" else "BTC/USDT:USDT"
                        filename = (
                            f"BTC_USDT-{interval}.feather"
                            if candle == "spot"
                            else f"futures/BTC_USDT_USDT-{interval}-{candle}.feather"
                        )
                        write(
                            native_table(bars, key),
                            "data/" + filename,
                            native=True,
                            pair=pair,
                            timeframe=interval,
                            candle_type=candle,
                            compatibility_zero_volume=key.dataset != "trade_bars",
                        )
                        if key.dataset == "trade_bars":
                            write(
                                bars.append_column("decision_time", bars["bar_end"]),
                                f"features/trade_bars-{key.market}-{interval}.parquet",
                                key=key.asdict(),
                                interval=interval,
                            )
                elif key.dataset == "funding_events":
                    write(
                        native_table(table, key),
                        "data/futures/BTC_USDT_USDT-1h-funding_rate.feather",
                        native=True,
                        pair="BTC/USDT:USDT",
                        timeframe="1h",
                        candle_type="funding_rate",
                        note="Settlement events only; subsecond jitter floored to the hour",
                    )
                    write(
                        table,
                        "features/funding_events-linear_perpetual-primary.parquet",
                        key=key.asdict(),
                        note="Exact source settlement timestamps and decimals retained",
                    )
                else:
                    write(
                        table,
                        f"features/{key.dataset}-{key.market}-{key.series}.parquet",
                        key=key.asdict(),
                        note="Requires explicit strategy integration and availability assumptions",
                    )
            if not count:
                raise DataError(f"Empty dataset in export: {key.id}")
            if key.step and count != (micros(end) - micros(start)) // (key.step * 1_000_000):
                raise DataError(f"Snapshot row count does not cover the export window: {key.id}")
            if progress:
                progress({"key": key.id, "rows": count})
        files = []
        for relative, stream in sorted(streams.items()):
            stream["writer"].close()
            if stream["sink"] is not None:
                stream["sink"].close()
            with (staging / relative).open("rb") as handle:
                os.fsync(handle.fileno())
            stream["record"]["sha256"] = file_sha(staging / relative)
            files.append(stream["record"])
        manifest = {
            "format_version": 1,
            "options": options,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "source_snapshot_start": snapshot["start"],
            "source_snapshot_end": snapshot["end"],
            "freqtrade_version": "2026.8",
            "pyarrow_version": pa.__version__,
            "files": files,
            "availability_mode": snapshot["availability_mode"],
            "numeric_conversion": "decimal128 to float64 only in native Feather output",
            "funding_coverage": "source query coverage; funding schedule not assumed",
            "partitioning": "One verified canonical UTC day at a time; Arrow IPC Feather V2 and Parquet row groups",
        }
        manifest = {"export_id": sha256(json_bytes(manifest)), **manifest}
        atomic_write(staging / "manifest.json", json_bytes(manifest))
        staging.rename(destination)
        return manifest
    except BaseException:
        for stream in streams.values():
            with suppress(Exception):
                stream["writer"].close()
            if stream["sink"] is not None:
                with suppress(Exception):
                    stream["sink"].close()
        shutil.rmtree(staging)
        raise
