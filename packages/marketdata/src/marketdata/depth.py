"""BTCUSDT USD-M depth: exact quantities, bounded known coverage, journal replay."""

import json
from collections import deque
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from .domain import DataError, from_epoch, json_bytes, now, number, sha256, utc
from .schema import TIME, VALUE
from .store import atomic_write

DATASET = "book_depth"
INSTRUMENT = "binance:linear_perpetual:BTCUSDT"
WS = "wss://fstream.binance.com/public/ws/btcusdt@depth@100ms"
REST = "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=1000"
HISTORY_POLICY = "bounded_same_socket_v1"
HISTORY_LIMITS = {"events": 128, "levels": 50000, "serialized_bytes": 8 * 1024**2}
SAME_SOCKET_REASONS = {"same_socket_after_coverage_exhausted", "same_socket_after_lagged_snapshot"}
LEVEL = pa.list_(pa.struct([pa.field("price", VALUE, False), pa.field("quantity", VALUE, False)]))
SCHEMA = pa.schema(
    [
        pa.field("instrument_id", pa.string(), False),
        pa.field("sequence", pa.int64(), False),
        pa.field("epoch", pa.int32(), False),
        pa.field("kind", pa.string(), False),
        pa.field("timestamp", TIME, False),
        pa.field("event_time", TIME, False),
        pa.field("received_at", TIME, False),
        pa.field("first_update_id", pa.int64()),
        pa.field("final_update_id", pa.int64(), False),
        pa.field("previous_update_id", pa.int64()),
        pa.field("bids", LEVEL, False),
        pa.field("asks", LEVEL, False),
        pa.field("source_id", pa.string(), False),
        pa.field("source_row", pa.int64(), False),
    ],
    metadata={b"dataset": b"book_depth", b"schema_version": b"1"},
)


