"""Bounded public depth capture; each reconnect starts a new snapshot epoch."""

import fcntl
import gzip
import json
import shutil
import time
from collections import deque
from contextlib import contextmanager
from datetime import timedelta

from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.sync.client import connect

from . import depth
from .backup import reserve_bytes
from .binance import Fetcher, SourceError
from .depth_refresh import RefreshChecks, headroom
from .depth_refresh import metadata as refresh_metadata
from .domain import DataError, WriterBusy, json_bytes, now, sha256, utc
from .oi_probe import retry_delay
from .store import atomic_write
from .stream_guard import check_cooldown, rate_limited

LAGGED_RETRY_POLICY = "same_socket_backoff_v1"
LAGGED_RETRY_DELAYS = (1, 2, 4, 8, 10)
LAGGED_RETRY_REQUESTS = 8
LAGGED_RETRY_WINDOW = 60


class LaggedRetry:
    """Monotonic retry deadlines; the rolling budget survives successful resyncs."""

    def __init__(self):
        self.requests = deque()
        self.reset()

    def reset(self):
        self.failures = 0
        self.failed_at = self.retry_at = None

    def ready_at(self, current):
        while self.requests and self.requests[0] <= current - LAGGED_RETRY_WINDOW:
            self.requests.popleft()
        if self.retry_at is None:
            return None
        return max(
            self.retry_at,
            self.requests[0] + LAGGED_RETRY_WINDOW
            if len(self.requests) >= LAGGED_RETRY_REQUESTS
            else self.retry_at,
        )

    def failed(self, current):
        delay = LAGGED_RETRY_DELAYS[min(self.failures, len(LAGGED_RETRY_DELAYS) - 1)]
        self.failures += 1
        self.failed_at, self.retry_at = current, current + delay
        due = self.ready_at(current)
        return {
            "consecutive_failure": self.failures,
            "delay_seconds": delay,
            "budget_delay_seconds": due - self.retry_at,
            "not_before": now() + timedelta(seconds=due - current),
        }

    def claim(self, current):
        due = self.ready_at(current)
        if due is None or current < due:
            raise DataError("Lagged snapshot retry requested before its deadline/budget")
        self.requests.append(current)
        self.retry_at = None
        return current - self.failed_at


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


