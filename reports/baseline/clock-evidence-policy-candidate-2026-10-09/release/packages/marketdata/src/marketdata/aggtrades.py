"""Bounded BTCUSDT USD-M aggregate trades; immutable segments, ID-based pagination."""

import json
from collections import Counter
from datetime import timedelta
from decimal import Decimal, localcontext
from urllib.parse import urlencode
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from .binance import FUTURES, Fetcher, SourceError
from .clock_evidence import collection_context, finish_context
from .domain import DataError, Key, from_epoch, json_bytes, micros, now, number, sha256, utc
from .oi_probe import retry_delay
from .schema import TIME, VALUE
from .store import atomic_write

DATASET = "agg_trades"
INSTRUMENT = "binance:linear_perpetual:BTCUSDT"
REST = FUTURES + "/fapi/v1/aggTrades"
WS = "wss://fstream.binance.com/market/ws/btcusdt@aggTrade"
ECONOMIC = ("timestamp", "price", "quantity", "first_trade_id", "last_trade_id", "buyer_is_maker")
SCHEMA = pa.schema(
    [
        pa.field("instrument_id", pa.string(), False),
        pa.field("agg_trade_id", pa.int64(), False),
        pa.field("timestamp", TIME, False),
        pa.field("event_time", TIME),
        pa.field("received_at", TIME, False),
        pa.field("price", VALUE, False),
        pa.field("quantity", VALUE, False),
        pa.field("normal_quantity", VALUE),
        pa.field("first_trade_id", pa.int64(), False),
        pa.field("last_trade_id", pa.int64(), False),
        pa.field("buyer_is_maker", pa.bool_(), False),
        pa.field("source_id", pa.string(), False),
        pa.field("source_row", pa.int64(), False),
    ],
    metadata={b"dataset": b"agg_trades", b"schema_version": b"1"},
)


