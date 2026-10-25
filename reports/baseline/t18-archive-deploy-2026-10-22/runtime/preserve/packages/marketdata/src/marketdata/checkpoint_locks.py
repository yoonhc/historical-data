"""Cooperative Linux locks for the lifetime of archived checkpoint inputs.

Lock the *parent directory inode*, never a file inside an immutable checkpoint.
Readers can use read-only mounts. Retirement leaves these parent directories in
place, so renaming/removing a checkpoint cannot create a second lock domain.
Order: maintenance -> repository -> sorted checkpoint parents -> layout -> writer.
"""

import fcntl
import os
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .domain import DataError


class CheckpointBusy(DataError):
    pass


_held = threading.local()


def plain_path(path):
    path = Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise DataError(f"Checkpoint path must not contain symlinks or '..': {path}")
    return path


@contextmanager
def _parent_lock(parent, exclusive, wait):
    # Reentrancy is limited to the same thread and process. A child must not
    # mistake an inherited descriptor for its own independently acquired lock.
    if getattr(_held, "pid", None) != os.getpid():
        for fd, _mode in getattr(_held, "locks", {}).values():
            os.close(fd)  # No LOCK_UN: that would release the parent's flock.
        _held.pid, _held.locks = os.getpid(), {}
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    key = (info.st_dev, info.st_ino)
    owned = _held.locks.get(key)
    if owned:
        os.close(fd)
        if exclusive and not owned[1]:
            raise CheckpointBusy("Cannot upgrade a checkpoint reader to a retirement lock")
        yield
        return
    try:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(fd, operation | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise CheckpointBusy(f"Checkpoint input is in use: {parent}") from exc
        plain_path(parent)
        fresh = parent.stat()
        if (fresh.st_dev, fresh.st_ino) != key:
            raise DataError(f"Checkpoint parent changed while locking: {parent}")
        _held.locks[key] = (fd, exclusive)
        try:
            yield
        finally:
            del _held.locks[key]
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def checkpoint_locks(roots, *, exclusive=False, wait=True):
    """Pin inputs across their entire use; cleanup uses exclusive, nonblocking locks.

    Parents must already exist, and are intentionally never created by readers.
    Merely loading a manifest pins only that load; a longer consumer must retain
    this context across all subsequent reads (including external restic commands).
    """
    roots = [plain_path(root) for root in roots]
    with ExitStack() as stack:
        for parent in sorted({root.parent for root in roots}):
            stack.enter_context(_parent_lock(parent, exclusive, wait))
        for root in roots:
            plain_path(root)
        yield
