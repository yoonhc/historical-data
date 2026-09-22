"""Public-data CLI. All mutations take one local writer lock."""

import argparse
import json
import sys
import time
import tomllib
from datetime import timedelta
from pathlib import Path

from .binance import SourceError, archive_units, archive_url
from .domain import DataError, Key, json_bytes, json_default, now, utc
from .pipeline import Collector
from .store import Store, atomic_write


def emit(value, *, error=False):
    print(
        json.dumps(value, default=json_default, ensure_ascii=False),
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


def key_args(parser):
    parser.add_argument(
        "--dataset",
        choices=[
            "trade_bars",
            "mark_price_bars",
            "index_price_bars",
            "funding_events",
            "open_interest",
            "instrument_metadata",
        ],
        required=True,
    )
    parser.add_argument(
        "--market", choices=["spot", "linear_perpetual"], default="linear_perpetual"
    )
    parser.add_argument("--interval")
    parser.add_argument("--series", choices=["primary", "archive", "rest"])


def make_key(args):
    interval = args.interval or (
        "5m"
        if args.dataset == "open_interest"
        else "event"
        if args.dataset in {"funding_events", "instrument_metadata"}
        else "1m"
    )
    series = args.series or ("archive" if args.dataset == "open_interest" else "primary")
    return Key(args.dataset, args.market, interval=interval, series=series)


def range_args(parser):
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True, help="Exclusive UTC end")


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument(
        "--root", type=Path, default=Path("."), help="Data store, not source-code path"
    )
    sub = root.add_subparsers(dest="command", required=True)
    for name in ("plan", "backfill", "repair", "coverage", "query"):
        p = sub.add_parser(name)
        key_args(p)
        range_args(p)
        if name in {"plan", "backfill", "repair"}:
            p.add_argument("--source", choices=["auto", "archive", "rest"], default="auto")
            p.add_argument("--daily", action="store_true")
        if name in {"backfill", "repair"}:
            p.add_argument("--refresh", action="store_true")
            p.add_argument("--accept-revisions", action="store_true")
        if name == "query":
            p.add_argument("--snapshot")
            p.add_argument("--limit", type=int, default=5)
    p = sub.add_parser("import-archive")
    key_args(p)
    range_args(p)
    p.add_argument("path", type=Path)
    p.add_argument("--checksum", type=Path)
    p.add_argument("--accept-revisions", action="store_true")
    p = sub.add_parser("import-legacy")
    p.add_argument("path", type=Path)
    range_args(p)
    p.add_argument(
        "--datasets",
        nargs="+",
        choices=["trade_bars", "open_interest"],
        default=["trade_bars", "open_interest"],
    )
    p.add_argument("--report", type=Path)
    p = sub.add_parser("snapshot")
    p.add_argument("--config", type=Path, required=True)
    range_args(p)
    p.add_argument("--allow-gaps", action="store_true")
    p = sub.add_parser("backfill-set")
    p.add_argument("--config", type=Path, required=True)
    range_args(p)
    p.add_argument("--report", type=Path)
    sub.add_parser("validate")
    for name in ("update", "watch"):
        p = sub.add_parser(name)
        p.add_argument("--config", type=Path, required=True)
        if name == "watch":
            p.add_argument(
                "--cycles", type=int, default=0, help="0 runs continuously; interrupt to stop"
            )
            p.add_argument("--sleep-seconds", type=int, default=300)
    return root


def config_keys(path):
    with path.open("rb") as f:
        config = tomllib.load(f)
    items = config.get("datasets", [])
    if not items:
        raise DataError("No datasets configured")
    keys = [Key(**item) for item in items]
    if len({k.id for k in keys}) != len(keys):
        raise DataError("Duplicate dataset key in config")
    return keys


def source_for(key, requested):
    if requested != "auto":
        return requested
    return (
        "rest"
        if key.dataset in {"funding_events", "instrument_metadata"} or key.series == "rest"
        else "archive"
    )


def incomplete(result):
    if isinstance(result, list):
        return any(incomplete(item) for item in result)
    if isinstance(result, dict):
        return (
            result.get("state") in {"waiting_source", "quarantined", "failed"}
            or result.get("complete") is False
            or bool(result.get("quarantined"))
            or incomplete(result.get("results", []))
        )
    return False


def update(collector, keys):
    output = []
    current = now()
    midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    for key in keys:
        source = source_for(key, "auto")
        if key.dataset == "instrument_metadata":
            start, end = midnight, current
        elif key.dataset == "open_interest" and key.series == "rest":
            end = current.replace(second=0, microsecond=0) - timedelta(minutes=current.minute % 5)
            records = collector.store.revision_list(key, end - timedelta(days=29), end)
            if records:
                table = collector.store.read(key, utc(records[-1]["day"]), end)
                last = table["timestamp"][-1].as_py() if len(table) else end - timedelta(days=1)
                start = max(end - timedelta(days=29), last - timedelta(hours=1))
            else:
                start = end - timedelta(days=1)
        else:
            start, end = midnight - timedelta(days=3), midnight
        if source == "rest":
            result = collector.rest(key, start, end)
        else:
            result = collector.archive(
                key, start, end, monthly=False, refresh=True, progress=lambda p: emit(p, error=True)
            )
        output.append({"key": key.id, "results": result})
    return output


