"""Plan against a consistent isolated catalog; never run the operating scheduler."""

import argparse
import fcntl
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .binance import archive_url, rest_url
from .daily_schedule import Scheduler, load_config
from .domain import DAY, DataError, Key, WriterBusy, json_bytes, now, utc
from .fileio import packed_record, read_packed, safe_file
from .store import Store, atomic_write


class PreviewStore(Store):
    """Only metadata is writable. File reads use the pinned source layout.

    Used exclusively inside preview()'s shared layout-lock lifetime. No symlink
    from the writable copy to an operating write directory is created.
    """

    def __init__(self, root, source):
        super().__init__(root)
        self.source = source

    def file_bytes(self, relative):
        path = safe_file(self.source, relative)
        record = packed_record(self._files_db, relative)
        return read_packed(self.source, record) if record else path.read_bytes()

    def file_exists(self, relative):
        record = packed_record(self._files_db, relative)
        return safe_file(self.source, record["pack_path"] if record else relative).is_file()

    def file_info(self, relative):
        record = packed_record(self._files_db, relative)
        if record:
            return {"bytes": record["bytes"], "pack_id": record["pack_id"], "allocated_bytes": None}
        stat = safe_file(self.source, relative).stat()
        return {"bytes": stat.st_size, "pack_id": None, "allocated_bytes": stat.st_blocks * 512}


def classify(runner, tasks, current):
    result = []
    for task in tasks:
        state, reason = task["state"], task["reason"]
        if state == "complete":
            group = "verified_complete_no_acquire" if task["purpose"] == "acquire" else "complete"
        elif state in {"needs_review", "missed_observation"}:
            group = "needs_review"
        elif state == "running":
            group = "recovery_required"
        elif not task["next_at"] or utc(task["next_at"]) > current:
            group = "not_due"
        else:
            group = "due"
            if task["purpose"] == "refresh" and any(
                p["key"] == task["key"]
                and p["day"] == task["day"]
                and p["id"] != task["id"]
                and p["state"] != "complete"
                and (
                    p["purpose"] == "acquire"
                    or p["state"] == "needs_review"
                    or (p["purpose"] == "refresh" and p["cycle"] < task["cycle"])
                )
                for p in tasks
            ):
                group = "held_by_pending_target"
            key = Key(**task["key"])
            start = utc(task["day"])
            url = (
                rest_url(key, start, start + DAY)[0]
                if key.dataset in {"funding_events", "instrument_metadata"}
                else archive_url(key, start, False)
            )
            for cool in runner.ledger.rows("daily_cooldowns"):
                if cool["id"] == urlsplit(url).hostname and utc(cool["until"]) > current:
                    group, reason = "held_cooldown", cool["until"]
        result.append({"task": task, "classification": group, "hold_or_reason": reason})
    return result


def preview(source, destination, keys, policy, schedule, *, current=None, fault=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise DataError("Preview destination must be separate from the operating root")
    current = utc(current) if current else now()
    fault = fault or (lambda *_: None)
    started, began = now(), time.monotonic()
    # Existing lock opened read-only: a preview cannot create operating state.
    lock_path = safe_file(source, "state/file-layout.lock")
    with lock_path.open("rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WriterBusy("Source layout is being changed; preview deferred") from exc
        destination.mkdir(parents=True, exist_ok=False)
        snapshot = destination / "catalog-snapshot.sqlite"
        source_path = safe_file(source, "state/catalog.sqlite")
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as reader:
            reader.execute("PRAGMA query_only=ON")
            # Pin one SQLite read snapshot during copying while collectors keep
            # appending to WAL; avoid restarting a large copy on every commit.
            reader.execute("BEGIN")
            reader.execute("SELECT count(*) FROM sqlite_master").fetchone()
            with closing(sqlite3.connect(snapshot)) as target:
                reader.backup(target, pages=1024)
                target.execute("PRAGMA journal_mode=DELETE")
        with snapshot.open("rb") as handle:
            snapshot_sha = hashlib.file_digest(handle, "sha256").hexdigest()
        captured = now()
        fault("snapshot_created", snapshot)
        work = destination / "store"
        (work / "state").mkdir(parents=True)
        with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(work / "state/catalog.sqlite")) as target:
                reader.backup(target)
        store = PreviewStore(work, source)
        network_calls = []

        def refuse_http(request):
            network_calls.append(str(request.url))
            raise DataError("HTTP is forbidden in preview")

        with httpx.Client(transport=httpx.MockTransport(refuse_http)) as client:
            runner = Scheduler(store, keys, policy, schedule, client=client, clock=lambda: current)
            try:
                with runner.ledger.exclusive():
                    # No recover(), execute_task(), report bridge or live Store.
                    runner.plan(current)
                    tasks = runner.ledger.rows("daily_tasks")
                    classified = classify(runner, tasks, current)
                    counts = {}
                    for row in classified:
                        group = row["classification"]
                        counts[group] = counts.get(group, 0) + 1
                    report = {
                        "preview_only": True,
                        "source_root": str(source),
                        "destination": str(destination),
                        "started_at": started,
                        "snapshot_captured_at": captured,
                        "snapshot_sha256": snapshot_sha,
                        "as_of": current,
                        "config_hash": runner.config_hash,
                        "state_counts": counts,
                        "tasks": classified,
                        "http_requests": len(network_calls),
                        "operating_catalog_connection": "mode=ro; query_only=ON",
                        "source_layout_lock": "existing file; read-only fd; shared flock",
                        "max_tasks_per_run": schedule.max_tasks,
                        "semantics": "eligibility_at_snapshot_not_prediction_of_fetch_outcomes",
                        "snapshot_is_market_data_backup": False,
                        "elapsed_seconds": time.monotonic() - began,
                    }
                    fault("plan_completed", report)
                    atomic_write(destination / "preview.json", json_bytes(report))
                    return report
            finally:
                runner.close()
                store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        keys, policy, schedule = load_config(args.config)
        result = preview(args.source_root, args.destination, keys, policy, schedule)
        print(json_bytes({k: v for k, v in result.items() if k != "tasks"}).decode())
    except (DataError, OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"preview_only": True, "passed": False, "reason": str(exc)}))
        raise SystemExit(75 if isinstance(exc, WriterBusy) else 1) from exc


if __name__ == "__main__":
    main()
