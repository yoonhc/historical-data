"""Execute only the already approved persistent user-unit hold; no time/RTC/package changes."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
UNIT_ROOT = Path('/home/yoonhc/.config/systemd/user')


def log(event):
    event = {'at': datetime.now().astimezone().isoformat(), **event}
    with (HERE / 'hold-events.jsonl').open('a') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        stream.flush(); os.fsync(stream.fileno())
    if 'stage_complete' in event or event.get('returncode', 0):
        print(json.dumps(event, ensure_ascii=False), flush=True)


def command(args, timeout=30):
    log({'command_intent': args})
    p = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    log({'command': args, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
    if p.returncode:
        raise RuntimeError(f'Command failed: {args}')
    return p.stdout


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['timers', 'jobs', 'collectors'])
    args = parser.parse_args()
    before = json.loads((HERE / 'before.json').read_text())
    assert json.loads((HERE / 'approval.json').read_text())['user_reply'] == '지금 시작 — Windows 작업 가능'
    assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == before['boot_id']
    collector_names = {'marketdata-depth.service', 'marketdata-aggtrade.service'}
    if args.stage == 'timers':
        names = sorted(n for n in before['units'] if n.endswith('.timer'))
    elif args.stage == 'collectors':
        names = sorted(collector_names)
    else:
        names = sorted(n for n in before['units'] if n.endswith('.service') and n not in collector_names)
    assert len(names) == {'timers': 9, 'jobs': 9, 'collectors': 2}[args.stage]
    held = HERE / 'held-user-units'
    held.mkdir(exist_ok=True)
    for name in names:
        path = UNIT_ROOT / name
        old = before['units'][name]
        saved = held / name
        if path.is_symlink() and os.readlink(path) == '/dev/null':
            assert saved.is_symlink() or saved.is_file(), name
            assert hashlib.sha256((HERE / 'unit-content' / name).read_bytes()).hexdigest() == old['sha256']
            continue
        if not os.path.lexists(path):
            assert os.path.lexists(saved), name
            continue
        assert not os.path.lexists(saved), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == old['sha256'], name
        assert (os.readlink(path) if path.is_symlink() else None) == old['symlink'], name
    if args.stage in ('timers', 'collectors'):
        command(['/usr/bin/systemctl', '--user', 'stop', *names], timeout=150)
    for name in names:
        state = command(['/usr/bin/systemctl', '--user', 'show', name, '-p', 'ActiveState', '-p', 'MainPID'])
        values = dict(line.split('=', 1) for line in state.splitlines() if '=' in line)
        assert values['ActiveState'] in ('inactive', 'failed'), (name, values)
        assert values.get('MainPID', '0') == '0', (name, values)
    for name in names:
        source, saved = UNIT_ROOT / name, held / name
        if source.is_symlink() and os.readlink(source) == '/dev/null':
            continue
        if os.path.lexists(source):
            log({'preserve_unit_intent': str(source), 'destination': str(saved)})
            source.rename(saved)
            sync_dir(UNIT_ROOT); sync_dir(held)
        command(['/usr/bin/systemctl', '--user', 'mask', name])
    command(['/usr/bin/systemctl', '--user', 'daemon-reload'])
    for name in names:
        path = UNIT_ROOT / name
        assert path.is_symlink() and os.readlink(path) == '/dev/null', name
        result = command(['/usr/bin/systemctl', '--user', 'show', name, '-p', 'LoadState', '-p', 'UnitFileState', '-p', 'ActiveState', '-p', 'MainPID'])
        values = dict(line.split('=', 1) for line in result.splitlines() if '=' in line)
        assert values['LoadState'] == 'masked' and values['UnitFileState'] == 'masked', (name, values)
        assert values['ActiveState'] in ('inactive', 'failed') and values.get('MainPID', '0') == '0'
    sync_dir(UNIT_ROOT)
    log({'stage_complete': args.stage, 'persistent_masked_units': names})


if __name__ == '__main__':
    main()
