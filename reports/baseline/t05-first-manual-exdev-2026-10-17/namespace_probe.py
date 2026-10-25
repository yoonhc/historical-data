"""Real Linux rename tests in private user/mount namespaces using disposable /tmp fixtures."""
import errno
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def command(*argv):
    subprocess.run(argv, check=True, capture_output=True, timeout=15)


def mount_id(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return next(int(l.split()[1]) for l in Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines() if l.startswith('mnt_id:'))
    finally:
        os.close(fd)


def worker(root, mode):
    assert root.parent == Path('/tmp') and root.name.startswith('t05-rename-probe-')
    assert (root / 'fixture-marker').read_text() == 'disposable-t05-rename-fixture'
    shared = root / 'checkpoints'
    source, destination = shared / 'automatic/item', shared / '.automatic.retiring/item'
    # Reproduce the read-only parent plus either two child bind mounts or one common bind mount.
    command('/usr/bin/mount', '--bind', str(root), str(root))
    command('/usr/bin/mount', '-o', 'remount,bind,ro', str(root))
    for p in ((source.parent, destination.parent) if mode == 'split' else (shared,)):
        command('/usr/bin/mount', '--bind', str(p), str(p))
        command('/usr/bin/mount', '-o', 'remount,bind,rw', str(p))
    before = hashlib.sha256((source / 'payload').read_bytes()).hexdigest()
    result = {'mode': mode, 'source_dev': source.stat().st_dev,
              'staging_dev': destination.parent.stat().st_dev,
              'source_mount_id': mount_id(source.parent), 'staging_mount_id': mount_id(destination.parent)}
    try:
        source.rename(destination)
        result.update(rename_errno=None, source_present=source.exists(), destination_present=destination.is_dir(),
                      payload_unchanged=hashlib.sha256((destination / 'payload').read_bytes()).hexdigest() == before)
    except OSError as e:
        result.update(rename_errno=e.errno, source_present=source.is_dir(), destination_present=destination.exists(),
                      payload_unchanged=hashlib.sha256((source / 'payload').read_bytes()).hexdigest() == before)
    try:
        (root / 'outside').write_text('unexpected')
        result['outside_write_errno'] = None
    except OSError as e:
        result['outside_write_errno'] = e.errno
    assert result['source_dev'] == result['staging_dev']
    assert result['payload_unchanged'] and result['outside_write_errno'] == errno.EROFS
    if mode == 'split':
        assert result['rename_errno'] == errno.EXDEV and result['source_present'] and not result['destination_present']
        assert result['source_mount_id'] != result['staging_mount_id']
    else:
        assert result['rename_errno'] is None and not result['source_present'] and result['destination_present']
        assert result['source_mount_id'] == result['staging_mount_id']
    print(json.dumps(result))


def main():
    results = []
    for mode in ('split', 'common'):
        with tempfile.TemporaryDirectory(prefix='t05-rename-probe-', dir='/tmp') as directory:
            root = Path(directory)
            (root / 'fixture-marker').write_text('disposable-t05-rename-fixture')
            (root / 'checkpoints/automatic/item').mkdir(parents=True)
            (root / 'checkpoints/.automatic.retiring').mkdir()
            (root / 'checkpoints/automatic/item/payload').write_bytes(b'T05 disposable checkpoint contents\n')
            (root / 'outside').write_text('preserved')
            completed = subprocess.run(['/usr/bin/unshare', '--user', '--map-root-user', '--mount', '--propagation', 'private',
                                        '/usr/bin/python3', '-B', '-I', str(Path(__file__).resolve()), '--worker', directory, mode],
                                       capture_output=True, text=True, timeout=30)
            if completed.returncode:
                raise RuntimeError(completed.stderr)
            results.append(json.loads(completed.stdout))
            assert (root / 'outside').read_text() == 'preserved'
        assert not root.exists()
    value = {'passed': True, 'cases': results, 'fixture_cleanup': True,
             'actual_system_manager_test': False, 'operating_mutations': False}
    (Path(__file__).parent / 'namespace-results.json').write_text(json.dumps(value, indent=2) + '\n')
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]), sys.argv[3])
    else:
        assert len(sys.argv) == 1
        main()