def integer(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise DataError("Expected nonnegative int64 depth ID/time")
    return value


def levels(raw, *, snapshot=False, bid=False):
    if not isinstance(raw, list) or len(raw) > (1000 if snapshot else 20000):
        raise DataError("Invalid or oversized depth levels")
    rows, seen = [], set()
    for item in raw:
        if not isinstance(item, list) or len(item) != 2:
            raise DataError("Expected [price, quantity] depth level")
        price = number(item[0], positive=True)
        quantity = number(item[1], nonnegative=True, positive=snapshot)
        if price in seen:
            raise DataError("Duplicate depth price within an event")
        seen.add(price)
        rows.append({"price": price, "quantity": quantity})
    if snapshot and (not rows or [r["price"] for r in rows] != sorted(seen, reverse=bid)):
        raise DataError("Snapshot must have nonempty sorted sides")
    return rows


def parse(raw, kind, epoch, sequence, source_id, source_row, received_at):
    if kind not in {"anchor", "check", "update"} or not isinstance(raw, dict):
        raise DataError("Invalid depth event")
    snapshot = kind != "update"
    if not snapshot and (raw.get("e") != "depthUpdate" or raw.get("s") != "BTCUSDT"):
        raise DataError("Wrong depth event type/symbol")
    if "st" in raw and (type(raw["st"]) is not int or raw["st"] != 1):
        raise DataError("Expected USD-M depth")
    first = None if snapshot else integer(raw["U"])
    last = integer(raw["lastUpdateId"] if snapshot else raw["u"])
    previous = None if snapshot else integer(raw["pu"])
    if not snapshot and (first > last or previous >= last):
        raise DataError("Invalid depth update ID range")
    return {
        "instrument_id": INSTRUMENT,
        "sequence": sequence,
        "epoch": epoch,
        "kind": kind,
        "timestamp": from_epoch(integer(raw["T"])),
        "event_time": from_epoch(integer(raw["E"])),
        "received_at": utc(received_at),
        "first_update_id": first,
        "final_update_id": last,
        "previous_update_id": previous,
        "bids": levels(raw["bids" if snapshot else "b"], snapshot=snapshot, bid=True),
        "asks": levels(raw["asks" if snapshot else "a"], snapshot=snapshot),
        "source_id": source_id,
        "source_row": source_row,
    }


class SyncLost(DataError):
    """A fresh snapshot is needed; missing depth history cannot be backfilled."""


class CoverageExhausted(SyncLost):
    """The stream can remain connected while bounded snapshot coverage is renewed."""


class SnapshotBehind(SyncLost):
    """The anchor predates the first available update; do not invent a bridge."""


class Book:
    def __init__(self, snapshot):
        self.bids = {r["price"]: r["quantity"] for r in snapshot["bids"]}
        self.asks = {r["price"]: r["quantity"] for r in snapshot["asks"]}
        self.bid_floor, self.ask_ceiling = min(self.bids), max(self.asks)
        self.snapshot_bid_levels, self.snapshot_ask_levels = len(self.bids), len(self.asks)
        self.last = snapshot["final_update_id"]
        self.timestamp = snapshot["timestamp"]
        self.synced = False
        self.last_signature = None
        self.validate(self.bids, self.asks)

    @staticmethod
    def validate(bids, asks):
        if not bids or not asks:
            raise CoverageExhausted("Known snapshot price range exhausted")
        if max(bids) >= min(asks):
            raise SyncLost("Crossed or locked reconstructed order book")
        if len(bids) + len(asks) > 100000:
            raise SyncLost("Known order book level budget exceeded")

    def apply(self, row):
        first, last = row["first_update_id"], row["final_update_id"]
        signature = sha256(
            json_bytes(
                {
                    k: row[k]
                    for k in (
                        "first_update_id",
                        "final_update_id",
                        "previous_update_id",
                        "timestamp",
                        "event_time",
                        "bids",
                        "asks",
                    )
                }
            )
        )
        if not self.synced:
            if last < self.last:
                return "stale"
            if first > self.last:
                raise SnapshotBehind("Snapshot bridge missing: expected U <= lastUpdateId <= u")
        else:
            if last == self.last and signature == self.last_signature:
                return "duplicate"
            if last <= self.last:
                raise SyncLost("Conflicting duplicate or reversed depth update")
            if row["previous_update_id"] != self.last:
                raise SyncLost("Depth chain gap: pu differs from previous u")
            if row["timestamp"] < self.timestamp:
                raise SyncLost("Depth transaction time reversed")
        bids, asks = self.bids.copy(), self.asks.copy()
        for target, rows, inside in (
            (bids, row["bids"], lambda p: p >= self.bid_floor),
            (asks, row["asks"], lambda p: p <= self.ask_ceiling),
        ):
            for level in rows:
                p, q = level["price"], level["quantity"]
                if not inside(p):
                    continue
                if q == 0:
                    target.pop(p, None)
                else:
                    target[p] = q
        self.validate(bids, asks)
        self.bids, self.asks = bids, asks
        self.last, self.timestamp = last, row["timestamp"]
        self.last_signature, self.synced = signature, True
        return "applied"

    def view(self, top=20):
        bids, asks = sorted(self.bids.items(), reverse=True), sorted(self.asks.items())
        return {
            "last_update_id": self.last,
            "timestamp": self.timestamp,
            "synced": self.synced,
            "bid_floor": self.bid_floor,
            "ask_ceiling": self.ask_ceiling,
            "known_bid_levels": len(bids),
            "known_ask_levels": len(asks),
            "top_requested": top,
            "top_complete": len(bids) >= top and len(asks) >= top,
            "bids": bids[:top],
            "asks": asks[:top],
            "book_sha256": sha256(json_bytes({"bids": bids, "asks": asks, "u": self.last})),
        }


class History:
    """Bounded, already-received updates from one physical WebSocket connection."""

    def __init__(self):
        self.events = deque()
        self.levels = 0
        self.serialized_bytes = 0

    def append(self, row):
        if row["kind"] != "update":
            raise DataError("Only depth updates belong in recent history")
        # Exclude persistence provenance: live rows are still pending, while
        # replay rows already have source hashes. Keep byte accounting identical.
        saved = {
            k: row[k]
            for k in (
                "sequence",
                "epoch",
                "kind",
                "timestamp",
                "event_time",
                "received_at",
                "first_update_id",
                "final_update_id",
                "previous_update_id",
            )
        }
        for side in ("bids", "asks"):
            saved[side] = [dict(level) for level in row[side]]
        count = len(saved["bids"]) + len(saved["asks"])
        size = len(json_bytes(saved))
        self.events.append((saved, count, size))
        self.levels += count
        self.serialized_bytes += size
        while (
            len(self.events) > HISTORY_LIMITS["events"]
            or self.levels > HISTORY_LIMITS["levels"]
            or self.serialized_bytes > HISTORY_LIMITS["serialized_bytes"]
        ):
            _, removed_count, removed_size = self.events.popleft()
            self.levels -= removed_count
            self.serialized_bytes -= removed_size

    def align(self, snapshot):
        """Build privately; a failed bridge must never partly replace the live book."""
        candidate = Book(snapshot)
        proof = {
            "events": len(self.events),
            "levels": self.levels,
            "serialized_bytes": self.serialized_bytes,
            "applied": 0,
            "first_sequence": None,
            "last_sequence": None,
            "aligned": False,
        }
        try:
            for row, _, _ in self.events:
                if not candidate.synced and row["final_update_id"] < candidate.last:
                    continue
                if candidate.apply(row) == "applied":
                    proof["applied"] += 1
                    if proof["first_sequence"] is None:
                        proof["first_sequence"] = row["sequence"]
                    proof["last_sequence"] = row["sequence"]
        except SyncLost as exc:
            proof["reason"] = str(exc)
            return None, proof
        if not candidate.synced:
            proof["reason"] = "No snapshot bridge retained in recent history"
            return None, proof
        proof.update(aligned=True, last_update_id=candidate.last)
        return candidate, proof


def new_session(report, reason, previous=None):
    """One policy dispatch for capture and replay; absent policy preserves old journals."""
    policy = report.get("history_policy")
    if policy not in (None, HISTORY_POLICY):
        raise DataError("Unknown depth history policy")
    history = None
    if policy is not None:
        if report.get("history_limits") != HISTORY_LIMITS:
            raise DataError("Depth history limits differ from the recorded policy")
        if reason in SAME_SOCKET_REASONS:
            if previous is None or previous.history is None:
                raise DataError("Same-socket depth epoch has no preceding history")
            history = previous.history
        else:
            history = History()
    return Session(
        report["top"], refresh_from_checks=report.get("refresh_from_checks", False), history=history
    )


class Session:
    """Same deterministic state machine is used live and for offline replay."""

    def __init__(self, top=20, *, refresh_from_checks=False, history=None):
        self.book = None
        self.pending = None
        self.top = top
        self.counts = {"applied": 0, "stale": 0, "duplicate": 0}
        self.checks = []
        self.error = None
        self.refresh_from_checks = refresh_from_checks
        self.refreshes = 0
        self.history = history
        self.anchor_history = None

    def compare(self, candidate, sequence, *, history_alignment=None):
        a, b = self.book.view(self.top), candidate.view(self.top)
        comparable = (
            a["last_update_id"] == b["last_update_id"] and a["top_complete"] and b["top_complete"]
        )
        self.checks.append(
            {
                "snapshot_sequence": sequence,
                "last_update_id": a["last_update_id"],
                "top": self.top,
                "comparable": comparable,
                "equal": comparable and a["bids"] == b["bids"] and a["asks"] == b["asks"],
                **(
                    {"history_alignment": history_alignment}
                    if history_alignment is not None
                    else {}
                ),
            }
        )
        if comparable and not self.checks[-1]["equal"]:
            raise SyncLost("Independent REST snapshot disagrees at the same update ID")
        if self.refresh_from_checks:
            refreshed = self.checks[-1]["equal"]
            self.checks[-1]["refreshed"] = refreshed
            if refreshed:
                # The new snapshot has reached the exact live update ID. Keep the
                # stream chain/duplicate marker while replacing its known price range.
                candidate.synced = True
                candidate.last_signature = self.book.last_signature
                candidate.timestamp = self.book.timestamp
                self.book = candidate
                self.refreshes += 1

    def remember(self, row):
        if self.history is not None:
            self.history.append(row)

    def take(self, row):
        if row["kind"] == "anchor":
            if self.book is not None:
                raise DataError("Multiple anchors in one depth epoch")
            self.book = Book(row)
            if self.history is not None:
                candidate, self.anchor_history = self.history.align(row)
                if candidate is not None:
                    self.book = candidate
        elif self.book is None:
            raise DataError("Depth journal has no preceding anchor")
        elif row["kind"] == "check":
            if self.pending:
                raise DataError("Previous depth verification has not aligned")
            candidate = Book(row)
            if candidate.last < self.book.last:
                alignment = None
                if self.history is not None:
                    aligned, alignment = self.history.align(row)
                    if aligned is not None and aligned.last == self.book.last:
                        self.compare(aligned, row["sequence"], history_alignment=alignment)
                        return
                self.checks.append(
                    {
                        "snapshot_sequence": row["sequence"],
                        "comparable": False,
                        "equal": False,
                        "reason": "snapshot_behind_live_book",
                        **({"history_alignment": alignment} if alignment is not None else {}),
                    }
                )
            elif candidate.last == self.book.last and self.book.synced:
                self.compare(candidate, row["sequence"])
            else:
                self.pending = (candidate, row["sequence"])
        else:
            # Retain the event even if applying it exhausts coverage or proves
            # the anchor is behind; the same socket may reuse it for a new anchor.
            self.remember(row)
            result = self.book.apply(row)
            self.counts[result] += 1
            if result == "applied" and self.pending:
                candidate, sequence = self.pending
                try:
                    applied = candidate.apply(row)
                except SyncLost as exc:
                    self.checks.append(
                        {
                            "snapshot_sequence": sequence,
                            "comparable": False,
                            "equal": False,
                            "reason": str(exc),
                        }
                    )
                    self.pending = None
                else:
                    if applied == "applied":
                        self.pending = None
                        self.compare(candidate, sequence)

    def summary(self):
        return {
            "counts": self.counts,
            "book": self.book.view(self.top) if self.book else None,
            "checks": self.checks,
            "pending_check_sequence": self.pending[1] if self.pending else None,
            "error": self.error,
            **({"refreshes": self.refreshes} if self.refresh_from_checks else {}),
            **({"anchor_history": self.anchor_history} if self.history is not None else {}),
        }


def start_collection(store, details):
    report = {
        "id": uuid4().hex,
        "dataset": DATASET,
        "kind": "websocket",
        "started_at": now(),
        **details,
    }
    with store.writer(timeout=60), store.db:
        store.db.execute(
            "INSERT INTO event_collections VALUES(?,?,?,?,?,?,?)",
            (
                report["id"],
                DATASET,
                "websocket",
                report["started_at"].isoformat(),
                report["started_at"].isoformat(),
                "running",
                json_bytes(report).decode(),
            ),
        )
    return report


def save_collection(store, report, state):
    report.update(state=state, updated_at=now())
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
    atomic_write(store.root / "state/depth-latest.json", json_bytes(report))


def publish(store, collection_id, rows, source_id):
    if not rows:
        return
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), sink, compression="zstd")
    blob = sink.getvalue().to_pybytes()
    identity = sha256(blob)
    relative = f"data/canonical/{DATASET}/{identity[:2]}/{identity}.parquet"
    with store.writer(timeout=60):
        path = store.root / relative
        if store.file_exists(relative):
            if sha256(store.file_bytes(relative)) != identity:
                raise DataError("Corrupted existing depth segment")
        else:
            atomic_write(path, blob)
        with store.db:
            store.db.execute(
                "INSERT OR IGNORE INTO event_segments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    collection_id,
                    DATASET,
                    INSTRUMENT,
                    "journal",
                    relative,
                    identity,
                    source_id,
                    min(r["timestamp"] for r in rows).isoformat(),
                    max(r["timestamp"] for r in rows).isoformat(),
                    min(r["sequence"] for r in rows),
                    max(r["sequence"] for r in rows),
                    len(rows),
                ),
            )


