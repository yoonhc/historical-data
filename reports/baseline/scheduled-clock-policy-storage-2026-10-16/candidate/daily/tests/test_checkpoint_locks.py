import os
import subprocess
import sys

import pytest
from marketdata import backup
from marketdata.checkpoint_locks import CheckpointBusy, checkpoint_locks
from test_backup import populate

from scripts.restic_trial import ArchiveReader


@pytest.mark.parametrize("operation", ["verify", "restore", "reuse", "reader"])
def test_readers_pin_input_for_entire_operation(store, tmp_path, operation):
    populate(store, tmp_path)
    root = tmp_path / "day" / "checkpoint"
    backup.create_backup(store, root, min_free_gib=0)
    code = """
import sys
from pathlib import Path
from marketdata import backup
from marketdata.store import Store
from scripts.restic_trial import ArchiveReader
root, live, dest, operation = sys.argv[1:]
called = False
def pause():
    global called
    if not called:
        called = True
        print('pinned', flush=True)
        sys.stdin.readline()
if operation == 'reader':
    reader = ArchiveReader(root)
    pause()
    reader.close()
else:
    name = {'verify':'verify_files', 'restore':'copy_verified', 'reuse':'load_backup'}[operation]
    original = getattr(backup, name)
    def wrap(*a, **kw):
        pause()
        return original(*a, **kw)
    setattr(backup, name, wrap)
    if operation == 'verify':
        backup.verify_backup(root)
    elif operation == 'restore':
        backup.restore_backup(root, dest, min_free_gib=0)
    else:
        store = Store(live)
        try:
            backup.create_backup(store, dest, reuse_from=root, min_free_gib=0)
        finally:
            store.close()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(root), str(store.root), str(tmp_path / "new"), operation],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "pinned"
        with pytest.raises(CheckpointBusy):
            with checkpoint_locks([root], exclusive=True, wait=False):
                pytest.fail("Reader did not protect its input")
        # No collector writer is held while a checkpoint reader waits or runs.
        with store.writer(timeout=0):
            pass
        _, error = proc.communicate("release\n", timeout=30)
        assert proc.returncode == 0, error
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    with checkpoint_locks([root], exclusive=True, wait=False):
        assert backup.verify_backup(root)["ok"]  # Same-thread EX -> nested SH is safe.
    assert not (root / "state/file-layout.lock").exists()


def test_lock_reentrancy_symlinks_and_process_death(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    with checkpoint_locks([root]):
        with checkpoint_locks([root]):
            pass
        with pytest.raises(CheckpointBusy, match="upgrade"):
            with checkpoint_locks([root], exclusive=True):
                pass
    link = tmp_path / "link"
    link.symlink_to(root)
    with pytest.raises(ValueError, match="symlinks"):
        with checkpoint_locks([link]):
            pass
    code = """
import sys
from marketdata.checkpoint_locks import checkpoint_locks
with checkpoint_locks([sys.argv[1]]):
    print('pinned', flush=True)
    sys.stdin.readline()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "pinned"
        proc.kill()
        proc.wait(timeout=10)
        with checkpoint_locks([root], exclusive=True, wait=False):
            pass
    finally:
        proc.stdout.close()
        proc.stdin.close()


def test_fork_does_not_inherit_python_lock_ownership(tmp_path):
    # The child's close of an inherited descriptor must not unlock its parent.
    root = tmp_path / "input"
    root.mkdir()
    with checkpoint_locks([root]):
        child = os.fork()
        if child == 0:
            try:
                with checkpoint_locks([root], exclusive=True, wait=False):
                    os._exit(2)
            except CheckpointBusy:
                os._exit(0)
            except BaseException:
                os._exit(3)
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0


def test_archive_reader_failed_open_releases_pin(tmp_path):
    root = tmp_path / "bad"
    root.mkdir()
    with pytest.raises(Exception):
        ArchiveReader(root)
    with checkpoint_locks([root], exclusive=True, wait=False):
        pass
