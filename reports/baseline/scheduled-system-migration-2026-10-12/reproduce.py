"""Offline fresh-path source/environment rebuild and isolated transaction tests."""
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE.parents[2] / '.cache/uv'


def main():
    seal = json.loads((HERE / 'seal.json').read_text())
    for name, digest in seal['files'].items():
        assert hashlib.sha256((HERE / name).read_bytes()).hexdigest() == digest, name
    manifest = json.loads((HERE / 'manifest.json').read_text())
    result = {'release_id': manifest['release_id'], 'plan_id': manifest['plan_id'],
              'operating_writes': False, 'commands': []}
    with tempfile.TemporaryDirectory(prefix='scheduled-manager-repro-') as temp:
        root = Path(temp)
        candidate = root / 'candidate'
        candidate.mkdir()
        with tarfile.open(HERE / 'source.tar.gz') as archive:
            archive.extractall(candidate, filter='data')
        for name, digest in manifest['source_sha256'].items():
            assert hashlib.sha256((candidate / name).read_bytes()).hexdigest() == digest
        for name in ('plan.json', 'manifest.json', 'test_migration.py', 'test_parent_bridges.py'):
            shutil.copyfile(HERE / name, root / name)
        commands = [
            ['uv', 'sync', '--project', str(candidate), '--frozen', '--offline', '--no-editable',
             '--link-mode', 'copy', '--cache-dir', str(CACHE)],
            [str(candidate / '.venv/bin/python'), '-B', '-I', '-m', 'pytest', '-q',
             str(root / 'test_migration.py'), '--junitxml=' + str(HERE / 'reproduction-tests.xml')],
        ]
        for argv in commands:
            run = subprocess.run(argv, capture_output=True, text=True, timeout=180)
            record = {'argv': argv, 'returncode': run.returncode,
                      'stdout': run.stdout, 'stderr': run.stderr}
            result['commands'].append(record)
            if run.returncode:
                break
        result['passed'] = len(result['commands']) == 2 and all(x['returncode'] == 0 for x in result['commands'])
        result['reproduced_source_files'] = len(manifest['source_sha256'])
    result['temporary_directory_absent'] = not root.exists()
    (HERE / 'reproduction.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'commands'}))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
