import hashlib
import importlib.util
import os
from pathlib import Path
import stat

import pytest

spec = importlib.util.spec_from_file_location('bounded_dedup', Path(__file__).with_name('deduplicate.py'))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
PLAN = 'a' * 64


def item(path):
    s = path.stat()
    return {'path': str(path), 'dev': s.st_dev, 'ino': s.st_ino,
            'uid': s.st_uid, 'gid': s.st_gid, 'mode': stat.S_IMODE(s.st_mode),
            'bytes': s.st_size, 'allocated_bytes': s.st_blocks * 512,
            'nlink': s.st_nlink, 'mtime_ns': s.st_mtime_ns, 'ctime_ns': s.st_ctime_ns,
            'xattrs': {}, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def group(root, mode=0o600):
    a, b, c = [root / name for name in ('a.stdout', 'b.stdout', 'c.stdout')]
    for p in (a, b, c):
        p.write_bytes(b'fixed manifest\n' * 1000)
        p.chmod(mode)
    return {'source': item(a), 'targets': [item(b), item(c)],
            'sha256': hashlib.sha256(a.read_bytes()).hexdigest()}


@pytest.mark.parametrize('mode', [0o600, 0o664])
def test_paths_bytes_permissions_and_idempotence(tmp_path, mode):
    g = group(tmp_path, mode)
    assert d.inspect_group(g, PLAN) == 0
    events = []
    for t in g['targets']:
        assert d.replace_target(g, t, PLAN, events.append)
    assert d.inspect_group(g, PLAN) == 2
    assert len(events) == 4
    for t in g['targets']:
        assert not d.replace_target(g, t, PLAN, events.append)
        assert Path(t['path']).read_bytes() == Path(g['source']['path']).read_bytes()
        assert stat.S_IMODE(Path(t['path']).stat().st_mode) == mode
    assert len(events) == 4


@pytest.mark.parametrize('fault', ['before_replace', 'after_replace'])
def test_record_failure_can_be_inspected_and_resumed(tmp_path, fault):
    g = group(tmp_path)
    def fail(event):
        if event['phase'] == fault:
            raise OSError('injected audit failure')
    with pytest.raises(OSError):
        d.replace_target(g, g['targets'][0], PLAN, fail)
    assert d.inspect_group(g, PLAN) == (fault == 'after_replace')
    for t in g['targets']:
        d.replace_target(g, t, PLAN, lambda e: None)
    assert d.inspect_group(g, PLAN) == 2


def test_interrupted_temporary_link_preserves_original_and_resumes(tmp_path):
    g = group(tmp_path)
    target = g['targets'][0]
    os.link(g['source']['path'], d.temporary(target, PLAN))
    assert Path(target['path']).stat().st_ino == target['ino']
    assert d.inspect_group(g, PLAN) == 0
    assert d.replace_target(g, target, PLAN, lambda e: None)
    assert not d.temporary(target, PLAN).exists()
    assert d.inspect_group(g, PLAN) == 1


@pytest.mark.parametrize('change', ['bytes', 'mode', 'symlink', 'external_link', 'source_bytes'])
def test_changed_inputs_rejected_before_replacement(tmp_path, change):
    g = group(tmp_path)
    source, target = Path(g['source']['path']), Path(g['targets'][0]['path'])
    if change == 'bytes':
        target.write_bytes(b'x' * target.stat().st_size)
    elif change == 'mode':
        target.chmod(0o644)
    elif change == 'symlink':
        target.unlink()
        target.symlink_to(source)
    elif change == 'external_link':
        os.link(source, tmp_path / 'unplanned')
    else:
        source.write_bytes(b'x' * source.stat().st_size)
    with pytest.raises(RuntimeError):
        d.inspect_group(g, PLAN)
    assert Path(g['targets'][1]['path']).stat().st_ino == g['targets'][1]['ino']


def test_unrecognized_temporary_file_rejected(tmp_path):
    g = group(tmp_path)
    d.temporary(g['targets'][0], PLAN).write_text('unrelated')
    with pytest.raises(RuntimeError, match='Unrecognized'):
        d.inspect_group(g, PLAN)


def test_link_failure_leaves_original(tmp_path, monkeypatch):
    g = group(tmp_path)
    def fail(*args, **kwargs):
        raise OSError('link failed')
    monkeypatch.setattr(d.os, 'link', fail)
    with pytest.raises(OSError):
        d.replace_target(g, g['targets'][0], PLAN, lambda e: None)
    assert d.inspect_group(g, PLAN) == 0