def integer(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise DataError("Expected nonnegative int64 ID/timestamp")
    return value


def parse_trade(raw, source_id, ordinal, received_at, *, websocket=False):
    if not isinstance(raw, dict):
        raise DataError("Expected aggregate trade object")
    if websocket and (raw.get("e") != "aggTrade" or raw.get("s") != "BTCUSDT"):
        raise DataError("Wrong WebSocket event type/symbol")
    if "st" in raw and raw["st"] != 1:
        raise DataError("Expected USD-M aggregate trade")
    if type(raw.get("m")) is not bool:
        raise DataError("buyer_is_maker must be a boolean")
    first, last = integer(raw["f"]), integer(raw["l"])
    if first > last:
        raise DataError("Reversed first/last trade IDs")
    quantity = number(raw["q"], positive=True)
    normal = number(raw["nq"], nonnegative=True) if raw.get("nq") is not None else None
    if normal is not None and normal > quantity:
        raise DataError("Normal quantity exceeds total quantity")
    return {
        "instrument_id": INSTRUMENT,
        "agg_trade_id": integer(raw["a"]),
        "timestamp": from_epoch(integer(raw["T"])),
        "event_time": from_epoch(integer(raw["E"])) if websocket else None,
        "received_at": utc(received_at),
        "price": number(raw["p"], positive=True),
        "quantity": quantity,
        "normal_quantity": normal,
        "first_trade_id": first,
        "last_trade_id": last,
        "buyer_is_maker": raw["m"],
        "source_id": source_id,
        "source_row": ordinal,
    }


def same_trade(a, b):
    return all(a[key] == b[key] for key in ECONOMIC) and (
        a["normal_quantity"] is None
        or b["normal_quantity"] is None
        or a["normal_quantity"] == b["normal_quantity"]
    )


def ordered(rows):
    previous = None
    for row in rows:
        if previous is not None and (
            row["agg_trade_id"] <= previous["agg_trade_id"]
            or row["timestamp"] < previous["timestamp"]
        ):
            raise DataError("Aggregate trade page IDs/times must be strictly ordered by ID")
        previous = row


def id_gaps(ids):
    return [[a + 1, b - 1] for a, b in zip(ids, ids[1:]) if b > a + 1]


def start_collection(store, kind, details=None):
    identity, stamp = uuid4().hex, now()
    report = {"id": identity, "kind": kind, "started_at": stamp, **(details or {}), **collection_context(kind)}
    with store.writer(timeout=60), store.db:
        store.db.execute(
            "INSERT INTO event_collections VALUES(?,?,?,?,?,?,?)",
            (
                identity,
                DATASET,
                kind,
                stamp.isoformat(),
                stamp.isoformat(),
                "running",
                json_bytes(report).decode(),
            ),
        )
    return report


def save_collection(store, report, state):
    finish_context(report, state)
    report = {**report, "state": state, "updated_at": now()}
    with store.writer(timeout=60), store.db:
        store.db.execute(
            "UPDATE event_collections SET state=?,updated_at=?,report=? WHERE id=?",
            (
                state,
                report["updated_at"].isoformat(),
                json_bytes(report).decode(),
                report["id"],
            ),
        )
    return report


def publish_segment(store, collection_id, kind, rows, source_id):
    if not rows:
        return None
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd")
    blob = sink.getvalue().to_pybytes()
    identity = sha256(blob)
    relative = f"data/canonical/agg_trades/{kind}/{identity[:2]}/{identity}.parquet"
    record = {
        "id": identity,
        "collection_id": collection_id,
        "dataset": DATASET,
        "instrument_id": INSTRUMENT,
        "kind": kind,
        "path": relative,
        "file_sha": identity,
        "source_id": source_id,
        "first_time": min(r["timestamp"] for r in rows).isoformat(),
        "last_time": max(r["timestamp"] for r in rows).isoformat(),
        "first_id": min(r["agg_trade_id"] for r in rows),
        "last_id": max(r["agg_trade_id"] for r in rows),
        "row_count": len(rows),
    }
    with store.writer(timeout=60):
        path = store.root / relative
        if store.file_exists(relative):
            if sha256(store.file_bytes(relative)) != identity:
                raise DataError("Corrupted existing aggregate trade segment")
        else:
            atomic_write(path, blob)
        with store.db:
            store.db.execute(
                "INSERT OR IGNORE INTO event_segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(record.values()),
            )
    return identity


def save_response(store, collection_id, response, request_started):
    with store.writer(timeout=60):
        return store.put_raw(
            response.body,
            response.url,
            "aggtrade_rest",
            response.received_at,
            {
                "collection_id": collection_id,
                "headers": response.headers,
                "request_started_at": request_started,
                "received_at": response.received_at,
            },
        )


def fetch_page(store, fetcher, collection_id, **params):
    url = REST + "?" + urlencode({"symbol": "BTCUSDT", "limit": 1000, **params})
    started = now()
    cooldown = store.root / "state/aggtrade-rest-cooldown.json"
    if cooldown.exists() and started < utc(json.loads(cooldown.read_bytes())["until"]):
        raise DataError("Aggregate REST cooldown is active; retry later")
    try:
        response = fetcher.fetch(url, weight=20)
    except SourceError as exc:
        with store.writer(timeout=60):
            store.put_raw(
                exc.body,
                exc.url or url,
                "aggtrade_rest_error",
                metadata={
                    "collection_id": collection_id,
                    "status": exc.status,
                    "headers": exc.headers,
                    "request_started_at": started,
                    "error": str(exc),
                },
            )
            atomic_write(
                cooldown,
                json_bytes(
                    {
                        "until": now() + timedelta(seconds=retry_delay(exc, now())),
                        "status": exc.status,
                        "reason": str(exc),
                    }
                ),
            )
        raise
    source_id = save_response(store, collection_id, response, started)
    raw = json.loads(response.body)
    if not isinstance(raw, list) or len(raw) > 1000:
        raise DataError("Expected bounded aggregate trade list")
    rows = [parse_trade(item, source_id, i, response.received_at) for i, item in enumerate(raw)]
    ordered(rows)
    return rows, source_id


def collect_rest(
    store,
    start=None,
    end=None,
    *,
    first_id=None,
    last_id=None,
    fetcher=None,
    max_pages=100,
    progress=None,
):
    by_id = first_id is not None or last_id is not None
    if not 1 <= max_pages <= 1000:
        raise DataError("max-pages must be between 1 and 1000")
    if by_id:
        first_id, last_id = integer(first_id), integer(last_id)
        if first_id > last_id or start is not None or end is not None:
            raise DataError("Expected an inclusive ID range, without times")
    else:
        if start is None or end is None:
            raise DataError("Supply both start and end, or both first-id and last-id")
        start, end = utc(start), utc(end)
        if not now() - timedelta(hours=47) <= start < end <= now():
            raise DataError("REST pilot must be within the conservative past 47 hours")
        if end - start > timedelta(minutes=59) or micros(start) % 1000 or micros(end) % 1000:
            raise DataError("REST pilot requires millisecond bounds and at most 59 minutes")
    report = start_collection(
        store,
        "rest",
        {
            "start": start,
            "end": end,
            "first_requested_id": first_id,
            "last_requested_id": last_id,
            "pages": 0,
            "rows": 0,
            "gaps": [],
            "query_complete": False,
            "semantics": "source_query_completion_and_observed_ID_continuity_only",
        },
    )
    own = fetcher is None
    fetcher = fetcher or Fetcher(retries=0)
    cursor, previous = first_id, None
    try:
        while report["pages"] < max_pages:
            params = (
                {"fromId": cursor}
                if cursor is not None
                else {
                    "startTime": micros(start) // 1000,
                    "endTime": micros(end) // 1000 - 1,
                }
            )
            rows, source_id = fetch_page(store, fetcher, report["id"], **params)
            report["pages"] += 1
            if rows and cursor is not None and rows[0]["agg_trade_id"] < cursor:
                raise DataError("REST fromId pagination did not advance")
            if (
                rows
                and not by_id
                and cursor is None
                and any(not start <= r["timestamp"] < end for r in rows)
            ):
                raise DataError("REST initial page is outside the requested time bounds")
            selected = [
                r
                for r in rows
                if (
                    first_id <= r["agg_trade_id"] <= last_id
                    if by_id
                    else start <= r["timestamp"] < end
                )
            ]
            publish_segment(store, report["id"], "rest", selected, source_id)
            ids = [r["agg_trade_id"] for r in selected]
            if ids:
                preceding = previous if previous is not None else first_id - 1 if by_id else None
                report["gaps"].extend(id_gaps(([preceding] if preceding is not None else []) + ids))
                previous = ids[-1]
                report["first_id"] = report.get("first_id", ids[0])
                report["last_id"] = previous
                report["rows"] += len(ids)
            reached = bool(rows) and (
                rows[-1]["agg_trade_id"] >= last_id if by_id else rows[-1]["timestamp"] >= end
            )
            if reached or len(rows) < 1000:
                report["query_complete"] = True
                if by_id and (previous is None or previous < last_id):
                    report["gaps"].append([first_id if previous is None else previous + 1, last_id])
                break
            cursor = rows[-1]["agg_trade_id"] + 1
            save_collection(store, report, "running")
            if progress:
                progress(
                    {"phase": "aggtrade_rest", "pages": report["pages"], "rows": report["rows"]}
                )
        report["complete"] = report["query_complete"] and not report["gaps"]
        if not report["query_complete"]:
            report["stop_reason"] = "page_budget_exhausted"
        return save_collection(store, report, "complete" if report["complete"] else "partial")
    except (Exception, KeyboardInterrupt) as exc:
        report.update(complete=False, error=f"{type(exc).__name__}: {exc}")
        save_collection(
            store, report, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        )
        raise
    finally:
        if own:
            fetcher.close()


def read_trades(store, start, end, *, kind=None, max_rows=1_000_000):
    start, end = utc(start), utc(end)
    if not start < end or end - start > timedelta(hours=1):
        raise DataError("Pilot reader requires a positive window of at most one hour")
    if kind not in (None, "rest", "websocket"):
        raise DataError("Unsupported aggregate trade source")
    records = [
        dict(r)
        for r in store.db.execute(
            """SELECT * FROM event_segments WHERE dataset=? AND instrument_id=?
        AND first_time<? AND last_time>=? ORDER BY first_id,id""",
            (DATASET, INSTRUMENT, end.isoformat(), start.isoformat()),
        )
        if kind is None or r["kind"] == kind
    ]
    merged, duplicates, conflicts, known_normal = {}, 0, [], {}
    input_rows = 0
    for record in records:
        if input_rows + record["row_count"] > max_rows:
            raise DataError("Pilot read exceeds row budget; narrow the time window")
        table = store._revision_table(record)
        input_rows += len(table)
        for row in table.to_pylist():
            if not start <= row["timestamp"] < end:
                continue
            identity = row["agg_trade_id"]
            known = known_normal.get(identity)
            if row["normal_quantity"] is not None:
                if known and known["normal_quantity"] != row["normal_quantity"]:
                    conflicts.append(
                        {
                            "agg_trade_id": identity,
                            "field": "normal_quantity",
                            "source_ids": [known["source_id"], row["source_id"]],
                        }
                    )
                known_normal[identity] = row
            old = merged.get(identity)
            if old is None:
                merged[identity] = row
            else:
                duplicates += 1
                if not same_trade(old, row):
                    conflicts.append(
                        {
                            "agg_trade_id": identity,
                            "source_ids": [old["source_id"], row["source_id"]],
                        }
                    )
                # Preserve the earliest local observation when repeated copies agree.
                if row["received_at"] < old["received_at"]:
                    merged[identity] = row
    ids = sorted(merged)
    rows = [merged[i] for i in ids]
    reversed_times = sum(b["timestamp"] < a["timestamp"] for a, b in zip(rows, rows[1:]))
    return rows, {
        "start": start,
        "end": end,
        "kind": kind,
        "segments": [r["id"] for r in records],
        "rows": len(rows),
        "duplicate_rows": duplicates,
        "conflicts": conflicts,
        "first_id": ids[0] if ids else None,
        "last_id": ids[-1] if ids else None,
        "id_gaps": id_gaps(ids),
        "time_reversals": reversed_times,
        "valid": bool(rows) and not conflicts and not id_gaps(ids) and not reversed_times,
        "semantics": "continuity_between_observed_IDs_not_proof_of_time_window_edges",
    }


def compare_sources(store, start, end):
    rest, rest_audit = read_trades(store, start, end, kind="rest")
    live, live_audit = read_trades(store, start, end, kind="websocket")
    _, combined_audit = read_trades(store, start, end)
    a, b = {r["agg_trade_id"]: r for r in rest}, {r["agg_trade_id"]: r for r in live}
    common = sorted(a.keys() & b.keys())
    mismatches = sorted(
        {i for i in common if not same_trade(a[i], b[i])}
        | {r["agg_trade_id"] for r in combined_audit["conflicts"]}
    )
    return {
        "rest": rest_audit,
        "websocket": live_audit,
        "combined": combined_audit,
        "compared": len(common),
        "mismatched_ids": mismatches,
        "rest_only_ids": sorted(a.keys() - b.keys()),
        "websocket_only_ids": sorted(b.keys() - a.keys()),
        "equal": bool(common)
        and a.keys() == b.keys()
        and not mismatches
        and rest_audit["valid"]
        and live_audit["valid"],
    }


def compare_candles(store, start, end, *, kind="rest"):
    start, end = utc(start), utc(end)
    if micros(start) % 60_000_000 or micros(end) % 60_000_000:
        raise DataError("Candle comparison requires whole minute bounds")
    rows, audit = read_trades(store, start, end, kind=kind)
    if not audit["valid"]:
        raise DataError(
            "Cannot aggregate trades with empty data, ID gaps, conflicts or time reversals"
        )
    candles = store.read(Key("trade_bars"), start, end).to_pylist()
    groups = {}
    with localcontext() as context:
        context.prec = 80
        for row in rows:
            minute = row["timestamp"].replace(second=0, microsecond=0)
            p, q = row["price"], row["quantity"]
            if minute not in groups:
                groups[minute] = dict(
                    timestamp=minute,
                    open=p,
                    high=p,
                    low=p,
                    close=p,
                    base_volume=Decimal(0),
                    quote_volume=Decimal(0),
                    taker_buy_base_volume=Decimal(0),
                    taker_buy_quote_volume=Decimal(0),
                    aggregate_event_count=0,
                )
            bar = groups[minute]
            bar.update(high=max(bar["high"], p), low=min(bar["low"], p), close=p)
            bar["base_volume"] += q
            bar["quote_volume"] += p * q
            bar["aggregate_event_count"] += 1
            if not row["buyer_is_maker"]:
                bar["taker_buy_base_volume"] += q
                bar["taker_buy_quote_volume"] += p * q
        checks, counts = [], Counter()
        fields = (
            "open",
            "high",
            "low",
            "close",
            "base_volume",
            "quote_volume",
            "taker_buy_base_volume",
            "taker_buy_quote_volume",
        )
        for candle in candles:
            actual = groups.get(candle["timestamp"])
            if actual is None:
                checks.append({"timestamp": candle["timestamp"], "missing_aggtrades": True})
                continue
            differences = {
                f: {"aggtrade": actual[f], "candle": candle[f], "delta": actual[f] - candle[f]}
                for f in fields
                if actual[f] != candle[f]
            }
            counts.update(differences.keys())
            checks.append(
                {
                    "timestamp": candle["timestamp"],
                    "differences": differences,
                    "aggregate_event_count": actual["aggregate_event_count"],
                    "candle_trade_count": candle["trade_count"],
                }
            )
    expected = int((end - start).total_seconds() / 60)
    return {
        "audit": audit,
        "minutes_expected": expected,
        "candles": len(candles),
        "aggregated_minutes": len(groups),
        "mismatches_by_field": dict(counts),
        "checks": checks,
        "equal": len(candles) == len(groups) == expected
        and not counts
        and not any(r.get("missing_aggtrades") for r in checks),
        "note": "Aggregate event counts are not individual trade counts. ADL/insurance trades are excluded by the aggregate endpoint; report differences without modifying either source.",
    }
