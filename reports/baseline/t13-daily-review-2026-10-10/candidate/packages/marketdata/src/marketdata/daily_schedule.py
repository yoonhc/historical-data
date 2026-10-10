"""Durable, per-key/day daily acquisition. This module never changes services or clocks."""

import argparse
import fcntl
import json
import math
import signal
import sqlite3
import tomllib
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from .binance import Fetcher, SourceError, archive_url
from .domain import DAY, ConflictError, DataError, Key, WriterBusy, json_bytes, now, sha256, utc
from .operations import SyncPolicy, inventory, midnight, register_target, scope_start, window_end
from .pipeline import Collector
from .schema import economic_row, row_key
from .store import Store, atomic_write

VERSION = 1
KST = ZoneInfo("Asia/Seoul")
TERMINAL = {"complete", "needs_review", "missed_observation"}
TABLES = {
    "daily_tasks",
    "daily_attempts",
    "daily_cooldowns",
    "daily_runs",
    "daily_probe_targets",
    "daily_reviews",
}


@dataclass(frozen=True)
class Schedule:
    noon: str = "12:20"
    evening: str = "18:00"
    retry_slots: tuple = ("20:00", "22:00")
    transient_delay_seconds: int = 300
    transient_attempts: int = 3
    validation_attempts: int = 2
    cooldown_seconds: int = 900
    old_missing_days: int = 3
    max_tasks: int = 32

    def __post_init__(self):
        if not self.retry_slots:
            raise DataError("At least one delayed retry slot is required")
        for value in (self.noon, self.evening, *self.retry_slots):
            try:
                if datetime.strptime(value, "%H:%M").strftime("%H:%M") != value:
                    raise ValueError()
            except (ValueError, TypeError) as exc:
                raise DataError("Schedule expects HH:MM KST") from exc
        if not self.noon < self.evening < min(self.retry_slots):
            raise DataError("Expected noon < evening < retries")
        if tuple(sorted(set(self.retry_slots))) != tuple(self.retry_slots):
            raise DataError("Retry slots must be distinct and ascending")
        for name in (
            "transient_delay_seconds",
            "transient_attempts",
            "validation_attempts",
            "cooldown_seconds",
            "old_missing_days",
            "max_tasks",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise DataError(f"Invalid schedule {name}")
        if self.max_tasks > 256:
            raise DataError("max_tasks exceeds bounded run limit")

    def first(self, key):
        return (
            self.noon
            if key.market == "spot" or key.dataset in {"funding_events", "instrument_metadata"}
            else self.evening
        )

    def at(self, local_day, clock):
        return utc(datetime.fromisoformat(f"{local_day}T{clock}").replace(tzinfo=KST))

    def eligible(self, key, data_day):
        return self.at((utc(data_day) + DAY).date().isoformat(), self.first(key))

    def later_slot(self, key, current, *, old=False):
        current = utc(current)
        local_day = current.astimezone(KST).date()
        if not old:
            for clock in (self.first(key), *self.retry_slots):
                value = self.at(local_day.isoformat(), clock)
                if value > current:
                    return value
        return self.at((local_day + DAY).isoformat(), self.first(key))


def load_config(path):
    data = tomllib.loads(Path(path).read_text())
    keys = [Key(**entry) for entry in data["datasets"]]
    if not keys or len({k.id for k in keys}) != len(keys):
        raise DataError("Expected distinct configured keys")
    if any(k.dataset == "open_interest" and k.series != "archive" for k in keys):
        raise DataError("Live REST OI is outside daily scheduler scope")
    policy = SyncPolicy(**data.get("operations", {}))
    schedule = Schedule(**data.get("schedule", {}))
    return keys, policy, schedule


def fingerprint(rows, key):
    return sha256(
        json_bytes([economic_row(r) for r in sorted(rows, key=lambda r: row_key(r, key))])
    )


class Ledger:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def exclusive(self):
        with (self.store.root / "state/daily-scheduler.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WriterBusy("Another daily scheduler is running") from exc
            try:
                with self.store.writer(timeout=5):
                    self.store.db.executescript("""
                        CREATE TABLE IF NOT EXISTS daily_tasks (
                            id TEXT PRIMARY KEY, record TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS daily_attempts (
                            id TEXT PRIMARY KEY, record TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS daily_cooldowns (
                            id TEXT PRIMARY KEY, record TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS daily_runs (
                            id TEXT PRIMARY KEY, record TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS daily_probe_targets (
                            id TEXT PRIMARY KEY, record TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS daily_reviews (
                            id TEXT PRIMARY KEY, record TEXT NOT NULL);
                    """)
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def rows(self, table):
        if table not in TABLES:
            raise DataError("Invalid ledger table")
        return [json.loads(r[0]) for r in self.store.db.execute(f"SELECT record FROM {table}")]

    def save(self, table, record):
        self.save_many([(table, record)])

    def save_many(self, records):
        with self.store.writer(timeout=5), self.store.db:
            for table, record in records:
                if table not in TABLES:
                    raise DataError("Invalid ledger table")
                self.store.db.execute(
                    f"INSERT INTO {table} VALUES(?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record",
                    (record["id"], json_bytes(record).decode()),
                )


class AuditedClient:
    """Record intent before each HTTP attempt; 429/418 never get an immediate retry."""

    def __init__(self, client, scheduler, attempt):
        self.client, self.scheduler, self.attempt = client, scheduler, attempt

    @contextmanager
    def stream(self, method, url):
        scheduler, attempt = self.scheduler, self.attempt
        scheduler.check_cooldown(url)
        record = {
            "url": str(url),
            "started_at": scheduler.clock(),
            "status": None,
            "outcome": "request_outcome_unknown",
        }
        attempt["requests"].append(record)
        scheduler.ledger.save("daily_attempts", attempt)
        try:
            with self.client.stream(method, url) as response:
                record.update(
                    status=response.status_code,
                    headers=dict(response.headers),
                    outcome="response_headers_received",
                    received_at=scheduler.clock(),
                )
                scheduler.ledger.save("daily_attempts", attempt)
                if response.status_code in {429, 418}:
                    scheduler.cooldown(url, response.headers)
                    body = b""
                    for chunk in response.iter_bytes():
                        body += chunk[: max(0, 8192 - len(body))]
                        if len(body) >= 8192:
                            break
                    raise SourceError(
                        "Server cooldown",
                        response.status_code,
                        body,
                        str(url),
                        dict(response.headers),
                    )
                yield response
                record["outcome"] = "response_consumed"
        except httpx.TransportError as exc:
            record.update(outcome="transport_error", error=str(exc))
            raise
        finally:
            scheduler.ledger.save("daily_attempts", attempt)


class RecordingCollector(Collector):
    def __init__(self, scheduler, attempt, fetcher):
        super().__init__(scheduler.store, fetcher, cooperative=True)
        self.scheduler, self.attempt = scheduler, attempt

    def _save_response(self, response, kind):
        identity = super()._save_response(response, kind)
        self.attempt["inputs"].append(
            {"id": identity, "url": response.url, "kind": kind, "received_at": response.received_at}
        )
        self.scheduler.ledger.save("daily_attempts", self.attempt)
        return identity

    def _publish(self, key, batch, start, end, source_id, **options):
        lower, upper = start, end
        if key.dataset == "instrument_metadata":
            lower = min(r["timestamp"] for r in batch.rows)
            upper = max(r["timestamp"] for r in batch.rows) + timedelta(microseconds=1)
        self.attempt["publish_intent"] = {
            "key": key.asdict(),
            "start": start,
            "end": end,
            "source_id": source_id,
            "row_start": lower,
            "row_end": upper,
            "row_count": len(batch.rows),
            "economic_sha256": fingerprint(batch.rows, key),
        }
        self.scheduler.ledger.save("daily_attempts", self.attempt)
        result = super()._publish(key, batch, start, end, source_id, **options)
        self.scheduler.fault("after_publish", self.attempt)
        return result


class Scheduler:
    def __init__(
        self,
        store,
        keys,
        policy,
        schedule=None,
        *,
        client=None,
        clock=now,
        sleep=None,
        fault=None,
        readiness=None,
    ):
        self.store, self.keys, self.policy = store, keys, policy
        self.schedule = schedule or Schedule()
        self.ledger = Ledger(store)
        self.clock = clock
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_client = client is None
        self.sleep = sleep
        self.fault = fault or (lambda phase, attempt: None)
        self.readiness = readiness
        self.request_windows = {}
        self.config_hash = sha256(
            json_bytes(
                {
                    "keys": [k.asdict() for k in keys],
                    "policy": asdict(policy),
                    "schedule": asdict(self.schedule),
                    "version": VERSION,
                }
            )
        )

    def close(self):
        if self.owns_client:
            self.client.close()

    def check_cooldown(self, url):
        host = urlsplit(str(url)).hostname
        for row in self.ledger.rows("daily_cooldowns"):
            if row["id"] == host and utc(row["until"]) > self.clock():
                raise SourceError(
                    "Persisted host cooldown",
                    status=429,
                    url=str(url),
                    headers={
                        "retry-after": str((utc(row["until"]) - self.clock()).total_seconds())
                    },
                )

    def cooldown(self, url, headers):
        current = self.clock()
        delay = self.schedule.cooldown_seconds
        value = headers.get("retry-after")
        if value:
            try:
                delay = max(delay, float(value))
            except ValueError:
                try:
                    delay = max(
                        delay, (utc(parsedate_to_datetime(value)) - current).total_seconds()
                    )
                except (ValueError, TypeError, OverflowError):
                    pass
        if not math.isfinite(delay) or delay > 86400 * 365:
            raise DataError("Invalid or excessive Retry-After; operator review required")
        until = current + timedelta(seconds=delay)
        host = urlsplit(str(url)).hostname
        for old in self.ledger.rows("daily_cooldowns"):
            if old["id"] == host:
                until = max(until, utc(old["until"]))
        self.ledger.save(
            "daily_cooldowns",
            {"id": host, "until": until, "observed_at": current, "headers": dict(headers)},
        )
        return until

    def proof(self, key, day, intent=None, *, writer_held=False):
        """Only this task's raw/query/canonical evidence is checked, not the whole store."""
        start, end = utc(day), utc(day) + DAY
        if intent:
            start, end = utc(intent["start"]), utc(intent["end"])
        with nullcontext() if writer_held else self.store.writer(timeout=5):
            coverage = inventory(self.store, key, start, end)
            if key.dataset != "instrument_metadata" and not coverage["complete"]:
                return None
            if coverage["blocked_days"]:
                return None
            queries = [
                dict(r)
                for r in self.store.db.execute(
                    "SELECT * FROM queries WHERE key_id=? AND start<=? AND end>=?",
                    (key.id, start.isoformat(), end.isoformat()),
                )
            ]
            if intent:
                queries = [q for q in queries if q["source_id"] == intent["source_id"]]
            if not queries:
                return None
            records = self.store.revision_list(key, start, end)
            rows = []
            for r in records:
                rows.extend(self.store._revision_rows(r))
            if intent:
                lo, hi = utc(intent["row_start"]), utc(intent["row_end"])
                selected = [r for r in rows if lo <= r["timestamp"] < hi]
                if (
                    len(selected) != intent["row_count"]
                    or fingerprint(selected, key) != intent["economic_sha256"]
                ):
                    return None
            source_ids = sorted({q["source_id"] for q in queries})
            for identity in source_ids:
                self.store.raw(identity)
            return {
                "revision_ids": [r["id"] for r in records],
                "file_sha256": [r["file_sha"] for r in records],
                "query_ids": [q["id"] for q in queries],
                "source_ids": source_ids,
                "checked_at": self.clock(),
                "publish_intent": intent,
            }

    def task(self, key, day, purpose, cycle, due):
        identity = sha256(json_bytes([VERSION, key.asdict(), day, purpose, cycle]))
        return {
            "id": identity,
            "version": VERSION,
            "key": key.asdict(),
            "day": day,
            "purpose": purpose,
            "cycle": cycle,
            "state": "planned",
            "next_at": due,
            "first_eligible_at": due,
            "reason": "scheduled",
            "attempts": [],
            "publication_checks": 0,
            "transient_failures": 0,
            "validation_failures": 0,
            "review_required": False,
            "proof": None,
            "config_hash": self.config_hash,
        }

    def retained_proof(self, task):
        """Check immutable catalogue references; full hashes were checked at completion.

        Re-reading every retained ZIP/Parquet on each timer wakeup is deliberately
        left to the existing integrity/backup path. Recovery rechecks full content.
        """
        proof = task.get("proof")
        if not proof or not proof["query_ids"] or not proof["source_ids"]:
            return False
        key = Key(**task["key"])
        with self.store.writer(timeout=5):
            for identity in proof["query_ids"]:
                row = self.store.db.execute(
                    "SELECT key_id,source_id FROM queries WHERE id=?", (identity,)
                ).fetchone()
                if (
                    row is None
                    or row["key_id"] != key.id
                    or row["source_id"] not in proof["source_ids"]
                ):
                    return False
            for identity, digest in zip(proof["revision_ids"], proof["file_sha256"], strict=True):
                row = self.store.db.execute(
                    "SELECT key_id,file_sha,path FROM revisions WHERE id=?", (identity,)
                ).fetchone()
                if (
                    row is None
                    or row["key_id"] != key.id
                    or row["file_sha"] != digest
                    or not self.store.file_exists(row["path"])
                ):
                    return False
            for identity in proof["source_ids"]:
                row = self.store.db.execute(
                    "SELECT path FROM artifacts WHERE id=?", (identity,)
                ).fetchone()
                if row is None or not self.store.file_exists(row["path"]):
                    return False
        return True

    def plan(self, current):
        existing = {r["id"]: r for r in self.ledger.rows("daily_tasks")}
        cycle = current.astimezone(KST).date().isoformat()
        for old in existing.values():
            if old["key"] not in [k.asdict() for k in self.keys] and old["state"] not in TERMINAL:
                old.update(
                    state="needs_review",
                    reason="pending_target_removed_from_configuration",
                    next_at=None,
                    review_required=True,
                )
                self.ledger.save("daily_tasks", old)
            if old["state"] == "complete" and not self.retained_proof(old):
                old.update(
                    state="needs_review",
                    reason="stored_proof_reference_missing_or_changed",
                    next_at=None,
                    review_required=True,
                )
                self.ledger.save("daily_tasks", old)

        def add(task):
            if task["id"] not in existing:
                self.ledger.save("daily_tasks", task)
                existing[task["id"]] = task
            return existing[task["id"]]

        for key in self.keys:
            if key.dataset == "instrument_metadata":
                for old in existing.values():
                    if (
                        old["key"] == key.asdict()
                        and old["cycle"] < cycle
                        and old["state"] not in TERMINAL
                    ):
                        old.update(
                            state="missed_observation",
                            reason="historical_observation_unrecoverable",
                            next_at=None,
                            review_required=True,
                        )
                        self.ledger.save("daily_tasks", old)
                add(
                    self.task(
                        key, cycle, "observe", cycle, self.schedule.at(cycle, self.schedule.noon)
                    )
                )
                continue
            end = midnight(current)
            start = scope_start(self.store, key, self.policy, end)
            if start >= end:
                continue
            with self.store.writer(timeout=5):
                register_target(self.store, key, self.policy, start, current)
                coverage = inventory(self.store, key, start, end, cache=True)
            for entry in coverage["days"]:
                day = entry["day"]
                due = self.schedule.eligible(key, day)
                acquisition = self.task(key, day, "acquire", None, due)
                if (
                    acquisition["id"] not in existing
                    and not entry["missing_ranges"]
                    and not entry["blocked"]
                ):
                    proof = self.proof(key, day)
                    if proof:
                        acquisition.update(
                            state="complete",
                            proof=proof,
                            next_at=None,
                            reason="existing_verified_catalog",
                        )
                acquired = add(acquisition)
                if (
                    acquired["state"] not in TERMINAL
                    and not entry["missing_ranges"]
                    and not entry["blocked"]
                ):
                    # An older CLI may have filled a pending acquisition after rollback.
                    # This settles acquisition only, not a later revision-check cycle.
                    proof = self.proof(key, day)
                    if proof:
                        self.finish(acquired, proof)
                        existing[acquired["id"]] = acquired
                if acquired["state"] == "complete" and (
                    entry["missing_ranges"] or entry["blocked"]
                ):
                    acquired.update(
                        state="needs_review",
                        reason="current_catalog_coverage_incomplete_or_blocked",
                        next_at=None,
                        review_required=True,
                    )
                    self.ledger.save("daily_tasks", acquired)
                if utc(day) >= end - timedelta(days=self.policy.refresh_days):
                    refresh_due = max(due, self.schedule.at(cycle, self.schedule.first(key)))
                    add(self.task(key, day, "refresh", cycle, refresh_due))

    def finish(self, task, proof, attempt=None):
        if task["review_required"]:
            task["review_history"] = {"reason": task["reason"], "resolved_at": self.clock()}
        task.update(
            state="complete",
            reason="verified_storage",
            proof=proof,
            next_at=None,
            completed_at=self.clock(),
            review_required=False,
        )
        self.ledger.save("daily_tasks", task)
        # A successful new fetch satisfies due refreshes of the exact same target.
        if attempt:
            for other in self.ledger.rows("daily_tasks"):
                if (
                    other["id"] != task["id"]
                    and other["key"] == task["key"]
                    and other["day"] == task["day"]
                    and other["state"] not in TERMINAL
                    and utc(other["first_eligible_at"]) <= utc(attempt["started_at"])
                ):
                    other.update(
                        state="complete",
                        reason="covered_by_same_verified_attempt",
                        proof=proof,
                        covered_by=attempt["id"],
                        next_at=None,
                        completed_at=self.clock(),
                        review_required=False,
                    )
                    self.ledger.save("daily_tasks", other)

    def recover(self):
        tasks = {t["id"]: t for t in self.ledger.rows("daily_tasks")}
        for attempt in self.ledger.rows("daily_attempts"):
            if attempt["state"] != "running":
                continue
            task = tasks[attempt["task_id"]]
            intent = attempt.get("publish_intent")
            proof = self.proof(Key(**task["key"]), task["day"], intent) if intent else None
            if proof:
                for item in attempt["inputs"]:
                    self.store.raw(item["id"])
                attempt.update(state="recovered_complete", proof=proof, finished_at=self.clock())
                self.finish(task, proof, attempt)
                self.ledger.save("daily_attempts", attempt)
            else:
                attempt.update(state="interrupted", finished_at=self.clock())
                for request in attempt["requests"]:
                    if request["status"] in {429, 418}:
                        self.cooldown(request["url"], request.get("headers", {}))
                task["transient_failures"] += 1
                grant = task.get("manual_retry")
                exhausted = (
                    grant["remaining"] == 0
                    if grant
                    else task["transient_failures"] >= self.schedule.transient_attempts
                )
                task.update(
                    state="retry_wait",
                    reason="interrupted_before_verified_storage",
                    next_at=max(utc(task["next_at"] or self.clock()), self.clock()),
                )
                if exhausted:
                    task.update(state="needs_review", next_at=None, review_required=True)
                self.ledger.save_many([("daily_tasks", task), ("daily_attempts", attempt)])

    def failure(self, task, attempt, exc):
        current, key = self.clock(), Key(**task["key"])
        reason, state, due = str(exc), "needs_review", None
        if isinstance(exc, WriterBusy):
            state, due = "deferred_lock", current + timedelta(seconds=60)
        elif isinstance(exc, SourceError):
            expected = None
            if key.dataset not in {"funding_events", "instrument_metadata"}:
                expected = archive_url(key, utc(task["day"]), False)
            if exc.status == 404 and expected and exc.url in {expected, expected + ".CHECKSUM"}:
                task["publication_checks"] += 1
                old = current - utc(task["first_eligible_at"]) >= timedelta(
                    days=self.schedule.old_missing_days
                )
                state, due = "waiting_source", self.schedule.later_slot(key, current, old=old)
                task["review_required"] |= old
            elif exc.status in {429, 418}:
                state, due = "cooldown", self.cooldown(exc.url, exc.headers)
            elif exc.status is None or exc.status in {408, 500, 502, 503, 504}:
                task["transient_failures"] += 1
                if (
                    task.get("manual_retry")
                    or task["transient_failures"] < self.schedule.transient_attempts
                ):
                    state, due = (
                        "retry_wait",
                        current + timedelta(seconds=self.schedule.transient_delay_seconds),
                    )
        elif isinstance(exc, DataError) and not isinstance(exc, ConflictError):
            task["validation_failures"] += 1
            if (
                task.get("manual_retry")
                or task["validation_failures"] < self.schedule.validation_attempts
            ):
                state, due = "validation_retry", self.schedule.later_slot(key, current)
        if task.get("manual_retry", {}).get("remaining") == 0:
            state, due = "needs_review", None
        task.update(
            state=state,
            next_at=due,
            reason=reason,
            review_required=task["review_required"] or state == "needs_review",
        )
        attempt.update(
            state=state,
            error={
                "type": type(exc).__name__,
                "message": str(exc),
                "status": getattr(exc, "status", None),
            },
            finished_at=current,
        )
        self.ledger.save_many([("daily_tasks", task), ("daily_attempts", attempt)])

    def execute_task(self, task):
        key, current = Key(**task["key"]), self.clock()
        if self.readiness:
            ready = self.readiness()
            if not ready["ready"]:
                task.update(
                    state="deferred_clock",
                    reason=ready.get("reason", "clock_not_ready"),
                    next_at=current + timedelta(seconds=60),
                )
                self.ledger.save("daily_tasks", task)
                return
        grant = task.get("manual_retry")
        if grant:
            if grant["remaining"] <= 0:
                task.update(
                    state="needs_review",
                    next_at=None,
                    review_required=True,
                    reason="approved_additional_attempts_exhausted",
                )
                self.ledger.save("daily_tasks", task)
                return
            # Approval is not a cooldown exemption. Do not consume a new grant
            # before the very first possible request is allowed.
            from .binance import rest_url

            url = (
                rest_url(key, utc(task["day"]), utc(task["day"]) + DAY)[0]
                if key.dataset in {"funding_events", "instrument_metadata"}
                else archive_url(key, utc(task["day"]), False)
            )
            host = urlsplit(url).hostname
            held = [
                utc(r["until"])
                for r in self.ledger.rows("daily_cooldowns")
                if r["id"] == host and utc(r["until"]) > current
            ]
            if held:
                task.update(state="cooldown", next_at=max(held), reason="persisted_host_cooldown")
                self.ledger.save("daily_tasks", task)
                return
        attempt = {
            "id": uuid.uuid4().hex,
            "task_id": task["id"],
            "state": "running",
            "started_at": current,
            "requests": [],
            "inputs": [],
            "config_hash": self.config_hash,
        }
        task["attempts"].append(attempt["id"])
        if grant:
            grant["remaining"] -= 1
            attempt["manual_request_id"] = grant["request_id"]
        task.update(
            state="running",
            next_at=current + timedelta(seconds=self.schedule.transient_delay_seconds),
        )
        self.ledger.save_many([("daily_tasks", task), ("daily_attempts", attempt)])
        options = {"sleep": self.sleep} if self.sleep else {}
        fetcher = Fetcher(client=AuditedClient(self.client, self, attempt), **options)
        fetcher.windows = self.request_windows
        collector = RecordingCollector(self, attempt, fetcher)
        try:
            if task["purpose"] == "observe":
                result = collector.rest(key, current - timedelta(seconds=1), current)
            else:
                start, end = utc(task["day"]), utc(task["day"]) + DAY
                if key.dataset == "funding_events":
                    result = collector.rest(key, start, end)
                else:
                    result = collector.archive(key, start, end, monthly=False, refresh=True)
            if any(r["state"] == "waiting_source" for r in result):
                responses = [r for r in attempt["requests"] if r["status"] == 404]
                raise SourceError(
                    "Expected archive not available",
                    404,
                    url=responses[-1]["url"] if responses else "",
                )
            intent = attempt.get("publish_intent")
            proof = self.proof(key, task["day"], intent) if intent else None
            if not proof:
                raise DataError("Task has no complete verified publication proof")
            self.fault("before_complete", attempt)
            attempt.update(state="complete", proof=proof, result=result, finished_at=self.clock())
            # Save task first: recovery can finish a still-running attempt from the same proof.
            self.finish(task, proof, attempt)
            self.ledger.save("daily_attempts", attempt)
        except (DataError, SourceError, OSError, ValueError, KeyError) as exc:
            self.failure(task, attempt, exc)

    def sync_status(self, tasks, report):
        """Append a truthful per-key summary for the unchanged legacy status CLI.

        Preserve previous runs; a timer wakeup without a changed task state does
        not invent another successful acquisition or refresh its completion time.
        """
        current = self.clock()
        cycle = current.astimezone(KST).date().isoformat()
        with self.store.writer(timeout=5), self.store.db:
            for key in self.keys:
                selected = [
                    t
                    for t in tasks
                    if t["key"] == key.asdict()
                    and (
                        t["purpose"] == "acquire"
                        or t["cycle"] == cycle
                        or t["state"] not in TERMINAL
                    )
                ]
                view = [
                    {k: t[k] for k in ("id", "state", "next_at", "reason", "proof")}
                    for t in selected
                ]
                fingerprint = sha256(json_bytes(view))
                latest = self.store.db.execute(
                    "SELECT report FROM sync_runs WHERE key_id=? ORDER BY started_at DESC,rowid DESC LIMIT 1",
                    (key.id,),
                ).fetchone()
                if latest and json.loads(latest[0]).get("scheduler_fingerprint") == fingerprint:
                    continue
                complete = bool(selected) and all(t["state"] == "complete" for t in selected)
                errors = [
                    {"task_id": t["id"], "message": t["reason"]}
                    for t in selected
                    if t["state"] == "needs_review"
                ]
                state = "complete" if complete else "failed" if errors else "partial"
                end = window_end(key, current)
                start = scope_start(self.store, key, self.policy, end)
                identity = uuid.uuid4().hex
                summary = {
                    "run_id": identity,
                    "key": key.id,
                    "state": state,
                    "complete": complete,
                    "errors": errors,
                    "results": view,
                    "scheduler_cycle": cycle,
                    "scheduler_run_id": report["id"],
                    "scheduler_fingerprint": fingerprint,
                    "semantics": "task_state_summary_not_whole_daily_reexecution",
                }
                self.store.db.execute(
                    "INSERT INTO sync_runs VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        identity,
                        key.id,
                        start.isoformat(),
                        end.isoformat(),
                        current.isoformat(),
                        current.isoformat(),
                        current.isoformat(),
                        state,
                        json_bytes(summary).decode(),
                    ),
                )

    def run(self):
        with self.ledger.exclusive():
            current = self.clock()
            self.recover()
            self.plan(current)
            processed, touched = [], set()
            # Acquire before refresh; one acquisition can discharge both purposes.
            tasks = sorted(
                self.ledger.rows("daily_tasks"),
                key=lambda t: (
                    t["purpose"] == "refresh",
                    t["next_at"] or "9999",
                    t["day"],
                    t["id"],
                ),
            )
            for task in tasks:
                if len(processed) >= self.schedule.max_tasks:
                    break
                task = next(t for t in self.ledger.rows("daily_tasks") if t["id"] == task["id"])
                if (
                    task["state"] in TERMINAL
                    or not task["next_at"]
                    or utc(task["next_at"]) > self.clock()
                ):
                    continue
                target = (json_bytes(task["key"]), task["day"])
                if target in touched:
                    continue
                peers = [
                    p
                    for p in self.ledger.rows("daily_tasks")
                    if p["key"] == task["key"] and p["day"] == task["day"] and p["id"] != task["id"]
                ]
                if task["purpose"] == "refresh" and any(
                    p["state"] != "complete"
                    and (
                        p["purpose"] == "acquire"
                        or p["state"] == "needs_review"
                        or (p["purpose"] == "refresh" and p["cycle"] < task["cycle"])
                    )
                    for p in peers
                ):
                    continue
                touched.add(target)
                self.execute_task(task)
                processed.append(task["id"])
            report = summarize(self.ledger.rows("daily_tasks"), self.clock())
            report.update(
                id=uuid.uuid4().hex,
                started_at=current,
                finished_at=self.clock(),
                processed=processed,
                run_outcome="tasks_processed" if processed else "no_due_tasks",
                config_hash=self.config_hash,
                runner_ok=True,
            )
            self.ledger.save("daily_runs", report)
            self.sync_status(self.ledger.rows("daily_tasks"), report)
            return report


def summarize(tasks, current):
    current = utc(current)
    cycle = current.astimezone(KST).date().isoformat()
    counts = {}
    for task in tasks:
        counts[task["state"]] = counts.get(task["state"], 0) + 1
    obligations = [
        t
        for t in tasks
        if t["purpose"] == "acquire" or t["cycle"] == cycle or t["state"] not in TERMINAL
    ]
    return {
        "version": VERSION,
        "as_of": current,
        "state_counts": counts,
        "data_complete": bool(obligations) and all(t["state"] == "complete" for t in obligations),
        "review_required": any(t["review_required"] for t in tasks),
        "pending": [
            {
                k: t[k]
                for k in (
                    "id",
                    "key",
                    "day",
                    "purpose",
                    "cycle",
                    "state",
                    "next_at",
                    "reason",
                    "review_required",
                )
            }
            for t in tasks
            if t["state"] != "complete"
        ],
        "completed_tasks": sum(t["state"] == "complete" for t in tasks),
    }


def read_status(root):
    path = Path(root).resolve() / "state/catalog.sqlite"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        found = db.execute("SELECT 1 FROM sqlite_master WHERE name='daily_tasks'").fetchone()
        tasks = (
            [json.loads(r[0]) for r in db.execute("SELECT record FROM daily_tasks")]
            if found
            else []
        )
    return summarize(tasks, now())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "status", "probe"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--probe-config", type=Path)
    args = parser.parse_args()
    if args.action == "status":
        print(json_bytes(read_status(args.root)).decode())
        return
    if args.config is None:
        parser.error("run requires --config")
    keys, policy, schedule = load_config(args.config)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    store = Store(args.root)
    runner = Scheduler(store, keys, policy, schedule)

    def emit(result):
        if args.report:
            atomic_write(args.report, json_bytes(result))
        print(json_bytes(result).decode())

    try:
        if args.action == "probe" and args.probe_config is None:
            parser.error("probe requires --probe-config")
        result = runner.run() if args.action == "run" else {}
        if args.probe_config:
            from .daily_probe import probe

            result["publication_observation"] = probe(runner, args.probe_config)
        emit(result)
        # Process success does not imply data_complete. Never restart for expected 404.
    except WriterBusy as exc:
        emit(
            {
                "runner_ok": True,
                "data_complete": None,
                "state": "deferred_lock",
                "as_of": now(),
                "reason": str(exc),
            }
        )
    except (DataError, SourceError, OSError, sqlite3.Error, ValueError) as exc:
        emit(
            {
                "runner_ok": False,
                "data_complete": None,
                "state": "failed",
                "as_of": now(),
                "reason": f"{type(exc).__name__}: {exc}",
            }
        )
        raise SystemExit(1) from exc
    finally:
        runner.close()
        store.close()


if __name__ == "__main__":
    main()