def main(argv=None):
    args = parser().parse_args(argv)
    store = Store(args.root)
    collector = Collector(store)
    try:
        if args.command == "coverage":
            result = store.coverage(make_key(args), args.start, args.end)
        elif args.command == "query":
            table = store.read(make_key(args), args.start, args.end, args.snapshot)
            result = {
                "rows": len(table),
                "schema": str(table.schema),
                "sample": table.slice(0, max(0, args.limit)).to_pylist(),
            }
        elif args.command == "validate":
            result = store.validate()
            emit(result)
            return 0 if result["ok"] else 1
        elif args.command == "plan":
            key = make_key(args)
            source = source_for(key, args.source)
            result = {
                "key": key.asdict(),
                "source": source,
                "start": utc(args.start),
                "end": utc(args.end),
            }
            if source == "archive":
                result["archives"] = [
                    {"start": a, "end": b, "url": archive_url(key, a, m)}
                    for a, b, m in archive_units(key, args.start, args.end, not args.daily)
                ]
        elif args.command == "watch":
            keys = config_keys(args.config)
            iteration = 0
            last_failed = False
            while args.cycles == 0 or iteration < args.cycles:
                try:
                    with store.writer():
                        result = update(collector, keys)
                    emit({"cycle": iteration, "result": result})
                    last_failed = incomplete(result)
                except (DataError, SourceError) as exc:
                    emit({"cycle": iteration, "error": str(exc)}, error=True)
                    last_failed = True
                iteration += 1
                if args.cycles == 0 or iteration < args.cycles:
                    time.sleep(max(1, args.sleep_seconds))
            return 2 if last_failed else 0
        else:
            with store.writer():
                if args.command in {"backfill", "repair"}:
                    key = make_key(args)
                    source = source_for(key, args.source)
                    if source == "rest":
                        result = collector.rest(
                            key, args.start, args.end, accept_revisions=args.accept_revisions
                        )
                    else:
                        result = collector.archive(
                            key,
                            args.start,
                            args.end,
                            monthly=not args.daily,
                            refresh=args.refresh or args.command == "repair",
                            accept_revisions=args.accept_revisions,
                            progress=lambda p: emit(p, error=True),
                        )
                    if key.dataset != "instrument_metadata":
                        coverage = store.coverage(key, args.start, args.end)
                        result = {
                            "results": result,
                            "coverage": coverage,
                            "complete": coverage["complete"],
                        }
                elif args.command == "import-archive":
                    result = collector.import_archive(
                        args.path,
                        make_key(args),
                        args.start,
                        args.end,
                        args.checksum,
                        args.accept_revisions,
                    )
                elif args.command == "import-legacy":
                    result = collector.import_legacy(
                        args.path,
                        args.start,
                        args.end,
                        args.datasets,
                        progress=lambda p: emit(p, error=True),
                    )
                    if args.report:
                        atomic_write(args.report, json_bytes(result))
                        result = {
                            "partitions": len(result),
                            "report": str(args.report),
                            "quarantined": sum(r["state"] == "quarantined" for r in result),
                        }
                elif args.command == "snapshot":
                    result = {
                        "snapshot_id": store.create_snapshot(
                            config_keys(args.config),
                            args.start,
                            args.end,
                            allow_gaps=args.allow_gaps,
                        )
                    }
                elif args.command == "update":
                    result = update(collector, config_keys(args.config))
                elif args.command == "backfill-set":
                    result = []
                    for key in config_keys(args.config):
                        try:
                            if source_for(key, "auto") == "rest":
                                partitions = collector.rest(key, args.start, args.end)
                            else:
                                partitions = collector.archive(
                                    key,
                                    args.start,
                                    args.end,
                                    progress=lambda p: emit(p, error=True),
                                )
                            coverage = store.coverage(key, args.start, args.end)
                            result.append(
                                {
                                    "key": key.asdict(),
                                    "results": partitions,
                                    "coverage": coverage,
                                    "complete": coverage["complete"],
                                }
                            )
                        except (DataError, SourceError, OSError) as exc:
                            result.append(
                                {"key": key.asdict(), "state": "failed", "error": str(exc)}
                            )
                        emit(
                            {"key": key.id, "state": result[-1].get("state", "checked")}, error=True
                        )
                    if args.report:
                        atomic_write(args.report, json_bytes(result))
                else:
                    raise DataError("Unknown command")
        emit(result)
        return 2 if incomplete(result) else 0
    except (DataError, SourceError, OSError, KeyError) as exc:
        emit({"error": type(exc).__name__, "message": str(exc)}, error=True)
        return 1
    except KeyboardInterrupt:
        emit(
            {"state": "interrupted", "message": "Raw files retained; rerun the same command"},
            error=True,
        )
        return 130
    finally:
        collector.fetcher.close()
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
