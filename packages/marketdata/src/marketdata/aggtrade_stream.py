"""Single-instrument WebSocket pilot with bounded flushes and explicit ID recovery."""

import fcntl
import gzip
import json
import shutil
import time
from collections import OrderedDict
from contextlib import contextmanager

from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.sync.client import connect

from .aggtrades import (
    DATASET,
    WS,
    collect_rest,
    parse_trade,
    publish_segment,
    same_trade,
    save_collection,
    start_collection,
)
from .backup import reserve_bytes
from .domain import DataError, WriterBusy, json_bytes, now, sha256, utc
from .store import atomic_write
from .stream_guard import check_cooldown, rate_limited


@contextmanager
def stream_lock(store):
    with (store.root / "state/aggtrade-stream.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WriterBusy("Another aggregate trade stream is running") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Sequence:
    """Detect gaps without treating multiple trades at one timestamp as duplicates."""

    def __init__(self, last=None):
        self.last = last
        self.recent = OrderedDict()
        if last:
            self.recent[last["agg_trade_id"]] = last

    def observe(self, row):
        identity = row["agg_trade_id"]
        prior = self.recent.get(identity)
        if prior:
            if not same_trade(prior, row):
                raise DataError(f"Conflicting WebSocket aggregate trade ID {identity}")
            return {"duplicate": True}
        previous = self.last
        gap = None
        reversed_id = previous is not None and identity < previous["agg_trade_id"]
        if previous and identity > previous["agg_trade_id"] + 1:
            gap = [previous["agg_trade_id"] + 1, identity - 1]
        if (
            previous
            and identity > previous["agg_trade_id"]
            and row["timestamp"] < previous["timestamp"]
        ):
            raise DataError("Aggregate trade timestamp reversed as ID advanced")
        if previous is None or identity > previous["agg_trade_id"]:
            self.last = row
        self.recent[identity] = row
        if len(self.recent) > 10000:
            self.recent.popitem(last=False)
        return {"gap": gap, "out_of_order": reversed_id}


def persist_messages(store, report, messages):
    if not messages:
        return 0
    blob = json_bytes(messages)
    with store.writer(timeout=60):
        source_id = store.put_raw(
            gzip.compress(blob, mtime=0),
            WS,
            "aggtrade_websocket",
            messages[0]["received_at"],
            {
                "collection_id": report["id"],
                "connection": report["connections"],
                "timestamp_semantics": "application_recv_return_not_wire_arrival",
                "compression": "gzip",
                "uncompressed_bytes": len(blob),
                "uncompressed_sha256": sha256(blob),
            },
        )
    rows = [
        parse_trade(json.loads(item["message"]), source_id, i, item["received_at"], websocket=True)
        for i, item in enumerate(messages)
    ]
    publish_segment(store, report["id"], "websocket", rows, source_id)
    return len(rows)


def latest_trade(store):
    record = store.db.execute(
        """SELECT * FROM event_segments WHERE dataset=? AND kind='websocket'
        ORDER BY last_id DESC LIMIT 1""",
        (DATASET,),
    ).fetchone()
    if record is None:
        return None
    return max(store._revision_table(record).to_pylist(), key=lambda r: r["agg_trade_id"])


def capture(
    store,
    *,
    seconds=300,
    flush_seconds=5,
    rotate_seconds=3600,
    min_free_gib=10,
    fresh_start=False,
    connector=connect,
    recover=None,
    progress=None,
):
    if (
        not 0 <= seconds <= 86400
        or not 1 <= flush_seconds <= 30
        or not 10 <= rotate_seconds <= 82800
    ):
        raise DataError(
            "Expected seconds 0..86400, flush 1..30, rotation 10..82800; 0 runs continuously"
        )
    minimum = reserve_bytes(min_free_gib)
    recover = recover or (lambda a, b: collect_rest(store, first_id=a, last_id=b, max_pages=20))
    with stream_lock(store):
        check_cooldown(store, "aggtrade")
        with store.writer(timeout=60), store.db:
            for prior in store.db.execute(
                """SELECT id,report FROM event_collections WHERE dataset=?
                AND kind='websocket' AND state='running'""",
                (DATASET,),
            ).fetchall():
                interrupted = {
                    **json.loads(prior["report"]),
                    "state": "interrupted",
                    "updated_at": now(),
                    "complete": False,
                    "error": "Previous process exited without a final report",
                }
                store.db.execute(
                    "UPDATE event_collections SET state=?,updated_at=?,report=? WHERE id=?",
                    (
                        "interrupted",
                        interrupted["updated_at"].isoformat(),
                        json_bytes(interrupted).decode(),
                        prior["id"],
                    ),
                )
        report = start_collection(
            store,
            "websocket",
            {
                "messages": 0,
                "persisted_messages": 0,
                "connections": 0,
                "rotations": 0,
                "duplicate_messages": 0,
                "out_of_order_messages": 0,
                "detected_gaps": 0,
                "repaired_gaps": 0,
                "unrepaired_gaps": 0,
                "recent_recoveries": [],
                "connection_errors": 0,
                "recent_errors": [],
                "seconds_requested": seconds,
                "complete": False,
                "timestamp_semantics": "application_recv_return_not_wire_arrival",
                "boundary_semantics": "first_session_start_and_final_stop_are_not_complete_time_windows",
            },
        )
        previous = latest_trade(store)
        sequence = Sequence(None if fresh_start else previous)
        report["fresh_start"] = fresh_start
        report["previous_persisted_id"] = previous["agg_trade_id"] if previous else None
        report["uncollected_before_start"] = None
        report["resume_after_id"] = sequence.last["agg_trade_id"] if sequence.last else None
        messages = []
        started, last_flush = time.monotonic(), time.monotonic()
        deadline = started + seconds if seconds else float("inf")
        failures = 0

        def flush():
            nonlocal last_flush
            if shutil.disk_usage(store.root).free < minimum:
                raise DataError("Aggregate trade capture stopped: free space below reserve")
            report["persisted_messages"] += persist_messages(store, report, messages)
            messages.clear()
            save_collection(store, report, "running")
            atomic_write(
                store.root / "state/aggtrade-latest.json",
                json_bytes({**report, "updated_at": now(), "state": "running"}),
            )
            last_flush = time.monotonic()
            if progress:
                progress(
                    {
                        "phase": "aggtrade_stream",
                        "messages": report["messages"],
                        "persisted": report["persisted_messages"],
                        "connections": report["connections"],
                        "unrepaired_gaps": report["unrepaired_gaps"],
                    }
                )

        try:
            while time.monotonic() < deadline:
                try:
                    with connector(
                        WS,
                        open_timeout=10,
                        close_timeout=5,
                        ping_interval=20,
                        ping_timeout=20,
                        max_size=64_000,
                        max_queue=256,
                    ) as socket:
                        report["connections"] += 1
                        connected = last_message = time.monotonic()
                        while time.monotonic() < min(deadline, connected + rotate_seconds):
                            try:
                                message = socket.recv(
                                    timeout=min(1, max(0.01, deadline - time.monotonic()))
                                )
                            except TimeoutError:
                                if time.monotonic() - last_message > 30:
                                    raise OSError("No BTCUSDT aggregate messages for 30 seconds")
                                if time.monotonic() - last_flush >= flush_seconds:
                                    flush()
                                continue
                            if isinstance(message, bytes):
                                message = message.decode("utf-8")
                            received = now()
                            messages.append(
                                {"message": message, "received_at": received.isoformat()}
                            )
                            report["messages"] += 1
                            row = parse_trade(
                                json.loads(message),
                                "pending",
                                len(messages) - 1,
                                received,
                                websocket=True,
                            )
                            event = sequence.observe(row)
                            if report["messages"] == 1 and fresh_start and previous:
                                first = previous["agg_trade_id"] + 1
                                last = row["agg_trade_id"] - 1
                                if first <= last:
                                    report["uncollected_before_start"] = {
                                        "first_id": first,
                                        "last_id": last,
                                        "reason": "explicit_fresh_start_outside_capture_scope",
                                        "repaired": False,
                                    }
                            report["last_id"] = row["agg_trade_id"]
                            report["last_trade_time"] = row["timestamp"]
                            report["last_received_at"] = received
                            last_message, failures = time.monotonic(), 0
                            report["duplicate_messages"] += bool(event.get("duplicate"))
                            report["out_of_order_messages"] += bool(event.get("out_of_order"))
                            if event.get("gap"):
                                report["detected_gaps"] += 1
                                flush()
                                first, last = event["gap"]
                                try:
                                    recovery = recover(first, last)
                                    recovered = recovery["complete"]
                                    item = {
                                        "first_id": first,
                                        "last_id": last,
                                        "collection_id": recovery["id"],
                                        "complete": recovered,
                                    }
                                except Exception as exc:
                                    recovered = False
                                    item = {
                                        "first_id": first,
                                        "last_id": last,
                                        "complete": False,
                                        "error": str(exc),
                                    }
                                report["repaired_gaps" if recovered else "unrepaired_gaps"] += 1
                                report["recent_recoveries"] = (
                                    report["recent_recoveries"] + [item]
                                )[-50:]
                                # Every recovery attempt also has its own durable collection report.
                            if (
                                len(messages) >= 5000
                                or time.monotonic() - last_flush >= flush_seconds
                            ):
                                flush()
                        flush()
                        if time.monotonic() < deadline:
                            report["rotations"] += 1
                except (ConnectionClosed, WebSocketException, OSError) as exc:
                    # Flush received data even after an abrupt socket closure.
                    flush()
                    report["connection_errors"] += 1
                    report["recent_errors"] = (
                        report["recent_errors"] + [{"at": now(), "error": str(exc)}]
                    )[-20:]
                    failures += 1
                    if isinstance(exc, InvalidStatus) and exc.response.status_code in {418, 429}:
                        rate_limited(store, "aggtrade", exc.response)
                    if failures >= 5:
                        raise DataError("Five consecutive WebSocket connection failures") from exc
                    if time.monotonic() < deadline:
                        time.sleep(min(2 ** min(failures, 5), max(0, deadline - time.monotonic())))
            report["complete"] = (
                bool(report["messages"])
                and not report["unrepaired_gaps"]
                and not report["out_of_order_messages"]
            )
            report = save_collection(store, report, "complete" if report["complete"] else "partial")
            return report
        except (Exception, KeyboardInterrupt) as exc:
            # Invalid input is still retained verbatim by persist_messages before parsing fails.
            try:
                flush()
            except Exception as flush_exc:
                report["flush_error"] = str(flush_exc)
            report.update(complete=False, error=f"{type(exc).__name__}: {exc}")
            report = save_collection(
                store, report, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            )
            raise
        finally:
            atomic_write(store.root / "state/aggtrade-latest.json", json_bytes(report))


def stream_status(store):
    latest = store.db.execute(
        "SELECT * FROM event_collections WHERE dataset=? AND kind='websocket' ORDER BY started_at DESC LIMIT 1",
        (DATASET,),
    ).fetchone()
    if latest is None:
        return {"state": "empty", "healthy": False}
    report = json.loads(latest["report"])
    received = report.get("last_received_at")
    age = (now() - utc(received)).total_seconds() if received else None
    historical = [
        r["id"]
        for r in store.db.execute(
            "SELECT id,report FROM event_collections WHERE dataset=? AND kind='websocket' AND id<>?",
            (DATASET, latest["id"]),
        )
        if any(
            json.loads(r["report"]).get(key)
            for key in ("unrepaired_gaps", "out_of_order_messages", "error", "flush_error")
        )
    ]
    healthy = (
        latest["state"] == "running"
        and age is not None
        and 0 <= age < 30
        and not report.get("unrepaired_gaps")
        and not report.get("out_of_order_messages")
    )
    return {
        "state": latest["state"],
        "healthy": healthy,
        "last_receive_age_seconds": age,
        "report": report,
        "historical_collections_with_recorded_errors": historical,
        "note": "A completed bounded capture is not a running service. Inspect aggtrade-audit for stored data continuity.",
    }
