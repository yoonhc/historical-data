"""Explicit, idempotent review of ONE daily task. No HTTP or service operations."""

import argparse
import copy
import json
import os
import re
import sqlite3
from datetime import timedelta
from pathlib import Path

from .binance import archive_url, rest_url
from .daily_schedule import KST, Scheduler, fingerprint, load_config
from .domain import DAY, DataError, Key, WriterBusy, json_bytes, now, sha256, utc
from .fileio import layout_lock
from .normalize import normalize_archive, normalize_rest
from .pipeline import Collector
from .store import Store


def task_hash(task):
    return sha256(json_bytes(task))


def inspect_task(root, task_id):
    path = Path(root).resolve() / "state/catalog.sqlite"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT record FROM daily_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise DataError("Unknown task")
        task = json.loads(row[0])
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='daily_reviews'").fetchone()
        reviews = (
            [json.loads(r[0]) for r in db.execute("SELECT record FROM daily_reviews")]
            if exists
            else []
        )
    return {
        "task": task,
        "expected_task_sha256": task_hash(task),
        "reviews": [r for r in reviews if r["request"]["task_id"] == task_id],
    }


def _fetch(store, source_id, url, earliest, latest, *, http=True):
    for row in store.db.execute(
        "SELECT * FROM fetches WHERE artifact_id=? AND url=? ORDER BY id DESC", (source_id, url)
    ):
        if earliest <= utc(row["observed_at"]) <= latest:
            if not http or json.loads(row["metadata"]).get("status") == 200:
                return dict(row)
    raise DataError("No successful fetch in this task's eligible confirmation period")


