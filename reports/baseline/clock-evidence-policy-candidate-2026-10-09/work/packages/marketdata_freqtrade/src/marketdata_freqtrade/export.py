"""Versioned file export. Freqtrade is deliberately not a runtime dependency."""

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.feather as feather
import pyarrow.parquet as pq
from marketdata.domain import INTERVALS, DataError, Key, json_bytes, micros, sha256, utc
from marketdata.store import atomic_write
from marketdata.transforms import resample

PROFILE = "freqtrade-2026.8-v2"
OHLCV = ["date", "open", "high", "low", "close", "volume"]


def native_table(table, key):
    """Precision loss and compatibility-only volume happen at this boundary."""
    date = table["timestamp"].cast(pa.timestamp("ms", tz="UTC"))
    if key.dataset == "funding_events":
        timestamps = table["timestamp"].to_pylist()
        aligned = [t.replace(minute=0, second=0, microsecond=0) for t in timestamps]
        # Freqtrade's 1h funding loader floors timestamps. Accept only subsecond
        # settlement jitter; reject genuinely intrahour events instead of hiding them.
        if any((t - a).total_seconds() >= 1 for t, a in zip(timestamps, aligned)):
            raise DataError("Funding event is not within one second of an hour boundary")
        if len(set(aligned)) != len(table):
            raise DataError("Multiple funding event types at one time cannot be represented")
        return pa.table(
            {
                "date": pa.array(aligned, type=pa.timestamp("ms", tz="UTC")),
                "funding_rate": table["funding_rate"].cast(pa.float64()),
            }
        )
    values = {"date": date}
    values.update({name: table[name].cast(pa.float64()) for name in OHLCV[1:5]})
    values["volume"] = (
        table["base_volume"].cast(pa.float64())
        if key.dataset == "trade_bars"
        else pa.array([0.0] * len(table))
    )
    return pa.table(values)


def verify_export(path):
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_bytes())
    identity = manifest.pop("export_id")
    if sha256(json_bytes(manifest)) != identity:
        raise DataError("Export manifest integrity failure")
    for record in manifest["files"]:
        target = (path / record["path"]).resolve()
        if not target.is_relative_to(path.resolve()):
            raise DataError("Export path escapes its directory")
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != record["sha256"]:
            raise DataError(f"Export file hash mismatch: {target}")
    return {"export_id": identity, **manifest}


def export_snapshot(store, snapshot_id, destination, timeframes=("1m", "5m", "15m", "1h")):
    snapshot = store.snapshot_manifest(snapshot_id)
    if any(not item["complete"] for item in snapshot["coverage"]):
        raise DataError("Export requires complete snapshot coverage")
    timeframes = sorted(set(timeframes), key=lambda t: INTERVALS.get(t, 0))
    if not timeframes or any(t not in INTERVALS for t in timeframes):
        raise DataError("Unsupported export timeframes")
    keys = [Key(**item) for item in snapshot["keys"].values()]
    by_id = {key.id for key in keys}
    if any(k.dataset == "trade_bars" and k.market == "linear_perpetual" for k in keys):
        required = {Key("mark_price_bars").id, Key("funding_events", interval="event").id}
        if not required <= by_id:
            raise DataError("Futures export requires 1m mark prices and funding events in snapshot")
    start, end = utc(snapshot["start"]), utc(snapshot["end"])
    destination = Path(destination).resolve()
    options = {"profile": PROFILE, "snapshot_id": snapshot_id, "timeframes": timeframes}
    if destination.exists():
        existing = verify_export(destination)
        if existing["options"] != options:
            raise DataError("Export destination already contains a different export")
        return existing
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".export-", dir=destination.parent))
    files = []

    def write(table, relative, *, native=False, **extra):
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        sink = pa.BufferOutputStream()
        if native:
            feather.write_feather(table, sink, compression="zstd")
        else:
            pq.write_table(table, sink, compression="zstd")
        blob = sink.getvalue().to_pybytes()
        atomic_write(path, blob)
        files.append(
            {
                "path": relative,
                "rows": len(table),
                "columns": table.column_names,
                "sha256": sha256(blob),
                "native": native,
                **extra,
            }
        )

    try:
        for key in sorted(keys, key=lambda k: k.id):
            table = store.read(key, start, end, snapshot_id)
            if not len(table):
                raise DataError(f"Empty dataset in export: {key.id}")
            if key.dataset.endswith("_bars"):
                candle = {"mark_price_bars": "mark", "index_price_bars": "index"}.get(
                    key.dataset
                ) or ("spot" if key.market == "spot" else "futures")
                requested = timeframes if key.dataset == "trade_bars" else ["1h"]
                for interval in requested:
                    step = INTERVALS[interval] * 1_000_000
                    if micros(start) % step or micros(end) % step:
                        raise DataError("Snapshot bounds must align with all exported timeframes")
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
                        extra = bars.append_column("decision_time", bars["bar_end"])
                        write(
                            extra,
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
                    note="Settlement events only; subsecond timestamp jitter floored to hour for native loader",
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
                    note="Additional data requires explicit strategy integration",
                )
        manifest = {
            "format_version": 1,
            "options": options,
            "start": snapshot["start"],
            "end": snapshot["end"],
            "freqtrade_version": "2026.8",
            "files": files,
            "availability_mode": snapshot["availability_mode"],
            "numeric_conversion": "decimal128 to float64 only in native Feather output",
            "funding_coverage": "source query coverage; funding schedule not assumed",
        }
        manifest = {"export_id": sha256(json_bytes(manifest)), **manifest}
        atomic_write(staging / "manifest.json", json_bytes(manifest))
        staging.rename(destination)
        return manifest
    except BaseException:
        shutil.rmtree(staging)
        raise
