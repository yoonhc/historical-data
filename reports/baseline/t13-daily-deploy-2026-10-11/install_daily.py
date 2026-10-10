"""Install the approved T13 release/config only. No service or clock control.

Run without --run for input validation. A partial root install is never rerun
blindly. Existing daily links and timer continue until separately activated.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CANDIDATE = HERE.parent / 't13-daily-review-2026-10-10'
RELEASE_ID = 't13-daily-scheduler-20261011-703fb0d455523c1b'
RELEASE = Path('/opt/marketdata/releases') / RELEASE_ID
OPS = Path('/var/lib/marketdata-t13-deploy-20261011')
CONFIGS = {'daily-scheduler.toml': 'configs/daily-scheduler.toml',
           'daily-publication-probe.toml': 'configs/daily-publication-probe.example.toml'}
PHASE = 'validate'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, data, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def run(argv, *, cwd=None, timeout=60):
    p = subprocess.run([str(x) for x in argv], cwd=cwd, text=True, capture_output=True, timeout=timeout)
    with (OPS / 'commands.jsonl').open('a') as out:
        out.write(json.dumps({'argv': [str(x) for x in argv], 'code': p.returncode,
                              'stdout': p.stdout, 'stderr': p.stderr}) + '\n')
        out.flush(); os.fsync(out.fileno())
    if p.returncode:
        raise RuntimeError(f'Command failed ({p.returncode}): {argv}\n{p.stderr[-3000:]}')
    return p.stdout


def validate():
    inputs = json.loads((HERE/'install-inputs.json').read_text())
    if inputs['release_id'] != RELEASE_ID or not inputs['approved']:
        raise RuntimeError('Unexpected approved release')
    for path, expected in inputs['files'].items():
        if sha(path) != expected:
            raise RuntimeError('Reviewed input changed: ' + path)
    before = json.loads((HERE/'operating-before.json').read_text())
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != before['boot_id']:
        raise RuntimeError('Boot changed; refresh deployment evidence')
    for path, expected in before['protected_hashes'].items():
        if sha(path) != expected:
            raise RuntimeError('Protected operating file changed: ' + path)
    for path, expected in before['source_hashes'].items():
        if sha(ROOT/path) != expected:
            raise RuntimeError('Operating source changed: ' + path)
    for path, value in before['daily_files'].items():
        p = Path(path)
        if sha(p) != value['sha256'] or not p.is_symlink() or os.readlink(p) != value['symlink']:
            raise RuntimeError('Daily changed before installation: ' + path)
    for path, expected in before['retirement_masks'].items():
        if os.readlink(path) != expected:
            raise RuntimeError('Retirement mask changed')
    for path in (RELEASE, OPS, *[Path('/etc/marketdata')/n for n in CONFIGS]):
        if path.exists() or path.is_symlink():
            raise RuntimeError('Existing installation requires review: ' + str(path))
    manifest = json.loads((CANDIDATE/'manifest.json').read_text())
    if manifest['release_id'] != RELEASE_ID:
        raise RuntimeError('Manifest release mismatch')
    if sha(CANDIDATE/'source.tar.gz') != manifest['source_archive_sha256']:
        raise RuntimeError('Source archive mismatch')
    files = manifest['source_sha256']
    computed = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if computed != manifest['content_manifest_sha256']:
        raise RuntimeError('Manifest content mismatch')
    return inputs, before, manifest


def install(inputs, before, manifest):
    global PHASE
    OPS.mkdir(mode=0o755)
    write_new(OPS/'installer.py', Path(__file__).read_bytes())
    for name in ('install-inputs.json', 'operating-before.json', 'expected-environment.json', 'inspect_environment.py'):
        write_new(OPS/name, (HERE/name).read_bytes())
    write_new(OPS/'manifest.json', (CANDIDATE/'manifest.json').read_bytes())
    PHASE = 'fixed_release'
    print(PHASE, flush=True)
    RELEASE.mkdir(mode=0o755)
    files = manifest['source_sha256']
    with tarfile.open(CANDIDATE/'source.tar.gz', 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) != len(files) or {m.name for m in members} != {'source/'+p for p in files}:
            raise RuntimeError('Unexpected archive entries')
        for member in members:
            name = member.name.removeprefix('source/')
            if not member.isfile() or Path(name).is_absolute() or '..' in Path(name).parts:
                raise RuntimeError('Unsafe archive member')
            blob = archive.extractfile(member).read()
            if hashlib.sha256(blob).hexdigest() != files[name]:
                raise RuntimeError('Archive file hash differs: ' + name)
            write_new(RELEASE/name, blob)
    write_new(OPS/'uv', Path(inputs['uv']).read_bytes(), 0o755)
    # Private copy; root never changes the developer cache or hardlinks it into /opt.
    shutil.copytree(inputs['uv_cache'], OPS/'uv-cache', symlinks=False)
    run([OPS/'uv', 'sync', '--offline', '--frozen', '--no-editable', '--link-mode=copy',
         '--python', '/usr/bin/python3', '--cache-dir', OPS/'uv-cache',
         '--reinstall-package', 'marketdata', '--reinstall-package', 'marketdata-freqtrade'],
        cwd=RELEASE, timeout=300)
    for path, expected in files.items():
        if sha(RELEASE/path) != expected:
            raise RuntimeError('Installed source differs: ' + path)
    PHASE = 'installed_environment'
    print(PHASE, flush=True)
    actual = json.loads(run([RELEASE/'.venv/bin/python', '-B', '-I', OPS/'inspect_environment.py']))
    expected = json.loads((OPS/'expected-environment.json').read_text())
    if actual != expected:
        write_new(OPS/'unexpected-environment.json', json.dumps(actual, indent=2).encode())
        raise RuntimeError('Installed environment differs from tested environment')
    # Reject any writable-by-user installed file; no privilege expansion for collectors.
    for path in [RELEASE, *RELEASE.rglob('*')]:
        if path.is_symlink():
            continue
        st = path.stat()
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise RuntimeError('Release ownership/permissions differ: ' + str(path))
    run(['/usr/sbin/runuser', '-u', 'yoonhc', '--', RELEASE/'.venv/bin/python', '-B', '-I', OPS/'inspect_environment.py'])
    PHASE = 'external_configuration'
    for name, source in CONFIGS.items():
        write_new(Path('/etc/marketdata')/name, (RELEASE/source).read_bytes())
    access = "import os,tomllib,json,sys; from pathlib import Path; p=Path(sys.argv[1]); a=tomllib.loads(Path('/etc/marketdata/daily-scheduler.toml').read_text()); b=tomllib.loads(Path('/etc/marketdata/daily-publication-probe.toml').read_text()); assert b['observation']['enabled'] is False; assert os.access(p/'state',os.R_OK|os.W_OK|os.X_OK); assert os.access(p/'data',os.R_OK|os.W_OK|os.X_OK); print(json.dumps({'uid':os.getuid(),'observer_enabled':False,'data_access':True}))"
    run(['/usr/sbin/runuser', '-u', 'yoonhc', '--', RELEASE/'.venv/bin/python', '-B', '-I', '-c', access, ROOT])
    for path, expected in before['protected_hashes'].items():
        if sha(path) != expected:
            raise RuntimeError('Protected file changed during install: ' + path)
    for path, value in before['daily_files'].items():
        if sha(path) != value['sha256'] or os.readlink(path) != value['symlink']:
            raise RuntimeError('Daily changed during installation')
    PHASE = 'installed_pending_daily_activation'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    inputs, before, manifest = validate()
    if not args.run:
        print(json.dumps({'inputs_valid': True, 'release_id': RELEASE_ID, 'services_changed': False}))
        return
    if os.geteuid() != 0:
        raise SystemExit('Administrator authentication required')
    os.umask(0o022)
    result = {'release_id': RELEASE_ID, 'passed': False,
              'started_at': datetime.now(timezone.utc).isoformat(),
              'services_changed': False, 'clock_control': False, 'automatic_retirement': 'masked'}
    try:
        install(inputs, before, manifest)
        result['passed'] = True
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    result.update(phase=PHASE, finished_at=datetime.now(timezone.utc).isoformat())
    if OPS.exists():
        write_new(OPS/'result.json', (json.dumps(result, indent=2)+'\n').encode())
    print(json.dumps(result, indent=2), flush=True)
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
