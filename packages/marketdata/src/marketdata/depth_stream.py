"""Bounded public depth capture; each reconnect starts a new snapshot epoch."""

import fcntl
import gzip
import json
import shutil
import time
from contextlib import contextmanager
from datetime import timedelta

from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.sync.client import connect

from . import depth
from .backup import reserve_bytes
from .binance import Fetcher, SourceError
from .domain import DataError, WriterBusy, json_bytes, now, sha256, utc
from .oi_probe import retry_delay
from .store import atomic_write
from .stream_guard import check_cooldown, rate_limited


@contextmanager
def stream_lock(store):
    with (store.root / "state/depth-stream.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WriterBusy("Another depth stream is running") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def snapshot(store, fetcher, collection_id, epoch, sequence, kind):
    started = now()
    cooldown = store.root / "state/depth-rest-cooldown.json"
    if cooldown.exists() and started < utc(json.loads(cooldown.read_bytes())["until"]):
        raise DataError("Depth REST cooldown is active; retry later")
    try:
        response = fetcher.fetch(depth.REST, weight=20)
    except SourceError as exc:
        with store.writer(timeout=60):
            store.put_raw(
                exc.body,
                exc.url or depth.REST,
                "depth_rest_error",
                metadata={
                    "collection_id": collection_id,
                    "epoch": epoch,
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
    with store.writer(timeout=60):
        source_id = store.put_raw(
            response.body,
            response.url,
            "depth_snapshot",
            response.received_at,
            {
                "collection_id": collection_id,
                "epoch": epoch,
                "sequence": sequence,
                "kind": kind,
                "request_started_at": started,
                "received_at": response.received_at,
                "headers": response.headers,
            },
        )
    row = depth.parse(
        json.loads(response.body), kind, epoch, sequence, source_id, 0, response.received_at
    )
    depth.publish(store, collection_id, [row], source_id)
    return row


def persist_messages(store, collection_id, messages):
    if not messages:
        return 0
    blob = json_bytes(messages)
    with store.writer(timeout=60):
        source_id = store.put_raw(
            gzip.compress(blob, mtime=0),
            depth.WS,
            "depth_websocket",
            messages[0]["received_at"],
            {
                "collection_id": collection_id,
                "compression": "gzip",
                "uncompressed_bytes": len(blob),
                "uncompressed_sha256": sha256(blob),
                "timestamp_semantics": "application_recv_return_not_wire_arrival",
            },
        )
    rows = [
        depth.parse(
            json.loads(m["message"]),
            "update",
            m["epoch"],
            m["sequence"],
            source_id,
            i,
            m["received_at"],
        )
        for i, m in enumerate(messages)
    ]
    depth.publish(store, collection_id, rows, source_id)
    return len(rows)


def capture(
    store,
    *,
    seconds=180,
    flush_seconds=5,
    rotate_seconds=3600,
    verify_seconds=30,
    top=20,
    min_free_gib=10,
    connector=connect,
    fetcher=None,
    progress=None,
):
    if not (
        1 <= seconds <= 3600
        and 1 <= flush_seconds <= 30
        and 10 <= rotate_seconds <= 82800
        and 10 <= verify_seconds <= 3600
        and 1 <= top <= 1000
    ):
        raise DataError(
            "Depth pilot: seconds 1..3600, flush 1..30, rotation 10..82800, verification 10..3600, top 1..1000"
        )
    minimum = reserve_bytes(min_free_gib)
    own = fetcher is None
    fetcher = fetcher or Fetcher(retries=0, max_bytes=2_000_000)
    try:
        with stream_lock(store):
            check_cooldown(store, "depth")
            with store.writer(timeout=60), store.db:
                for previous in store.db.execute(
                    "SELECT * FROM event_collections WHERE dataset=? AND state='running'",
                    (depth.DATASET,),
                ).fetchall():
                    interrupted = {
                        **json.loads(previous["report"]),
                        "complete": False,
                        "state": "interrupted",
                        "updated_at": now(),
                        "error": "Previous process exited without a final report",
                    }
                    store.db.execute(
                        "UPDATE event_collections SET state=?,updated_at=?,report=? WHERE id=?",
                        (
                            "interrupted",
                            interrupted["updated_at"].isoformat(),
                            json_bytes(interrupted).decode(),
                            previous["id"],
                        ),
                    )
            report = depth.start_collection(
                store,
                {
                    "seconds_requested": seconds,
                    "flush_seconds": flush_seconds,
                    "rotate_seconds": rotate_seconds,
                    "verify_seconds": verify_seconds,
                    "top": top,
                    "refresh_from_checks": True,
                    "messages": 0,
                    "persisted_messages": 0,
                    "snapshots": 0,
                    "connections": 0,
                    "connection_errors": 0,
                    "sync_losses": 0,
                    "epochs": [],
                    "errors": [],
                    "synchronized": False,
                    "complete": False,
                    "scope": "BTCUSDT USD-M non-RPI depth; known snapshot price range only",
                    "boundary_semantics": "new_snapshot_on_every_connection; no_historical_gap_repair",
                },
            )
            messages, sequence, session, epoch_report = [], 0, None, None
            buffered_bytes = 0
            start = last_flush = time.monotonic()
            deadline, failures, reason = start + seconds, 0, "initial"

            def flush():
                nonlocal last_flush, buffered_bytes
                if shutil.disk_usage(store.root).free < minimum:
                    raise DataError("Depth capture stopped: free space below reserve")
                report["persisted_messages"] += persist_messages(store, report["id"], messages)
                messages.clear()
                buffered_bytes = 0
                if epoch_report is not None:
                    epoch_report["final"] = session.summary()
                depth.save_collection(store, report, "running")
                last_flush = time.monotonic()
                if progress:
                    progress(
                        {
                            "phase": "depth_stream",
                            "messages": report["messages"],
                            "epochs": len(report["epochs"]),
                            "synchronized": report["synchronized"],
                            "sync_losses": report["sync_losses"],
                        }
                    )

            def get_snapshot(kind):
                nonlocal sequence
                if shutil.disk_usage(store.root).free < minimum:
                    raise DataError("Depth capture stopped: free space below reserve")
                flush()
                row = snapshot(store, fetcher, report["id"], epoch_report["epoch"], sequence, kind)
                sequence += 1
                report["snapshots"] += 1
                session.take(row)

            try:
                while time.monotonic() < deadline:
                    epoch_report = None
                    session = None
                    report["synchronized"] = False
                    try:
                        # The sync client starts a bounded receiving queue on connect, before REST.
                        with connector(
                            depth.WS,
                            open_timeout=10,
                            close_timeout=2,
                            ping_interval=20,
                            ping_timeout=20,
                            max_size=1_000_000,
                            max_queue=128,
                        ) as socket:
                            connected = last_message = last_check = time.monotonic()
                            report["connections"] += 1
                            session = depth.Session(top, refresh_from_checks=True)
                            epoch_report = {
                                "epoch": len(report["epochs"]),
                                "started_at": now(),
                                "reason": reason,
                            }
                            report["epochs"].append(epoch_report)
                            get_snapshot("anchor")
                            while time.monotonic() < min(deadline, connected + rotate_seconds):
                                try:
                                    message = socket.recv(
                                        timeout=min(1, max(0.01, deadline - time.monotonic()))
                                    )
                                except TimeoutError:
                                    if time.monotonic() - last_message > 10:
                                        raise OSError("No BTCUSDT depth messages for 10 seconds")
                                    if time.monotonic() - last_flush >= flush_seconds:
                                        flush()
                                    continue
                                if isinstance(message, bytes):
                                    message = message.decode("utf-8")
                                received = now()
                                item = {
                                    "message": message,
                                    "received_at": received.isoformat(),
                                    "sequence": sequence,
                                    "epoch": epoch_report["epoch"],
                                }
                                messages.append(item)
                                buffered_bytes += len(message.encode("utf-8"))
                                sequence += 1
                                report["messages"] += 1
                                report["last_received_at"] = received
                                row = depth.parse(
                                    json.loads(message),
                                    "update",
                                    item["epoch"],
                                    item["sequence"],
                                    "pending",
                                    len(messages) - 1,
                                    received,
                                )
                                session.take(row)
                                report["synchronized"] = session.book.synced
                                report["last_update_id"] = session.book.last
                                last_message, failures = time.monotonic(), 0
                                if not session.book.synced and time.monotonic() - connected > 15:
                                    raise depth.SyncLost(
                                        "Snapshot bridge not found within 15 seconds"
                                    )
                                if (
                                    len(messages) >= 500
                                    or buffered_bytes >= 4 * 1024**2
                                    or time.monotonic() - last_flush >= flush_seconds
                                ):
                                    flush()
                                if (
                                    session.book.synced
                                    and not session.pending
                                    and (
                                        time.monotonic() - last_check >= verify_seconds
                                        or (
                                            (
                                                len(session.book.bids)
                                                < min(
                                                    max(100, top * 5),
                                                    session.book.snapshot_bid_levels // 2,
                                                )
                                                or len(session.book.asks)
                                                < min(
                                                    max(100, top * 5),
                                                    session.book.snapshot_ask_levels // 2,
                                                )
                                            )
                                            and time.monotonic() - last_check >= 5
                                        )
                                    )
                                ):
                                    get_snapshot("check")
                                    last_check = time.monotonic()
                            reason = "planned_rotation"
                            epoch_report.update(
                                ended_at=now(),
                                end_reason="rotation"
                                if time.monotonic() < deadline
                                else "duration",
                            )
                            flush()
                    except depth.SyncLost as exc:
                        session.error = str(exc)
                        report["sync_losses"] += 1
                        report["synchronized"] = False
                        epoch_report.update(ended_at=now(), end_reason="sync_loss")
                        flush()
                        reason = "resync_after_loss"
                        if report["sync_losses"] >= 10:
                            raise DataError("Ten depth synchronization failures") from exc
                        time.sleep(min(1, max(0, deadline - time.monotonic())))
                    except (ConnectionClosed, WebSocketException, OSError) as exc:
                        report["connection_errors"] += 1
                        report["synchronized"] = False
                        report["errors"] = (report["errors"] + [{"at": now(), "error": str(exc)}])[
                            -20:
                        ]
                        if epoch_report is not None:
                            epoch_report.update(ended_at=now(), end_reason="disconnect")
                        flush()
                        failures += 1
                        if isinstance(exc, InvalidStatus) and exc.response.status_code in {
                            418,
                            429,
                        }:
                            rate_limited(store, "depth", exc.response)
                        if failures >= 5:
                            raise DataError("Five consecutive depth connection failures") from exc
                        reason = "reconnect_after_disconnect"
                        time.sleep(min(2 ** min(failures, 5), max(0, deadline - time.monotonic())))
                report["complete"] = (
                    bool(report["messages"])
                    and not report["sync_losses"]
                    and not report["connection_errors"]
                    and all(
                        e.get("final", {}).get("book", {}).get("synced", False)
                        for e in report["epochs"]
                    )
                )
                report["synchronized"] = False
                depth.save_collection(
                    store, report, "complete" if report["complete"] else "partial"
                )
                return report
            except (Exception, KeyboardInterrupt) as exc:
                if epoch_report is not None:
                    epoch_report.update(
                        ended_at=now(),
                        end_reason="interrupted" if isinstance(exc, KeyboardInterrupt) else "error",
                    )
                try:
                    flush()
                except Exception as flush_exc:
                    report["flush_error"] = str(flush_exc)
                report.update(
                    complete=False, synchronized=False, error=f"{type(exc).__name__}: {exc}"
                )
                depth.save_collection(
                    store, report, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
                )
                raise
    finally:
        if own:
            fetcher.close()