def _remote_proof(runner, task, job):
    """Verify a legacy publication and its remote fetches, not just checked_at.

    The published job must still be current. Raw payloads are re-parsed and
    compared with active economic rows; neither old queries nor downloads alone
    can attest a refresh. Missing evidence is deliberately refused.
    """
    store, key = runner.store, Key(**task["key"])
    earliest, latest = utc(task["first_eligible_at"]), utc(job["updated_at"])
    if not earliest <= latest <= runner.clock():
        raise DataError("Publication is outside the confirmation period")
    start, end = utc(task["day"]), utc(task["day"]) + DAY
    identity = job["source_id"]
    blob, observed = store.raw(identity)
    fetches = []
    if key.dataset not in {"funding_events", "instrument_metadata"}:
        url = archive_url(key, start, False)
        if job["id"] != sha256(json_bytes([url, key.asdict(), start, end])):
            raise DataError("Not the exact daily remote archive publication")
        fetches.append(_fetch(store, identity, url, earliest, latest))
        checks = store.db.execute(
            "SELECT * FROM fetches WHERE url=? ORDER BY id DESC", (url + ".CHECKSUM",)
        ).fetchall()
        pair = None
        for check in checks:
            if (
                not earliest <= utc(check["observed_at"]) <= latest
                or json.loads(check["metadata"]).get("status") != 200
            ):
                continue
            body, _ = store.raw(check["artifact_id"])
            try:
                Collector._verify_checksum(blob, body, url.rsplit("/", 1)[1])
            except DataError:
                continue
            pair = dict(check)
            break
        if pair is None:
            raise DataError("No matching verified ZIP/CHECKSUM pair in this cycle")
        fetches.append(pair)
        batch = normalize_archive(
            blob, key, identity, observed, url.rsplit("/", 1)[1][:-4] + ".csv"
        )
        if batch.quarantine or any(not start <= r["timestamp"] < end for r in batch.rows):
            raise DataError("Archive cannot satisfy the exact task interval")
        rows = batch.rows
    else:
        manifest = json.loads(blob)
        if manifest.get("key") != key.asdict() or not manifest.get("pagination_complete"):
            raise DataError("Invalid REST publication manifest")
        if key.dataset == "funding_events":
            if utc(manifest["start"]) != start or utc(manifest["end"]) != end:
                raise DataError("REST publication range mismatch")
            if job["id"] != sha256(json_bytes([key.asdict(), start, end, "rest"])):
                raise DataError("Not the exact REST publication")
        fetches.append(_fetch(store, identity, "internal:rest-query", earliest, latest, http=False))
        cursor, rows, terminal = start, [], False
        sources = manifest["source_ids"]
        if not sources:
            raise DataError("Missing REST pages")
        for index, source_id in enumerate(sources):
            url = rest_url(key, cursor, end)[0]
            fetch = _fetch(store, source_id, url, earliest, latest)
            fetches.append(fetch)
            body, _ = store.raw(source_id)
            observed = utc(fetch["observed_at"])
            batch = normalize_rest(body, key, source_id, observed)
            if batch.quarantine:
                raise DataError("Quarantined REST input")
            if key.dataset == "instrument_metadata":
                if (
                    len(sources) != 1
                    or observed.astimezone(KST).date().isoformat() != task["cycle"]
                ):
                    raise DataError("Metadata is not an observation in the requested cycle")
                start, end = utc(manifest["start"]), utc(manifest["end"])
                rows, terminal = batch.rows, True
                if not rows or any(not start <= r["timestamp"] < end for r in rows):
                    raise DataError("Metadata observation is outside publication range")
                break
            if any(not cursor <= r["timestamp"] < end for r in batch.rows):
                raise DataError("REST page outside requested cursor range")
            rows.extend(batch.rows)
            if not batch.rows:
                terminal = index == len(sources) - 1
                if not terminal:
                    raise DataError("REST manifest continues after empty page")
            else:
                cursor = max(r["timestamp"] for r in batch.rows) + timedelta(milliseconds=1)
                terminal = cursor >= end
        if not terminal:
            raise DataError("REST pagination completion cannot be established")
    lower, upper = start, end
    if key.dataset == "instrument_metadata":
        lower = min(r["timestamp"] for r in rows)
        upper = max(r["timestamp"] for r in rows) + timedelta(microseconds=1)
    intent = {
        "key": key.asdict(),
        "start": start,
        "end": end,
        "source_id": identity,
        "row_start": lower,
        "row_end": upper,
        "row_count": len(rows),
        "economic_sha256": fingerprint(rows, key),
    }
    proof = runner.proof(key, task["day"], intent, writer_held=True)
    if not proof:
        raise DataError("Remote publication does not match active verified data")
    proof["review_confirmation"] = {
        "job": dict(job),
        "fetches": fetches,
        "eligible_since": task["first_eligible_at"],
    }
    return proof


def reconcile_proof(runner, task):
    key = Key(**task["key"])
    if task["purpose"] == "acquire":
        proof = runner.proof(key, task["day"], writer_held=True)
        if proof:
            return proof
        raise DataError("No complete, unblocked verified acquisition evidence")
    failures = []
    for job in runner.store.db.execute(
        "SELECT * FROM jobs WHERE key_id=? AND state='published' ORDER BY updated_at DESC",
        (key.id,),
    ).fetchall():
        try:
            return _remote_proof(runner, task, job)
        except (DataError, ValueError, KeyError) as exc:
            failures.append(str(exc))
    raise DataError("No publication proving this confirmation cycle: " + "; ".join(failures[:3]))


