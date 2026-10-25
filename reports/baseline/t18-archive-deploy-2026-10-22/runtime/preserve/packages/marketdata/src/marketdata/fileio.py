"""Logical file reads from immutable loose files or byte-preserving ZIP_STORED packs."""

import fcntl
import hashlib
import os
import zipfile
from contextlib import contextmanager
from pathlib import PurePosixPath

from .domain import DataError, WriterBusy


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


@contextmanager
def layout_lock(root, *, exclusive=False):
    """Readers/backups pin physical layout; a writer attempts an immediate exclusive change."""
    with (root / "state/file-layout.lock").open("a") as lock:
        mode = fcntl.LOCK_EX | fcntl.LOCK_NB if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(lock, mode)
        except BlockingIOError as exc:
            raise WriterBusy("File layout is in use by a reader or backup; retry later") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def packed_record(db, relative):
    return db.execute(
        "SELECT f.*,p.path AS pack_path,p.bytes AS pack_bytes FROM packed_files f "
        "JOIN file_packs p ON p.id=f.pack_id WHERE f.path=?",
        (relative,),
    ).fetchone()


def read_packed(root, record):
    path = safe_file(root, record["pack_path"])
    try:
        if path.stat().st_size != record["pack_bytes"]:
            raise DataError(f"Truncated file pack: {path}")
        with zipfile.ZipFile(path) as archive:
            member = archive.getinfo(record["path"])
            if member.compress_type != zipfile.ZIP_STORED or member.file_size != record["bytes"]:
                raise DataError("Packed member format/size mismatch")
            blob = archive.read(member)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise DataError(f"Invalid packed member: {record['path']}") from exc
    if hashlib.sha256(blob).hexdigest() != record["sha256"]:
        raise DataError(f"Packed member hash mismatch: {record['path']}")
    return blob


def read_file(root, db, relative):
    with layout_lock(root):
        safe_file(root, relative)
        record = packed_record(db, relative)
        if record:
            # A damaged committed pack must be visible even if a loose duplicate survives.
            return read_packed(root, record)
        return safe_file(root, relative).read_bytes()


def sync_parent(path):
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
