"""Serialized retention-preserving hourly packing and daily verified backups."""

import fcntl
import json
import os
import shutil
import uuid
from contextlib import contextmanager

from .backup import create_backup, reserve_bytes, verify_backup
from .domain import DataError, WriterBusy, json_bytes, now, utc
from .fileio import layout_lock, safe_file
from .packing import pack_events, prune_loose
from .store import atomic_write


@contextmanager
def maintenance_lock(store):
    with (store.root / "state/maintenance.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WriterBusy("Another maintenance job is running; retry later") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def capacity(root, min_free_gib=10):
    minimum = reserve_bytes(min_free_gib)
    disk, stats = shutil.disk_usage(root), os.statvfs(root)
    warnings = []
    if disk.free < minimum:
        warnings.append("low_free_space")
    if stats.f_files and stats.f_favail / stats.f_files < 0.05:
        warnings.append("low_free_inodes")
    return {
        "free_bytes": disk.free,
        "free_inodes": stats.f_favail,
        "total_inodes": stats.f_files,
        "min_free_bytes": minimum,
        "warnings": warnings,
        "healthy": not warnings,
    }


def pending_prune(store, limit=32):
    """Find leftover copies, including a pack published before an interrupted run."""
    pending = []
    for pack in store.db.execute("SELECT id FROM file_packs ORDER BY created_at,id"):
        if any(
            safe_file(store.root, row[0]).exists()
            for row in store.db.execute("SELECT path FROM packed_files WHERE pack_id=?", (pack[0],))
        ):
            pending.append(pack[0])
        if len(pending) == limit:
            break
    return pending


def daily_backup(store, day, min_free_gib, progress):
    # A stable date groups attempts. Incomplete attempts remain for investigation;
    # only a verified completion marker can satisfy this day's backup.
    directory = safe_file(store.root, f"backups/automatic/{day}")
    for marker in sorted(directory.glob("*/backup.json")):
        result = verify_backup(marker.parent)
        return {**result, "path": str(marker.parent), "reused": True}
    destination = directory / uuid.uuid4().hex
    result = create_backup(store, destination, min_free_gib=min_free_gib, progress=progress)
    return {**result, "reused": False}


def run(store, *, mode="hourly", min_free_gib=10, max_files=20000, progress=None):
    if mode not in {"hourly", "daily"} or not 2 <= max_files <= 100000:
        raise DataError("Expected hourly/daily mode and max_files 2..100000")
    reserve_bytes(min_free_gib)
    with maintenance_lock(store):
        started = now()
        report = {
            "id": uuid.uuid4().hex,
            "mode": mode,
            "started_at": started,
            "state": "running",
            "ok": False,
            "retention": "indefinite",
            "logical_files_deleted": 0,
        }
        latest = store.root / f"state/maintenance-{mode}-latest.json"
        history = store.root / f"reports/maintenance/{started:%Y-%m-%d}/{report['id']}.json"

        def save():
            report["updated_at"] = now()
            atomic_write(history, json_bytes(report))
            atomic_write(latest, json_bytes(report))

        def phase(name):
            report["phase"] = name
            save()
            if progress:
                progress({"phase": name, "id": report["id"]})

        try:
            phase("capacity")
            report["capacity_before"] = capacity(store.root, min_free_gib)
            # Recover interrupted cleanup first; the verified pack retains all bytes.
            phase("prune_pending")
            pending = pending_prune(store)
            pruned = prune_loose(store, pack_ids=pending, apply=True) if pending else None
            report["pending_prune"] = {
                "packs": pending,
                "removed": pruned["removed"] if pruned else 0,
                "limit_reached": len(pending) == 32,
            }
            if not capacity(store.root, min_free_gib)["healthy"]:
                raise DataError("Insufficient capacity; no new packs or backup created")
            phase("pack")
            report["packing"] = pack_events(
                store, apply=True, min_free_gib=min_free_gib, max_files=max_files
            )
            phase("prune_new")
            ids = [p["id"] for p in report["packing"]["packs"]]
            pruned = prune_loose(store, pack_ids=ids, apply=True) if ids else None
            report["new_prune"] = {"packs": ids, "removed": pruned["removed"] if pruned else 0}
            if mode == "daily":
                phase("validate")
                with layout_lock(store.root):
                    report["validation"] = store.validate()
                if not report["validation"]["ok"]:
                    raise DataError("Store validation failed; no backup created")
                phase("backup")
                report["backup"] = daily_backup(
                    store, started.date().isoformat(), min_free_gib, progress
                )
            report["capacity_after"] = capacity(store.root, min_free_gib)
            report.update(
                ok=report["capacity_after"]["healthy"],
                state="complete" if report["capacity_after"]["healthy"] else "partial",
                phase="finished",
            )
            return report
        except (Exception, KeyboardInterrupt) as exc:
            report.update(
                state="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            report["finished_at"] = now()
            save()


def status(store, *, min_free_gib=10):
    current = now()
    modes = {}
    for mode, hours in (("hourly", 3), ("daily", 36)):
        path = store.root / f"state/maintenance-{mode}-latest.json"
        report = json.loads(path.read_bytes()) if path.exists() else None
        age = (current - utc(report["started_at"])).total_seconds() if report else None
        modes[mode] = {
            "healthy": bool(report and report["ok"] and 0 <= age <= hours * 3600),
            "age_seconds": age,
            "report": report,
        }
    space = capacity(store.root, min_free_gib)
    return {
        "checked_at": current,
        "healthy": space["healthy"] and all(m["healthy"] for m in modes.values()),
        "capacity": space,
        "modes": modes,
    }
