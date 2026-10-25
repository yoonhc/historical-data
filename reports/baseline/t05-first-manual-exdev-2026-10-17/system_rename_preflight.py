"""Disposable system-manager rename trial; no operating units, grants or checkpoints are changed.

Two transient units run as UID 1000 using the installed service's protection
options. They exercise only new dummy directories under /mnt/backup/.md-t05-*.
No daemon-reload, operating service control, retirement invocation or grant write.
"""
import argparse
import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNIT_FILE = Path('/etc/systemd/system/marketdata-checkpoint-retirement-apply@.service')
EXPECTED_UNIT = '9932a3c4a8bc5acc74d5ba9c98d6348e2e367524999e15181b246fd0ac3addd0'
PROCESSES = ('chrony.service', 'marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service')
CONFIG = Path('/etc/marketdata/checkpoint-clock-retirement')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def command(argv, timeout=40):
    return subprocess.run(list(map(str, argv)), capture_output=True, text=True, env=ENV, cwd='/', timeout=timeout)


def protected():
    result = {'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'files': {}, 'masks': {}, 'processes': {}}
    paths = [UNIT_FILE, *CONFIG.glob('*.json'), *CONFIG.glob('*.toml'), *CONFIG.glob('units/*'),
             Path('/var/lib/marketdata-clock/boot-state.json'), Path('/etc/chrony/marketdata.conf')]
    for path in paths:
        st = path.lstat()
        result['files'][str(path)] = {'uid': st.st_uid, 'gid': st.st_gid, 'mode': stat.S_IMODE(st.st_mode),
                                    'sha256': sha(path.read_bytes())}
    for directory in ('/etc/systemd/system', '/home/yoonhc/.config/systemd/user'):
        for name in ('marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer'):
            path = Path(directory) / name
            require(path.is_symlink() and os.readlink(path) == '/dev/null', 'Operating mask differs')
            result['masks'][str(path)] = os.readlink(path)
    for name in PROCESSES:
        p = command(['/usr/bin/systemctl', 'show', name, '-p', 'MainPID', '-p', 'InvocationID', '-p', 'ActiveState'])
        require(p.returncode == 0, p.stderr)
        result['processes'][name] = dict(line.split('=', 1) for line in p.stdout.splitlines())
    for name in ('manual.json', 'recurring.json'):
        value = json.loads((CONFIG / name).read_bytes())
        require(value.get('enabled') is False and value.get('approved_at') is None
                and value.get('approval_reference') is None, 'Operating grant enabled')
    return result


def properties(unit_text):
    # Copy actual service safeguards; omit only execution identity, live paths,
    # reporting and timeout/resource settings that this disposable trial supplies.
    omit = {'Type', 'User', 'Group', 'WorkingDirectory', 'Environment', 'ReadWritePaths', 'ExecStart',
            'StandardOutput', 'StandardError', 'TimeoutStartSec', 'TimeoutStopSec'}
    section = None
    result = []
    for line in unit_text.splitlines():
        if line.startswith('['):
            section = line
        elif section == '[Service]' and '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            if key not in omit:
                result.append(key + '=' + value)
    require('ProtectSystem=strict' in result and 'ProtectHome=read-only' in result
            and 'ProtectClock=yes' in result and 'NoNewPrivileges=yes' in result, 'Protection options differ')
    return result


def mount_id(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return next(int(line.split()[1]) for line in Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines() if line.startswith('mnt_id:'))
    finally:
        os.close(fd)


def worker(root, mode):
    require(os.getuid() == 1000 and mode in ('split', 'common'), 'Wrong trial identity')
    require(root.parent == Path('/mnt/backup') and root.name.startswith('.md-t05-rename-'), 'Not a trial path')
    require((root / 'marker').read_text() == root.name, 'Trial marker differs')
    case = root / mode
    source = case / 'checkpoints/automatic/dummy'
    target = case / 'checkpoints/.automatic.retiring/dummy'
    original = (source / 'payload').read_bytes()
    value = {'mode': mode, 'uid': os.getuid(), 'gid': os.getgid(), 'same_device': source.stat().st_dev == target.parent.stat().st_dev,
             'source_mount_id': mount_id(source.parent), 'staging_mount_id': mount_id(target.parent),
             'invocation_id': os.environ.get('INVOCATION_ID'), 'source_path': str(source), 'target_path': str(target)}
    try:
        source.rename(target)
        value['rename_errno'] = None
    except OSError as exc:
        value['rename_errno'] = exc.errno
    value.update(source_exists=source.is_dir(), target_exists=target.is_dir(),
                 payload_preserved=((target if target.exists() else source) / 'payload').read_bytes() == original)
    try:
        (case / 'outside/protected').write_bytes(b'not permitted')
        value['outside_write_errno'] = None
    except OSError as exc:
        value['outside_write_errno'] = exc.errno
    value['passed'] = (value['same_device'] and value['payload_preserved'] and value['outside_write_errno'] == errno.EROFS
                       and value['invocation_id'] and ((mode == 'split' and value['rename_errno'] == errno.EXDEV
                       and value['source_exists'] and not value['target_exists'] and value['source_mount_id'] != value['staging_mount_id'])
                       or (mode == 'common' and value['rename_errno'] is None and not value['source_exists']
                       and value['target_exists'] and value['source_mount_id'] == value['staging_mount_id'])))
    (root / 'control' / (mode + '.json')).write_text(json.dumps(value, indent=2) + '\n')
    return 0 if value['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    require(args.run and os.geteuid() == 0, 'Explicit --run and root required for disposable system units')
    unit_raw = UNIT_FILE.read_bytes()
    require(not UNIT_FILE.is_symlink() and sha(unit_raw) == EXPECTED_UNIT, 'Installed unit changed; review required')
    unit_properties = properties(unit_raw.decode())
    token = uuid.uuid4().hex[:16]
    root = Path('/mnt/backup') / ('.md-t05-rename-' + token)
    runtime = Path('/run') / ('md-t05-rename-' + token)
    report = HERE / ('system-rename-result-' + token + '.json')
    result = {'started_at': datetime.now(timezone.utc).isoformat(), 'passed': False,
              'operating_mutations': False, 'trial_root': str(root), 'trial_runtime': str(runtime),
              'tool_sha256': sha(Path(__file__).read_bytes()), 'installed_unit_sha256': sha(unit_raw),
              'protection_properties': unit_properties, 'cases': [], 'cleanup': {}}
    names = []
    before = protected()
    try:
        root.mkdir(mode=0o755)
        runtime.mkdir(mode=0o755)
        (root / 'marker').write_text(root.name)
        sealed = runtime / 'probe.py'
        sealed.write_bytes(Path(__file__).read_bytes())
        sealed.chmod(0o644)
        control = root / 'control'
        control.mkdir(mode=0o700)
        os.chown(control, 1000, 1000)
        for mode in ('split', 'common'):
            case = root / mode
            for relative in ('checkpoints/automatic/dummy', 'checkpoints/.automatic.retiring', 'outside'):
                (case / relative).mkdir(parents=True, exist_ok=True)
            (case / 'checkpoints/automatic/dummy/payload').write_bytes(b'Disposable T05 checkpoint\n')
            (case / 'outside/protected').write_bytes(b'preserved')
            for path in [case, *case.rglob('*')]:
                os.chown(path, 1000, 1000)
            name = 'md-t05-rename-' + token + '-' + mode + '.service'
            names.append(name)
            writable = [control, case / 'checkpoints'] if mode == 'common' else [control, case / 'checkpoints/automatic', case / 'checkpoints/.automatic.retiring']
            props = [*unit_properties, 'User=yoonhc', 'Group=yoonhc', 'WorkingDirectory=' + str(control),
                     'Environment=LC_ALL=C', 'TimeoutStartSec=45s', 'TimeoutStopSec=10s',
                     'ReadWritePaths=' + ' '.join(map(str, writable)), 'StandardOutput=journal', 'StandardError=journal']
            argv = ['/usr/bin/systemd-run', '--quiet', '--no-ask-password', '--wait', '--collect', '--service-type=oneshot', '--unit=' + name]
            for prop in props:
                argv.append('--property=' + prop)
            argv += ['/usr/bin/python3', '-B', '-I', str(sealed), '--fixture-worker', str(root), mode]
            print('checking disposable ' + mode + ' mount boundary', flush=True)
            done = command(argv, timeout=60)
            result['cases'].append({'unit': name, 'returncode': done.returncode, 'stdout': done.stdout, 'stderr': done.stderr,
                                    'result': json.loads((control / (mode + '.json')).read_bytes()) if (control / (mode + '.json')).is_file() else None})
            require(done.returncode == 0 and result['cases'][-1]['result']['passed'], 'Trial failed; inspect case and journal')
            require((case / 'outside/protected').read_bytes() == b'preserved', 'Outside fixture changed')
        result['operating_unchanged'] = protected() == before
        require(result['operating_unchanged'], 'Protected operating state changed')
        result['passed'] = True
    except BaseException as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        errors = []
        states = {}
        for name in names:
            state = command(['/usr/bin/systemctl', 'show', name, '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlPID', '-p', 'LoadState'])
            row = dict(line.split('=', 1) for line in state.stdout.splitlines() if '=' in line)
            if row.get('ActiveState') not in ('inactive', 'failed') or row.get('MainPID', '0') != '0' or row.get('ControlPID', '0') != '0':
                # Only our disposable transient unit may be stopped on timeout.
                stop = command(['/usr/bin/systemctl', 'stop', name])
                if stop.returncode:
                    errors.append(stop.stderr)
            state = command(['/usr/bin/systemctl', 'show', name, '-p', 'LoadState', '-p', 'ActiveState', '-p', 'MainPID'])
            row = dict(line.split('=', 1) for line in state.stdout.splitlines() if '=' in line)
            states[name] = row
            if row.get('MainPID', '0') != '0' or row.get('ActiveState') not in ('inactive', 'failed'):
                errors.append('Trial still running: ' + name)
        if not errors:
            for path in (root, runtime):
                if path.exists():
                    shutil.rmtree(path)
        result['cleanup'] = {'errors': errors, 'unit_states': states, 'paths_absent': not root.exists() and not runtime.exists()}
        result['operating_unchanged'] = protected() == before
        result['passed'] = bool(result['passed'] and not errors and result['cleanup']['paths_absent'] and result['operating_unchanged'])
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'passed': result['passed'], 'report': str(report), 'operating_unchanged': result['operating_unchanged'], 'cleanup': result['cleanup']}, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--fixture-worker':
        raise SystemExit(worker(Path(sys.argv[2]), sys.argv[3]))
    raise SystemExit(main())
