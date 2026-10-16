"""Only temporary preflight outputs. Operating accesses below are read-only."""
import argparse
import fcntl
import importlib
import json
import os
import sys
import time
import tomllib
from pathlib import Path

def save(path, value):
    data = (json.dumps(value, indent=2) + '\n').encode()
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())

def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=('access', 'context', 'hold', 'touch', 'fail', 'lock-try'))
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--name', required=True)
    p.add_argument('--release', type=Path)
    p.add_argument('--service')
    p.add_argument('--timer')
    args = p.parse_args()
    assert args.state.name.startswith('md-t15-') and args.state.parent == Path('/var/lib')
    assert args.name.replace('-', '').isalnum() and os.getuid() == 1000
    report = {'uid': os.getuid(), 'pid': os.getpid(), 'mode': args.mode,
              'invocation': os.environ.get('INVOCATION_ID'), 'python': sys.executable}
    if args.mode == 'access':
        import marketdata
        report['marketdata'] = marketdata.__file__
        # Never instantiate Store or a scheduler. Parse configurations only.
        configs = ['/etc/marketdata/runtime.json', '/etc/marketdata/clock-bootstrap.json',
            '/etc/marketdata/daily-scheduler.toml', '/etc/marketdata/daily-publication-probe.toml',
            '/etc/marketdata/checkpoint-clock-retirement/settings.toml',
            '/home/yoonhc/.config/marketdata/restic-archive.toml',
            '/home/yoonhc/.config/marketdata/network-observer.toml']
        for name in configs:
            raw = Path(name).read_bytes()
            json.loads(raw) if name.endswith('.json') else tomllib.loads(raw.decode())
        report['configurations_read'] = configs
        archive = tomllib.loads(Path(configs[-2]).read_text())
        with Path(archive['restic_password_file']).open('rb') as stream:
            assert stream.read(1)  # no contents, length or digest in output
        report['password_readable'] = True
        for name in ('/home/yoonhc/projects/algoTrade/historical_data/data',
                     '/mnt/backup/marketdata-historical/automatic'):
            assert Path(name).is_dir() and os.access(name, os.R_OK | os.X_OK)
        fd = os.open('/var/lib/marketdata-clock/coordinator.lock', os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            report['coordinator_shared_read_lock'] = True
        finally:
            os.close(fd)
    elif args.mode == 'context':
        sys.path.insert(0, str(args.release / 'migration'))
        import pinned
        import run_system_archive
        schedule = pinned.load_archive()
        run_system_archive.bind(schedule)
        report['context'] = schedule.service_context(service=args.service, timer=args.timer)
        report['wrong_unit'] = schedule.service_context(service=args.service.replace('-context', '-other'), timer=args.timer)
        report['wrong_timer'] = schedule.service_context(service=args.service, timer=args.timer.replace('.timer', '-other.timer'))
    elif args.mode == 'hold':
        fd = os.open(args.state / 'shared-test.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        save(args.state / (args.name + '-started.json'), report)
        deadline = time.monotonic() + 120
        while not (args.state / (args.name + '-release')).exists():
            if time.monotonic() > deadline:
                raise TimeoutError('Trial release missing')
            time.sleep(0.1)
        os.close(fd)
        report['normal_completion'] = True
    elif args.mode == 'lock-try':
        fd = os.open(args.state / 'shared-test.lock', os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                report['lock_conflict'] = False
            except BlockingIOError:
                report['lock_conflict'] = True
        finally:
            os.close(fd)
    save(args.state / (args.name + '.json'), report)
    return 1 if args.mode == 'fail' else 0

if __name__ == '__main__':
    raise SystemExit(main())