def collection_record(store, identity=None):
    row = store.db.execute(
        "SELECT * FROM event_collections WHERE dataset=? "
        + ("AND id=?" if identity else "ORDER BY started_at DESC LIMIT 1"),
        (DATASET, identity) if identity else (DATASET,),
    ).fetchone()
    if row is None:
        raise DataError("Unknown depth collection")
    return row


def replay(store, identity=None, *, max_rows=100000):
    record = collection_record(store, identity)
    report = json.loads(record["report"])
    epoch_reports = {e["epoch"]: e for e in report["epochs"]}
    records = list(
        store.db.execute(
            "SELECT * FROM event_segments WHERE dataset=? AND collection_id=? ORDER BY first_id",
            (DATASET, record["id"]),
        )
    )
    if sum(r["row_count"] for r in records) > max_rows:
        raise DataError("Depth replay row budget exceeded")
    sessions = {}
    row_count = 0
    for segment in records:
        # Keep at most one immutable segment in memory, not the whole capture.
        for batch in store._revision_table(segment).to_batches(max_chunksize=256):
            for row in batch.to_pylist():
                if row["sequence"] != row_count:
                    raise DataError("Depth journal sequence is missing or duplicated")
                row_count += 1
                epoch = row["epoch"]
                if epoch not in sessions:
                    if report.get("history_policy") is not None and epoch != len(sessions):
                        raise DataError("Depth history epochs are not consecutive")
                    sessions[epoch] = new_session(
                        report,
                        epoch_reports[epoch].get("reason", "initial"),
                        sessions.get(epoch - 1),
                    )
                session = sessions[epoch]
                if session.error:
                    if (
                        report.get("lagged_snapshot_policy") == "retain_unsynchronized_updates_v1"
                        and isinstance(getattr(session, "failure", None), SnapshotBehind)
                        and row["kind"] == "update"
                    ):
                        session.remember(row)
                        continue
                    raise DataError("Depth updates continued after an invalid epoch")
                try:
                    session.take(row)
                except (DataError, KeyError) as exc:
                    session.error = str(exc)
                    session.failure = exc
    expected = {e["epoch"]: e.get("final") for e in report["epochs"]}
    summaries = {e: s.summary() for e, s in sessions.items()}
    matches = json_bytes(summaries) == json_bytes(expected)
    checks = [c for s in sessions.values() for c in s.checks]
    pending_checks = sum(s.pending is not None for s in sessions.values())
    valid = (
        bool(row_count)
        and matches
        and report.get("complete", False)
        and all(s.book and s.book.synced and not s.error for s in sessions.values())
    )
    return {
        "collection_id": record["id"],
        "state": record["state"],
        "rows": row_count,
        "segments": [r["id"] for r in records],
        "epochs": summaries,
        "replay_matches_live": matches,
        "valid": valid,
        "continuous": valid and len(sessions) == 1 and not report.get("connection_errors"),
        "verification_checks": len(checks),
        "verification_equal": sum(bool(c["equal"]) for c in checks),
        "verification_uncomparable": sum(not c["comparable"] for c in checks),
        "verification_pending": pending_checks,
        "verification_complete": bool(checks)
        and not pending_checks
        and all(c["equal"] for c in checks),
        "note": "Per-epoch known price range only; reconnect intervals and time-window edges are not reconstructed.",
    }


def status(store):
    try:
        record = collection_record(store)
    except DataError:
        return {"state": "empty", "healthy": False}
    report = json.loads(record["report"])
    received = report.get("last_received_at")
    age = (now() - utc(received)).total_seconds() if received else None
    return {
        "state": record["state"],
        "healthy": record["state"] == "running"
        and age is not None
        and 0 <= age < 10
        and report.get("synchronized", False)
        and not report.get("sync_losses"),
        "last_receive_age_seconds": age,
        "recent_incomplete_collection_ids": [
            r["id"]
            for r in store.db.execute(
                "SELECT id FROM event_collections WHERE dataset=? AND id<>? "
                "AND state IN ('failed','partial','interrupted') ORDER BY started_at DESC LIMIT 100",
                (DATASET, record["id"]),
            )
        ],
        "report": report,
        "note": "A completed pilot is not a running depth service. Epoch boundaries do not have historical repair.",
    }
