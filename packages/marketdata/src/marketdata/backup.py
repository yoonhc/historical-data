"""Verified catalog checkpoints and independent copies of every referenced immutable file."""

import hashlib
import json
import math
import os
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path, PurePosixPath

from .domain import DataError, json_bytes, now, sha256
from .store import atomic_write

GIB = 1024**3
CATALOG = "state/catalog.sqlite"
SCOPE = "catalog_and_all_referenced_raw_revisions_snapshots"


def reserve_bytes(min_free_gib):
    if not math.isfinite(min_free_gib) or min_free_gib < 0:
        raise DataError("min-free-gib must be finite and nonnegative")
    return math.ceil(min_free_gib * GIB)


def safe_file(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != relative or not path.parts:
        raise DataError(f"Unsafe backup path: {relative}")
    result = root
    for part in path.parts:
        result = result / part
        if result.is_symlink():
            raise DataError(f"Symlink is not a backup file: {result}")
    return result


def digest_file(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def open_catalog(path):
    if not path.is_file():
        raise DataError(f"Missing catalog: {path}")
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    return db


def catalog_inventory(path):
    """Read a closed standalone catalog, including inactive revisions and quarantined raw files."""
    files, counts = {}, {}

    def add(relative, digest, kind):
        if not relative.startswith(f"data/{kind}/"):
            raise DataError(f"Unexpected {kind} path: {relative}")
        safe_file(path.parent.parent, relative)
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise DataError(f"Invalid SHA-256 for {relative}")
        record = {"path": relative, "sha256": digest, "kind": kind}
        if relative in files and files[relative] != record:
            raise DataError(f"Conflicting backup reference: {relative}")
        files[relative] = record

    with closing(open_catalog(path)) as db:
        if [row[0] for row in db.execute("PRAGMA integrity_check")] != ["ok"]:
            raise DataError("Catalog integrity_check failed")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise DataError("Catalog foreign_key_check failed")
        for table in (
            "artifacts",
            "fetches",
            "revisions",
            "active",
            "snapshots",
            "issues",
            "queries",
            "jobs",
            "sync_targets",
            "sync_runs",
            "sync_coverage",
        ):
            counts[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for row in db.execute("SELECT * FROM artifacts"):
            add(row["path"], row["id"], "raw")
        revisions = {row["id"]: dict(row) for row in db.execute("SELECT * FROM revisions")}
        for row in revisions.values():
            add(row["path"], row["file_sha"], "canonical")
        for row in db.execute("SELECT * FROM active"):
            revision = revisions[row["revision_id"]]
            if (row["key_id"], row["day"]) != (revision["key_id"], revision["day"]):
                raise DataError("Active revision key/day mismatch")
        for row in db.execute("SELECT * FROM snapshots"):
            if sha256(row["manifest"].encode()) != row["id"]:
                raise DataError("Catalog snapshot manifest hash mismatch")
            manifest = json.loads(row["manifest"])
            for revision in manifest["revisions"]:
                if revision != revisions.get(revision["id"]):
                    raise DataError("Snapshot references a missing or changed revision")
            add(row["path"], row["id"], "snapshots")
    return sorted(files.values(), key=lambda item: item["path"]), counts


def new_destination(destination, source):
    destination = Path(destination).absolute()
    # Resolve the parent, but do not follow an existing final symlink.
    destination = destination.parent.resolve() / destination.name
    if destination.exists() or destination.is_symlink():
        raise DataError(f"Destination already exists; choose a new directory: {destination}")
    source = Path(source).resolve()
    if destination == source or destination in source.parents:
        raise DataError("Destination overlaps the source")
    for relative in ("data", "state"):
        if destination.is_relative_to(source / relative):
            raise DataError("Destination must be outside the source data/ and state/")
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def require_capacity(destination, copy_bytes, minimum):
    free = shutil.disk_usage(destination).free
    if free < copy_bytes + minimum:
        raise DataError(
            f"Insufficient free space: {free} bytes available, "
            f"{copy_bytes} to copy plus {minimum} reserved"
        )


def copy_verified(source, destination, record):
    src = safe_file(source, record["path"])
    dst = safe_file(destination, record["path"])
    if not src.is_file():
        raise DataError(f"Missing backup input: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    with src.open("rb") as incoming, dst.open("xb") as outgoing:
        while chunk := incoming.read(1024 * 1024):
            outgoing.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if digest.hexdigest() != record["sha256"]:
        raise DataError(f"Hash mismatch while copying: {src}")
    if "bytes" in record and record["bytes"] != size:
        raise DataError(f"Size mismatch while copying: {src}")
    return {**record, "bytes": size}


def verify_files(root, records):
    for record in records:
        path = safe_file(root, record["path"])
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            raise DataError(f"Missing or truncated backup file: {path}")
        if digest_file(path) != record["sha256"]:
            raise DataError(f"Backup hash mismatch: {path}")


def sync_directories(root):
    # Each copied file was fsynced; persist new directory entries before the completion marker.
    directories = [root, *(p for p in root.rglob("*") if p.is_dir())]
    for directory in reversed(directories):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    fd = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def create_backup(store, destination, *, min_free_gib=10, progress=None):
    minimum = reserve_bytes(min_free_gib)
    if store.db.in_transaction:
        raise DataError("Finish the catalog transaction before creating a backup")
    destination = new_destination(destination, store.root)
    require_capacity(destination, (store.root / CATALOG).stat().st_size, minimum)
    (destination / "state").mkdir()
    catalog = destination / CATALOG
    started = now()
    with store.writer(timeout=60):
        lock_started = time.monotonic()
        checkpoint_started = now()
        with closing(sqlite3.connect(catalog)) as target:
            store.db.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        checkpoint_finished = now()
        writer_seconds = time.monotonic() - lock_started
    # The source files are immutable and retained; copying does not hold the live writer.
    records, counts = catalog_inventory(catalog)
    size = sum(safe_file(store.root, item["path"]).stat().st_size for item in records)
    require_capacity(destination, size, minimum)
    if progress:
        progress({"phase": "checkpoint", "files": len(records), "writer_seconds": writer_seconds})
    copied = []
    for index, record in enumerate(records, 1):
        copied.append(copy_verified(store.root, destination, record))
        if progress and index % 1000 == 0:
            progress({"phase": "copy", "copied": index, "total": len(records)})
    with catalog.open("rb") as handle:
        os.fsync(handle.fileno())
    copied.append(
        {
            "path": CATALOG,
            "kind": "catalog",
            "sha256": digest_file(catalog),
            "bytes": catalog.stat().st_size,
        }
    )
    verify_files(destination, copied)
    manifest = {
        "format_version": 1,
        "scope": SCOPE,
        "source_root": str(store.root),
        "started_at": started,
        "checkpoint_started_at": checkpoint_started,
        "checkpoint_finished_at": checkpoint_finished,
        "completed_at": now(),
        "writer_lock_seconds": writer_seconds,
        "same_filesystem_as_source": store.root.stat().st_dev == destination.stat().st_dev,
        "catalog_counts": counts,
        "files": sorted(copied, key=lambda item: item["path"]),
        "excluded": [
            "unregistered/orphan files",
            "derived exports",
            "reports and backtest results",
            "code/configuration/environment",
            "runtime locks, state JSON and prior backups",
        ],
    }
    manifest["backup_id"] = sha256(json_bytes(manifest))
    sync_directories(destination)
    # A directory without this marker is incomplete and cannot be verified/restored.
    atomic_write(destination / "backup.json", json_bytes(manifest))
    return {
        "backup_id": manifest["backup_id"],
        "path": str(destination),
        "ok": True,
        "files": len(copied),
        "bytes": sum(item["bytes"] for item in copied),
        "catalog_counts": counts,
        "writer_lock_seconds": writer_seconds,
        "same_filesystem_as_source": manifest["same_filesystem_as_source"],
    }


def load_backup(source):
    source = Path(source).resolve()
    marker = safe_file(source, "backup.json")
    if not marker.is_file():
        raise DataError("Missing backup.json completion marker; backup is incomplete")
    manifest = json.loads(marker.read_bytes())
    identity = manifest.pop("backup_id")
    if sha256(json_bytes(manifest)) != identity:
        raise DataError("Backup manifest hash mismatch")
    manifest["backup_id"] = identity
    if manifest["format_version"] != 1 or manifest["scope"] != SCOPE:
        raise DataError("Unsupported backup format or scope")
    catalog_record = [item for item in manifest["files"] if item["path"] == CATALOG]
    if len(catalog_record) != 1:
        raise DataError("Backup must contain exactly one catalog")
    for suffix in ("-wal", "-shm", "-journal"):
        if (source / (CATALOG + suffix)).exists():
            raise DataError("Backup catalog must be closed and standalone")
    verify_files(source, catalog_record)
    expected, counts = catalog_inventory(safe_file(source, CATALOG))
    expected.append({"path": CATALOG, "kind": "catalog", "sha256": catalog_record[0]["sha256"]})
    normalized = [
        {key: item[key] for key in ("path", "kind", "sha256")} for item in manifest["files"]
    ]
    if sorted(normalized, key=lambda r: r["path"]) != sorted(expected, key=lambda r: r["path"]):
        raise DataError("Backup inventory does not match catalog references")
    if counts != manifest["catalog_counts"]:
        raise DataError("Backup catalog counts mismatch")
    return source, manifest


def verify_backup(source):
    source, manifest = load_backup(source)
    verify_files(source, manifest["files"])
    return {
        "backup_id": manifest["backup_id"],
        "path": str(source),
        "ok": True,
        "checked_at": now(),
        "files": len(manifest["files"]),
        "bytes": sum(item["bytes"] for item in manifest["files"]),
        "catalog_counts": manifest["catalog_counts"],
    }


def restore_backup(source, destination, *, min_free_gib=10, progress=None):
    minimum = reserve_bytes(min_free_gib)
    source, manifest = load_backup(source)
    # Never overlay an existing store; keep the archived backup independent of its restore.
    destination = Path(destination).absolute()
    if destination.resolve().is_relative_to(source):
        raise DataError("Restore destination must be outside the backup")
    destination = new_destination(destination, source)
    records = manifest["files"]
    require_capacity(destination, sum(item["bytes"] for item in records), minimum)
    for index, record in enumerate(records, 1):
        copy_verified(source, destination, record)
        if progress and index % 1000 == 0:
            progress({"phase": "restore", "copied": index, "total": len(records)})
    verify_files(destination, records)
    _, counts = catalog_inventory(destination / CATALOG)
    if counts != manifest["catalog_counts"]:
        raise DataError("Restored catalog counts mismatch")
    receipt = {
        "backup_id": manifest["backup_id"],
        "source": str(source),
        "path": str(destination),
        "restored_at": now(),
        "files": len(records),
        "bytes": sum(item["bytes"] for item in records),
        "catalog_counts": counts,
        "ok": True,
        "services_started": False,
    }
    sync_directories(destination)
    atomic_write(destination / "restore.json", json_bytes(receipt))
    return receipt


def storage_report(root, *, min_free_gib=10):
    minimum = reserve_bytes(min_free_gib)
    root = Path(root).resolve()
    if not root.is_dir():
        raise DataError("Store directory does not exist")
    disk, stats = shutil.disk_usage(root), os.statvfs(root)
    directories = {}
    for relative in (
        "data/raw",
        "data/canonical",
        "data/snapshots",
        "data/exports",
        "state",
        "backups",
        "restore-checks",
    ):
        count, size, allocated = 0, 0, 0
        directory = root / relative
        if directory.is_symlink():
            raise DataError(f"Storage directory is a symlink: {directory}")
        for path in directory.rglob("*"):
            if path.is_file() and not path.is_symlink():
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue  # A writer may rename a staging file during this non-atomic scan.
                count += 1
                size += stat.st_size
                allocated += stat.st_blocks * 512
        directories[relative] = {"files": count, "bytes": size, "allocated_bytes": allocated}
    warnings = []
    if disk.free < minimum:
        warnings.append("low_free_space")
    if stats.f_files and stats.f_favail / stats.f_files < 0.05:
        warnings.append("low_free_inodes")
    return {
        "checked_at": now(),
        "root": str(root),
        "healthy": not warnings,
        "warnings": warnings,
        "filesystem": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "free_inodes": stats.f_favail,
            "total_inodes": stats.f_files,
        },
        "min_free_bytes": minimum,
        "directories": directories,
        "note": "Sizes are a live scan, not a catalog checkpoint; no retention or deletion applied.",
    }