def buffer_before_anchor(store, socket, collection_id, epoch, deadline):
    """Require an actual first event before REST; retain it if REST later fails."""
    buffered = deque()
    until = min(deadline, time.monotonic() + 2)
    try:
        while time.monotonic() < until:
            try:
                message = socket.recv(timeout=min(1, max(0.01, until - time.monotonic())))
            except TimeoutError:
                continue
            received = now()
            if isinstance(message, bytes):
                message = message.decode("utf-8")
            buffered.append({"message": message, "received_at": received.isoformat()})
            # One received event establishes buffering before REST without a
            # fixed warm-up delay. Later events remain in the client's bounded queue.
            break
    finally:
        if buffered:
            blob = json_bytes(list(buffered))
            with store.writer(timeout=60):
                store.put_raw(
                    gzip.compress(blob, mtime=0),
                    depth.WS,
                    "depth_bootstrap",
                    utc(buffered[0]["received_at"]),
                    {
                        "collection_id": collection_id,
                        "epoch": epoch,
                        "compression": "gzip",
                        "uncompressed_sha256": sha256(blob),
                        "messages": len(buffered),
                        "purpose": "pre_anchor_receipts; also journaled after successful anchor",
                        "timestamp_semantics": "application_recv_return_not_wire_arrival",
                    },
                )
    if not buffered:
        raise OSError("No depth messages received before initial snapshot")
    return buffered


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
    history_policy=depth.HISTORY_POLICY,
):
    if history_policy not in (None, depth.HISTORY_POLICY):
        raise DataError("Unknown depth history policy")
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
                    **(
                        {
                            "history_policy": history_policy,
                            "history_limits": dict(depth.HISTORY_LIMITS),
                        }
                        if history_policy is not None
                        else {}
                    ),
                    "messages": 0,
                    "persisted_messages": 0,
                    "snapshots": 0,
                    "connections": 0,
                    "connection_errors": 0,
                    "sync_losses": 0,
                    "coverage_resets": 0,
                    "coverage_top_shortfalls": 0,
                    "coverage_refresh": refresh_metadata(),
                    "coverage_check_requests": 0,
                    "periodic_check_requests": 0,
                    "recent_coverage_checks": [],
                    "coverage_reset_policy": "same_socket_new_epoch_v1",
                    "bootstrap_policy": "buffer_before_anchor_v1",
                    "bootstrap_timeout_seconds": 2,
                    "bootstrap_messages": 0,
                    "lagged_snapshot_policy": "retain_unsynchronized_updates_v1",
                    "lagged_retry_policy": LAGGED_RETRY_POLICY,
                    "lagged_retry_delays_seconds": list(LAGGED_RETRY_DELAYS),
                    "lagged_retry_budget": {
                        "max_requests": LAGGED_RETRY_REQUESTS,
                        "window_seconds": LAGGED_RETRY_WINDOW,
                        "scope": "retry anchor calls within this capture; survives resync/reconnect",
                    },
                    "lagged_retry_requests": 0,
                    "lagged_snapshots": 0,
                    "unreconstructed_messages": 0,
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
            lagged_retry = LaggedRetry()
            refresh_checks = RefreshChecks()

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

            def get_snapshot(kind, *, already_flushed=False):
                nonlocal sequence
                if shutil.disk_usage(store.root).free < minimum:
                    raise DataError("Depth capture stopped: free space below reserve")
                if not already_flushed:
                    flush()
                row = snapshot(store, fetcher, report["id"], epoch_report["epoch"], sequence, kind)
                sequence += 1
                report["snapshots"] += 1
                session.take(row)
                report["synchronized"] = session.book.synced
                report["last_update_id"] = session.book.last
                if session.book.synced:
                    lagged_retry.reset()

            def retry_lagged_anchor(stop_at):
                nonlocal session, epoch_report, epoch_started, last_check
                current = time.monotonic()
                due = lagged_retry.ready_at(current)
                if not session.error or due is None or current < due or current >= stop_at:
                    return
                # Preserve every received update before changing epochs. Recheck
                # after a potentially blocking flush; never begin a retry once
                # the session or physical connection is due to end.
                flush()
                current = time.monotonic()
                if current >= stop_at:
                    return
                waited = lagged_retry.claim(current)
                requested = now()
                previous_epoch = epoch_report["epoch"]
                epoch_report["lagged_retry"].update(
                    requested_at=requested, actual_wait_seconds=waited
                )
                epoch_report.update(ended_at=requested)
                session = depth.new_session(report, "same_socket_after_lagged_snapshot", session)
                epoch_report = {
                    "epoch": len(report["epochs"]),
                    "started_at": requested,
                    "reason": "same_socket_after_lagged_snapshot",
                    "retry_of_epoch": previous_epoch,
                }
                report["epochs"].append(epoch_report)
                report["lagged_retry_requests"] += 1
                epoch_started = current
                get_snapshot("anchor", already_flushed=True)
                last_check = time.monotonic()

            def renew_coverage(stop_at, *, incomplete=False):
                nonlocal session, epoch_report, epoch_started, last_check
                reason = "incomplete_top" if incomplete else "coverage_exhausted"
                report["sync_losses"] += 1
                report["coverage_resets"] += 1
                report["coverage_top_shortfalls"] += int(incomplete)
                report["synchronized"] = False
                epoch_report.update(ended_at=now(), end_reason=reason)
                if incomplete:
                    # Keep the state machine's true result: IDs may be aligned
                    # even though the requested top is no longer known. Do not
                    # invent a successful REST comparison or a replay exception.
                    epoch_report["coverage_shortfall"] = headroom(session.book, top)
                flush()
                if report["coverage_resets"] >= 10:
                    raise DataError("Ten depth coverage resets")
                if time.monotonic() >= stop_at:
                    return False
                session = depth.new_session(report, "same_socket_after_" + reason, session)
                epoch_report = {
                    "epoch": len(report["epochs"]),
                    "started_at": now(),
                    "reason": "same_socket_after_" + reason,
                }
                report["epochs"].append(epoch_report)
                epoch_started = time.monotonic()
                get_snapshot("anchor", already_flushed=True)
                last_check = time.monotonic()
                return True

            def check_coverage(stop_at):
                nonlocal last_check
                if not session.book.synced or session.pending:
                    return
                decision = refresh_checks.decision(
                    session.book,
                    top,
                    current=time.monotonic(),
                    last_request=last_check,
                    periodic_seconds=verify_seconds,
                )
                if decision is None:
                    return
                # Persistence can block. Re-evaluate both the quota and deadline
                # after flushing, before making a new external request.
                flush()
                current = time.monotonic()
                if current >= stop_at:
                    return
                decision = refresh_checks.decision(
                    session.book,
                    top,
                    current=current,
                    last_request=last_check,
                    periodic_seconds=verify_seconds,
                )
                if decision is None:
                    return
                refresh_checks.claim(decision, current)
                counter = (
                    "coverage_check_requests"
                    if decision["reason"] == "headroom"
                    else "periodic_check_requests"
                )
                report[counter] += 1
                record = {
                    "epoch": epoch_report["epoch"],
                    "sequence": sequence,
                    "requested_at": now(),
                    **decision,
                }
                report["recent_coverage_checks"] = (report["recent_coverage_checks"] + [record])[
                    -32:
                ]
                get_snapshot("check", already_flushed=True)
                last_check = time.monotonic()

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
                            connected = epoch_started = last_message = last_check = time.monotonic()
                            report["connections"] += 1
                            # This is a new failure episode, but not a fresh
                            # rolling request budget.
                            lagged_retry.reset()
                            session = depth.new_session(report, reason)
                            epoch_report = {
                                "epoch": len(report["epochs"]),
                                "started_at": now(),
                                "reason": reason,
                            }
                            report["epochs"].append(epoch_report)
                            if shutil.disk_usage(store.root).free < minimum:
                                raise DataError("Depth capture stopped: free space below reserve")
                            prefetched = buffer_before_anchor(
                                store, socket, report["id"], epoch_report["epoch"], deadline
                            )
                            report["bootstrap_messages"] += len(prefetched)
                            get_snapshot("anchor")
                            epoch_started = last_check = time.monotonic()
                            while prefetched or time.monotonic() < min(
                                deadline, connected + rotate_seconds
                            ):
                                retry_lagged_anchor(min(deadline, connected + rotate_seconds))
                                if not prefetched and time.monotonic() >= min(
                                    deadline, connected + rotate_seconds
                                ):
                                    break
                                try:
                                    if prefetched:
                                        incoming = prefetched.popleft()
                                        message, received = (
                                            incoming["message"],
                                            utc(incoming["received_at"]),
                                        )
                                    else:
                                        message = socket.recv(
                                            timeout=min(1, max(0.01, deadline - time.monotonic()))
                                        )
                                        received = now()
                                except TimeoutError:
                                    if time.monotonic() - last_message > 10:
                                        raise OSError("No BTCUSDT depth messages for 10 seconds")
                                    if time.monotonic() - last_flush >= flush_seconds:
                                        flush()
                                    continue
                                if isinstance(message, bytes):
                                    message = message.decode("utf-8")
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
                                last_message, failures = time.monotonic(), 0
                                if session.error:
                                    # A lagged anchor cannot reconstruct these rows. Keep
                                    # their exact journal while waiting for a fresh anchor.
                                    report["unreconstructed_messages"] += 1
                                    session.remember(row)
                                    if (
                                        len(messages) >= 500
                                        or buffered_bytes >= 4 * 1024**2
                                        or time.monotonic() - last_flush >= flush_seconds
                                    ):
                                        flush()
                                    retry_lagged_anchor(min(deadline, connected + rotate_seconds))
                                    continue
                                try:
                                    session.take(row)
                                except depth.SnapshotBehind as exc:
                                    session.error = str(exc)
                                    report["sync_losses"] += 1
                                    report["lagged_snapshots"] += 1
                                    report["unreconstructed_messages"] += 1
                                    report["synchronized"] = False
                                    epoch_report.update(end_reason="lagged_snapshot")
                                    epoch_report["lagged_retry"] = lagged_retry.failed(
                                        time.monotonic()
                                    )
                                    last_check = time.monotonic()
                                    flush()
                                    continue
                                except depth.CoverageExhausted as exc:
                                    # The ID stream is intact, but its bounded snapshot no
                                    # longer covers the market. Keep the socket and queued
                                    # updates; preserve the failed epoch before re-anchoring.
                                    session.error = str(exc)
                                    if not renew_coverage(
                                        min(deadline, connected + rotate_seconds)
                                    ):
                                        break
                                    continue
                                if session.book.synced and (
                                    len(session.book.bids) < top or len(session.book.asks) < top
                                ):
                                    if not renew_coverage(
                                        min(deadline, connected + rotate_seconds), incomplete=True
                                    ):
                                        break
                                    continue
                                report["synchronized"] = session.book.synced
                                report["last_update_id"] = session.book.last
                                if session.book.synced:
                                    lagged_retry.reset()
                                if (
                                    not session.book.synced
                                    and time.monotonic() - epoch_started > 15
                                ):
                                    raise depth.SyncLost(
                                        "Snapshot bridge not found within 15 seconds"
                                    )
                                if (
                                    len(messages) >= 500
                                    or buffered_bytes >= 4 * 1024**2
                                    or time.monotonic() - last_flush >= flush_seconds
                                ):
                                    flush()
                                check_coverage(min(deadline, connected + rotate_seconds))
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
