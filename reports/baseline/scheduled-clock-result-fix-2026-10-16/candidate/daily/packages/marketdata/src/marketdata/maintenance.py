"""Serialized retention-preserving hourly packing and daily verified backups."""

from . import job_clock as jc

import fcntl
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .backup import create_backup, load_backup, reserve_bytes, verify_backup
from .domain import DataError, WriterBusy, json_bytes, now, utc
from .fileio import layout_lock, safe_file
from .packing import pack_events, prune_loose
from .store import atomic_write


@contextmanager
def maintenance_lock(store, *, wait=False, progress=None):
    """Serialize maintenance without holding the collector's writer/layout locks."""
    with (store.root / "state/maintenance.lock").open("a") as handle:
        requested = time.monotonic()
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            if not wait:
                raise WriterBusy("Another maintenance job is running; retry later") from exc
            if progress:
                progress({"phase": "waiting_for_maintenance_lock"})
            # Stay in this process: normal contention must not consume systemd's
            # failure retries. SIGINT still interrupts this wait; the service's
            # TimeoutStartSec bounds the combined wait and work.
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield time.monotonic() - requested
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


def backup_location(store, directory=None, required_mount=None):
    """Resolve a dedicated destination; an explicit missing target is never recreated."""
    target = Path(directory) if directory is not None else Path("backups/automatic")
    if not target.is_absolute():
        target = store.root / target
    if target.is_symlink():
        raise DataError("Backup directory must not be a symlink")
    target = target.resolve()
    if target == store.root or target in store.root.parents:
        raise DataError("Backup directory overlaps the store")
    if any(target.is_relative_to(store.root / part) for part in ("data", "state")):
        raise DataError("Backup directory must be outside data/ and state/")
    if (target / "backup.json").exists():
        raise DataError("Backup directory must not be an archived backup")
    if (directory is not None or target.exists()) and not target.is_dir():
        raise DataError(f"Configured backup directory is missing: {target}")
    if required_mount is not None:
        mount = Path(required_mount)
        if not mount.is_absolute():
            mount = store.root / mount
        mount = mount.resolve()
        if not mount.is_mount() or not target.is_relative_to(mount):
            raise DataError(
                f"Required backup mount is unavailable or does not contain target: {mount}"
            )
    return target


def daily_backup(
    store, day, min_free_gib, progress, *, directory=None, incremental=False, required_mount=None
):
    # A stable date groups attempts. Incomplete attempts remain for investigation;
    # only a verified completion marker can satisfy this day's backup.
    root = backup_location(store, directory, required_mount)
    destination_day = safe_file(root, day)
    for marker in sorted(destination_day.glob("*/backup.json")):
        _, manifest = load_backup(marker.parent)
        if manifest["source_root"] != str(store.root):
            raise DataError("Backup directory belongs to another source; use a dedicated directory")
        result = verify_backup(marker.parent)
        return {**result, "path": str(marker.parent), "reused": True}
    previous = None
    if incremental:
        candidates = sorted(
            (p for p in root.glob("*/*/backup.json") if p.parent.parent.name < day), reverse=True
        )
        if candidates:
            previous = candidates[0].parent
            _, manifest = load_backup(previous)
            if manifest["source_root"] != str(store.root):
                raise DataError(
                    "Backup directory belongs to another source; use a dedicated directory"
                )
    destination = destination_day / uuid.uuid4().hex
    result = create_backup(
        store, destination, min_free_gib=min_free_gib, progress=progress, reuse_from=previous
    )
    backup_location(store, directory, required_mount)
    return {**result, "reused": False}


def run(
    store,
    *,
    mode="hourly",
    min_free_gib=10,
    max_files=20000,
    progress=None,
    backup_dir=None,
    incremental=False,
    backup_mount=None,
    wait_for_lock=False,
):
    if mode not in {"hourly", "daily"} or not 2 <= max_files <= 100000:
        raise DataError("Expected hourly/daily mode and max_files 2..100000")
    reserve_bytes(min_free_gib)
    requested = now()
    with maintenance_lock(store, wait=wait_for_lock, progress=progress) as wait_seconds:
        point = jc.active().point('maintenance_after_lock') if jc.active() else None
        started = now()
        work_day = (jc.active().pin_day('backup_day', point) if mode == 'daily' and point
                    else started.date().isoformat())
        report = {
            "id": uuid.uuid4().hex,
            "mode": mode,
            "work_day": work_day,
            **({"clock_observation": point.evidence} if point else {}),
            "requested_at": requested,
            "started_at": started,
            "wait_for_lock": wait_for_lock,
            "lock_wait_seconds": wait_seconds,
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
            if mode == "daily":
                phase("backup_configuration")
                directory = backup_location(store, backup_dir, backup_mount)
                report["backup_configuration"] = {
                    "directory": str(directory),
                    "incremental": incremental,
                    "required_mount": str((store.root / backup_mount).resolve())
                    if backup_mount
                    else None,
                }
                report["backup_capacity_before"] = capacity(
                    directory if directory.exists() else store.root, min_free_gib
                )
                if not report["backup_capacity_before"]["healthy"]:
                    raise DataError("Insufficient backup destination capacity")
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
                if jc.active():
                    report['backup_decision_observation'] = jc.active().wait_point('backup_after_validation').evidence
                phase("backup")
                report["backup"] = daily_backup(
                    store,
                    work_day,
                    min_free_gib,
                    progress,
                    directory=backup_dir,
                    incremental=incremental,
                    required_mount=backup_mount,
                )
                report["backup_capacity_after"] = capacity(directory, min_free_gib)
            report["capacity_after"] = capacity(store.root, min_free_gib)
            healthy = (
                report["capacity_after"]["healthy"]
                and report.get("backup_capacity_after", {"healthy": True})["healthy"]
            )
            report.update(
                ok=healthy,
                state="complete" if healthy else "partial",
                phase="finished",
            )
            return report
        except jc.ClockDeferred as exc:
            report.update(state='deferred_clock', error=str(exc))
            raise
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
        time_status = (jc.freshness(bool(report and report['ok']), utc(report['started_at']), hours)
                       if report and jc.active() else {})
        modes[mode] = {
            "healthy": bool(report and report["ok"] and 0 <= age <= hours * 3600),
            "age_seconds": age,
            "report": report,
            **time_status,
        }
    if jc.active():
        for value in modes.values():
            value['healthy'] = (value.get('execution_succeeded', False) and value.get('fresh') is True)
    space = capacity(store.root, min_free_gib)
    configuration = (modes["daily"]["report"] or {}).get("backup_configuration")
    backup_space = None
    if configuration:
        try:
            target = backup_location(
                store, configuration["directory"], configuration.get("required_mount")
            )
            backup_space = {"path": str(target), **capacity(target, min_free_gib)}
        except (DataError, OSError) as exc:
            backup_space = {"healthy": False, "error": str(exc)}
    return {
        "checked_at": current,
        "healthy": space["healthy"]
        and all(m["healthy"] for m in modes.values())
        and (backup_space is None or backup_space["healthy"]),
        "capacity": space,
        "backup_capacity": backup_space,
        "modes": modes,
        **({"freshness_known": all(m.get("freshness_known", False) for m in modes.values())} if jc.active() else {}),
    }
