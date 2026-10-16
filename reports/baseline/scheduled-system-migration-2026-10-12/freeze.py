"""Seal reviewed source and evidence without copying data or secrets."""
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANDIDATE = HERE / 'candidate'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(name, value):
    (HERE / name).write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def main():
    manifest = json.loads((HERE / 'manifest.json').read_text())
    with (HERE / 'source.tar.gz').open('wb') as output:
        with gzip.GzipFile(filename='', fileobj=output, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for name, digest in manifest['source_sha256'].items():
                    raw = (CANDIDATE / name).read_bytes()
                    assert sha(raw) == digest, name
                    entry = tarfile.TarInfo(name)
                    entry.size, entry.mode, entry.mtime = len(raw), 0o644, 0
                    archive.addfile(entry, io.BytesIO(raw))
    snippet = '''import importlib.metadata,json,sys,marketdata
print(json.dumps({"python":sys.version,"executable":sys.executable,"marketdata":marketdata.__file__,
"packages":dict(sorted((x.metadata["Name"],x.version) for x in importlib.metadata.distributions()))}))'''
    environment = json.loads(subprocess.check_output(
        [str(CANDIDATE / '.venv/bin/python'), '-B', '-I', '-c', snippet], text=True))
    environment['source_sha256'] = manifest['source_sha256']
    environment['venv_files'] = {str(p.relative_to(CANDIDATE / '.venv')): sha(p.read_bytes())
        for p in sorted((CANDIDATE / '.venv').rglob('*'))
        if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts}
    environment['venv_links'] = {str(p.relative_to(CANDIDATE / '.venv')): str(p.readlink())
        for p in sorted((CANDIDATE / '.venv').rglob('*')) if p.is_symlink()}
    write('environment.json', environment)
    files = ['source.tar.gz', 'manifest.json', 'plan.json', 'approval.template.json',
             'environment.json', 'build.py', 'capture.py', 'freeze.py', 'reproduce.py',
             'verify_units.py', 'test_migration.py', 'test_parent_bridges.py']
    files.extend(str(p.relative_to(HERE)) for p in sorted((HERE / 'rendered').rglob('*')) if p.is_file())
    seal = {'release_id': manifest['release_id'], 'plan_id': manifest['plan_id'],
            'source_files': len(manifest['source_sha256']),
            'files': {name: sha((HERE / name).read_bytes()) for name in files}}
    write('seal.json', seal)
    print(json.dumps({'release_id': seal['release_id'], 'source_files': seal['source_files'],
                      'source_archive_sha256': seal['files']['source.tar.gz'],
                      'environment_files': len(environment['venv_files'])}))


if __name__ == '__main__':
    main()
