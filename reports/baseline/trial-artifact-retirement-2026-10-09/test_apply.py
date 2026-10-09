"""Safety checks against disposable pytest directories, never the four real roots."""

import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location('trial_apply', Path(__file__).with_name('apply.py'))
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def tree(tmp_path):
    root = tmp_path/'trial'
    (root/'a'/'child').mkdir(parents=True)
    (root/'b').mkdir()
    (root/'a'/'child'/'one').write_bytes(b'one')
    (root/'a'/'two').write_bytes(b'two')
    (root/'b'/'three').write_bytes(b'three')
    outside = tmp_path/'keep'
    outside.write_bytes(b'protected')
    inventory = tmp_path/'inventory.gz'
    save_inventory(root, inventory)
    return root, inventory, outside


def save_inventory(root, inventory):
    with gzip.open(inventory, 'wt') as f:
        for row in subject.observed(root):
            f.write(json.dumps(row)+'\n')


def delete(root, inventory, tmp_path, size=2):
    journal = subject.Journal(tmp_path/'journal.jsonl')
    try:
        subject.delete_tree(root, inventory, subject.signature(root.stat()), journal, batch_size=size)
        return journal.files, journal.directories
    finally:
        journal.close()


def test_exact_list_deletion_leaves_sibling_and_parent(tmp_path):
    root, inventory, outside = tree(tmp_path)
    assert subject.check_tree(root, inventory) == 7
    assert delete(root, inventory, tmp_path) == (3, 4)
    assert not root.exists()
    assert root.parent.is_dir() and outside.read_bytes() == b'protected'
    events = [json.loads(x) for x in (tmp_path/'journal.jsonl').read_text().splitlines()]
    assert [x['kind'] for x in events] == ['batch_intent','batch_complete'] * 4
    assert events[-1]['files_deleted'] == 3 and events[-1]['directories_deleted'] == 4


def test_new_file_before_deletion_is_rejected(tmp_path):
    root, inventory, outside = tree(tmp_path)
    (root/'new').write_text('new')
    with pytest.raises(RuntimeError, match='Inventory changed'):
        subject.check_tree(root, inventory)
    assert (root/'a'/'two').exists() and outside.exists()


def test_new_file_after_preflight_is_preserved(tmp_path):
    root, inventory, outside = tree(tmp_path)
    subject.check_tree(root, inventory)
    (root/'b'/'new').write_text('new')
    with pytest.raises(OSError):
        delete(root, inventory, tmp_path)
    assert (root/'b'/'new').read_text() == 'new' and outside.exists()


def test_changed_file_is_not_unlinked(tmp_path):
    root, inventory, _ = tree(tmp_path)
    (root/'a'/'two').write_text('altered')
    with pytest.raises(RuntimeError, match='Changed before delete'):
        delete(root, inventory, tmp_path)
    assert (root/'a'/'two').read_text() == 'altered'


def test_external_hardlink_is_rejected(tmp_path):
    root, inventory, outside = tree(tmp_path)
    os.link(root/'a'/'two', tmp_path/'hardlink')
    save_inventory(root, inventory)
    with pytest.raises(RuntimeError, match='Hardlink appeared'):
        subject.check_tree(root, inventory)
    assert outside.exists() and (tmp_path/'hardlink').read_bytes() == b'two'


def test_file_symlink_replacement_is_rejected(tmp_path):
    root, inventory, outside = tree(tmp_path)
    victim = root/'a'/'two'
    victim.unlink()
    victim.symlink_to(outside)
    with pytest.raises(RuntimeError, match='Changed before delete'):
        delete(root, inventory, tmp_path)
    assert outside.read_bytes() == b'protected' and victim.is_symlink()


def test_parent_symlink_is_not_followed(tmp_path):
    root, inventory, outside = tree(tmp_path)
    (root/'a').rename(tmp_path/'moved')
    (root/'a').symlink_to(tmp_path/'moved', target_is_directory=True)
    with pytest.raises(OSError):
        delete(root, inventory, tmp_path)
    assert (tmp_path/'moved'/'two').read_bytes() == b'two' and outside.exists()


def test_mid_batch_failure_keeps_intent_and_stops(tmp_path, monkeypatch):
    root, inventory, outside = tree(tmp_path)
    original = os.unlink
    count = 0
    def fail_second(path, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('injected storage failure')
        return original(path, **kwargs)
    monkeypatch.setattr(os, 'unlink', fail_second)
    with pytest.raises(OSError, match='injected storage failure'):
        delete(root, inventory, tmp_path, size=128)
    events = [json.loads(x) for x in (tmp_path/'journal.jsonl').read_text().splitlines()]
    assert [x['kind'] for x in events] == ['batch_intent']
    assert outside.exists() and (root/'a'/'child'/'one').exists()


def test_nonempty_directory_cannot_be_removed_by_inventory(tmp_path):
    root, inventory, outside = tree(tmp_path)
    rows = list(subject.records(inventory))
    with gzip.open(inventory, 'wt') as f:
        for row in rows:
            if row['path'] != 'b/three':
                f.write(json.dumps(row)+'\n')
    with pytest.raises(OSError):
        delete(root, inventory, tmp_path)
    assert (root/'b'/'three').exists() and outside.exists()


def test_parent_traversal_in_inventory_is_rejected(tmp_path):
    root, inventory, outside = tree(tmp_path)
    rows = list(subject.records(inventory))
    rows[1]['path'] = '../keep'
    with gzip.open(inventory, 'wt') as f:
        for row in rows:
            f.write(json.dumps(row)+'\n')
    with pytest.raises(RuntimeError, match='Unsafe inventory path'):
        delete(root, inventory, tmp_path)
    assert outside.read_bytes() == b'protected'


def test_existing_checkpoint_reader_blocks_exclusive_retirement(tmp_path):
    from marketdata.checkpoint_locks import CheckpointBusy, checkpoint_locks

    root, inventory, outside = tree(tmp_path)
    code = ('import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY); '
            'fcntl.flock(fd,fcntl.LOCK_SH); print("ready",flush=True); sys.stdin.read()')
    reader = subprocess.Popen([sys.executable, '-c', code, str(root.parent)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert reader.stdout.readline().strip() == 'ready'
        with pytest.raises(CheckpointBusy):
            with checkpoint_locks([root], exclusive=True, wait=False):
                pytest.fail('Deletion must not start while a reader owns the parent')
        assert subject.check_tree(root, inventory) == 7 and outside.exists()
    finally:
        reader.communicate(timeout=5)