def apply_review(runner, request):
    required = {
        "request_id",
        "task_id",
        "expected_task_sha256",
        "action",
        "reason",
        "extra_attempts",
    }
    if not isinstance(request, dict) or set(request) != required:
        raise DataError("Review request fields must match the documented schema")
    for field in ("task_id", "expected_task_sha256"):
        if not isinstance(request[field], str) or not re.fullmatch(r"[a-f0-9]{64}", request[field]):
            raise DataError(f"Invalid {field}")
    if not isinstance(request["request_id"], str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,128}", request["request_id"]
    ):
        raise DataError("Invalid review request ID")
    if request["action"] not in {"reconcile", "retry"}:
        raise DataError("Unknown review action")
    if (
        not isinstance(request["reason"], str)
        or not request["reason"].strip()
        or len(request["reason"]) > 2000
    ):
        raise DataError("A bounded operator reason is required")
    extra = request["extra_attempts"]
    if (
        type(extra) is not int
        or (request["action"] == "retry" and not 1 <= extra <= 3)
        or (request["action"] == "reconcile" and extra != 0)
    ):
        raise DataError("Retry permits 1..3 additional task attempts; reconcile permits 0")
    with runner.ledger.exclusive(), layout_lock(runner.store.root), runner.store.writer(timeout=5):
        db = runner.store.db
        previous = db.execute(
            "SELECT record FROM daily_reviews WHERE id=?", (request["request_id"],)
        ).fetchone()
        if previous:
            receipt = json.loads(previous[0])
            if receipt["request"] != request:
                raise DataError("Review request ID was already used with different contents")
            return receipt
        row = db.execute(
            "SELECT record FROM daily_tasks WHERE id=?", (request["task_id"],)
        ).fetchone()
        if row is None:
            raise DataError("Unknown task")
        before = json.loads(row[0])
        if task_hash(before) != request["expected_task_sha256"]:
            raise DataError("Task changed since operator inspection")
        if before["state"] != "needs_review":
            raise DataError("Only needs_review tasks can be explicitly resumed")
        if before["config_hash"] != runner.config_hash or before["key"] not in [
            k.asdict() for k in runner.keys
        ]:
            raise DataError("Task/configuration scope differs")
        after = copy.deepcopy(before)
        current = runner.clock()
        if request["action"] == "reconcile":
            proof = reconcile_proof(runner, before)
            after.update(
                state="complete",
                reason="operator_reconciled_verified_storage",
                proof=proof,
                next_at=None,
                completed_at=current,
                review_required=False,
            )
        else:
            if (
                before["purpose"] == "observe"
                and before["cycle"] != current.astimezone(KST).date().isoformat()
            ):
                raise DataError("Cannot retry a historical metadata observation")
            after.update(
                state="retry_wait",
                reason="operator_authorized_additional_attempts",
                next_at=max(current, utc(before["first_eligible_at"])),
                review_required=False,
                manual_retry={
                    "request_id": request["request_id"],
                    "limit": extra,
                    "remaining": extra,
                },
            )
        receipt = {
            "id": request["request_id"],
            "version": 1,
            "request": request,
            "config_hash": runner.config_hash,
            "applied_at": current,
            "actor_uid": os.getuid(),
            "before": before,
            "after": after,
            "http_requests": 0,
        }
        # No nested Store transaction/ledger helper: receipt, state and budget
        # have one commit. A crash after commit is replayed from this receipt.
        with db:
            db.execute(
                "UPDATE daily_tasks SET record=? WHERE id=?",
                (json_bytes(after).decode(), after["id"]),
            )
            runner.fault("review_after_task_write", receipt)
            db.execute(
                "INSERT INTO daily_reviews VALUES(?,?)",
                (receipt["id"], json_bytes(receipt).decode()),
            )
            runner.fault("review_before_commit", receipt)
        runner.fault("review_after_commit", receipt)
        return json.loads(json_bytes(receipt))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "apply"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--task-id")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--request", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "inspect":
            if not args.task_id:
                parser.error("inspect requires --task-id")
            result = inspect_task(args.root, args.task_id)
        else:
            if not args.config or not args.request:
                parser.error("apply requires --config and --request")
            keys, policy, schedule = load_config(args.config)
            store = Store(args.root)
            runner = Scheduler(store, keys, policy, schedule)
            try:
                result = apply_review(runner, json.loads(args.request.read_text()))
            finally:
                runner.close()
                store.close()
        print(json_bytes(result).decode())
    except (DataError, OSError, sqlite3.Error, ValueError) as exc:
        print(json_bytes({"applied": False, "reason": str(exc), "as_of": now()}).decode())
        raise SystemExit(75 if isinstance(exc, WriterBusy) else 1) from exc


if __name__ == "__main__":
    main()
