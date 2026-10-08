"""Capture the accepted cutover's original user units and read-only host state."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
UNIT_ROOT = Path('/home/yoonhc/.config/systemd/user')


def run(args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=30,
                       env={**os.environ, 'LC_ALL': 'C', 'SYSTEMD_COLORS': '0'})
    return {'args': args, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}


def main():
    assert not (HERE / 'before.json').exists()
    inputs = json.loads((ROOT / 'reports/baseline/system-runtime-root-preflight-2026-10-07/inputs.json').read_text())
    units = sorted(Path(p).name for p in inputs['installed_user_units'])
    for group in ('pinned_files', 'operating_files', 'installed_user_units'):
        for p, digest in inputs[group].items():
            assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest, p
    shutil.copytree(UNIT_ROOT, HERE / 'saved-user-units', symlinks=True)
    records = {}
    for name in units:
        p = UNIT_ROOT / name
        st = p.lstat()
        raw = p.read_bytes()
        (HERE / 'unit-content').mkdir(exist_ok=True)
        (HERE / 'unit-content' / name).write_bytes(raw)
        records[name] = {'path': str(p), 'symlink': os.readlink(p) if p.is_symlink() else None,
                         'mode': stat.S_IMODE(st.st_mode), 'uid': st.st_uid, 'gid': st.st_gid,
                         'sha256': hashlib.sha256(raw).hexdigest()}
    props = ['Id', 'LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'MainPID', 'InvocationID',
             'ExecStart', 'KillSignal', 'TimeoutStopUSec', 'FragmentPath', 'DropInPaths', 'ControlGroup']
    manager = run(['/usr/bin/systemctl', '--user', 'show', *units, *[f'--property={p}' for p in props]])
    assert manager['returncode'] == 0
    commands = [manager,
                run(['/usr/bin/systemctl', '--user', 'list-jobs', '--no-pager']),
                run(['/usr/bin/systemctl', '--user', 'list-unit-files', 'marketdata-*', '--no-pager']),
                run(['/usr/bin/systemctl', 'list-unit-files', 'marketdata-*', 'chrony*', '--no-pager']),
                run(['/usr/bin/timedatectl', 'show']),
                run(['/usr/bin/findmnt', '-T', str(ROOT), '-o', 'TARGET,SOURCE,UUID,FSTYPE,OPTIONS']),
                run(['/usr/bin/findmnt', '-T', '/mnt/backup', '-o', 'TARGET,SOURCE,UUID,FSTYPE,OPTIONS']),
                run(['/usr/bin/crontab', '-l']),
                run(['/usr/bin/ps', '-u', 'yoonhc', '-o', 'pid,ppid,lstart,args'])]
    files = {}
    for p in [Path('/etc/adjtime'), *Path('/home/yoonhc/.config/marketdata').glob('*')]:
        if p.is_file():
            files[str(p)] = {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                             'mode': stat.S_IMODE(p.stat().st_mode)}
    for name in ['daily-latest.json', 'maintenance-daily-latest.json', 'depth-latest.json', 'aggtrade-latest.json']:
        p = ROOT / 'state' / name
        if p.is_file():
            (HERE / ('before-' + name)).write_bytes(p.read_bytes())
    record = {'captured_at': datetime.now().astimezone().isoformat(), 'units': records,
              'commands': commands, 'config_hashes': files,
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'candidate_hashes_match': True, 'operating_transition_applied': False}
    with (HERE / 'before.json').open('x') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(HERE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    print(json.dumps({'captured_at': record['captured_at'], 'units_saved': len(records),
                      'candidate_hashes_match': True, 'directory': str(HERE)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
