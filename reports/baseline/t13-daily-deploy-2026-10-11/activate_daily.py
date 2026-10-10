"""Switch only the approved user's daily units, then request the first run.

The timer stays disabled until the first-run result has been inspected. Never
rerun after a partial switch without reviewing activation-result.json/events.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CANDIDATE = HERE.parent/'t13-daily-review-2026-10-10'
RELEASE_ID = 't13-daily-scheduler-20261011-703fb0d455523c1b'
RELEASE = Path('/opt/marketdata/releases')/RELEASE_ID
OPS = Path('/var/lib/marketdata-t13-deploy-20261011')
UNITS = Path('/home/yoonhc/.config/systemd/user')
SERVICE, TIMER = 'marketdata-daily.service', 'marketdata-daily.timer'
PROPERTIES = 'Id,ActiveState,SubState,Result,MainPID,ControlPID,InvocationID,ExecMainStatus,ExecMainCode,ExecMainStartTimestampMonotonic,ExecMainExitTimestampMonotonic,CPUUsageNSec,MemoryPeak,IOReadBytes,IOWriteBytes,FragmentPath,DropInPaths,UnitFileState,NeedDaemonReload,ExecStart,ExecStartPre,After,Requires,Restart,TimeoutStartUSec,TimeoutStopUSec,Unit,Persistent,TimersCalendar,RandomizedDelayUSec,AccuracyUSec,NextElapseUSecRealtime'
PHASE = 'validate'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def properties(raw):
    result = {}
    for line in raw.splitlines():
        if '=' not in line:
            raise RuntimeError('Malformed systemctl property')
        key, value = line.split('=', 1)
        if key in result and key not in ('ExecStart', 'ExecStartPre'):
            raise RuntimeError('Unexpected repeated property: ' + key)
        result.setdefault(key, []).append(value)
    return result


def one(props, key):
    values = props.get(key, [])
    if len(values) != 1:
        raise RuntimeError('Missing/ambiguous property: ' + key)
    return values[0]


def commands(props, key):
    return [m.strip() for value in props.get(key, [])
            for m in re.findall(r'argv\[\]=(.*?) ; ignore_errors=', value)]


def event(kind, **values):
    with (HERE/'activation-events.jsonl').open('a') as f:
        f.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'kind': kind, **values})+'\n')
        f.flush(); os.fsync(f.fileno())


def command(args):
    p = subprocess.run([str(a) for a in args], text=True, capture_output=True, timeout=45)
    event('command', argv=[str(a) for a in args], code=p.returncode, stdout=p.stdout, stderr=p.stderr)
    if p.returncode:
        raise RuntimeError(f'Command failed ({p.returncode}): {args}\n{p.stderr}')
    return p.stdout


def show(unit):
    return properties(command(['/usr/bin/systemctl', '--user', 'show', unit, '--property='+PROPERTIES]))


def check_protected(before):
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != before['boot_id']:
        raise RuntimeError('Boot changed')
    for name, expected in before['protected_hashes'].items():
        if sha(name) != expected:
            raise RuntimeError('Protected file changed: ' + name)
    for name, target in before['retirement_masks'].items():
        if os.readlink(name) != target:
            raise RuntimeError('Retirement mask changed')


def replace_link_with_file(path, blob):
    temp = path.with_name(path.name+'.t13-new')
    with temp.open('xb') as f:
        f.write(blob); f.flush(); os.fsync(f.fileno())
    os.chmod(temp, 0o644)
    os.replace(temp, path)  # Replace the link; never write through it into workspace.
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_service(actual, before, expected_command):
    previous = properties(before['user_units'][SERVICE]['stdout'])
    expected_pre = commands(previous, 'ExecStartPre')
    if not expected_pre or commands(actual, 'ExecStartPre') != expected_pre:
        raise RuntimeError('Effective ExecStartPre checks differ')
    if commands(actual, 'ExecStart') != [expected_command]:
        raise RuntimeError('Effective daily command differs')
    if one(actual, 'DropInPaths') != one(previous, 'DropInPaths'):
        raise RuntimeError('Clock drop-in differs')
    for prop in ('After', 'Requires'):
        if 'marketdata-clock-ready.service' not in one(actual, prop).split():
            raise RuntimeError('Clock waiter dependency missing')
    for prop in ('Restart', 'TimeoutStartUSec', 'TimeoutStopUSec'):
        if one(actual, prop) != one(previous, prop):
            raise RuntimeError('Unapproved lifetime change: '+prop)
    if one(actual, 'NeedDaemonReload') != 'no':
        raise RuntimeError('Manager needs reload')


def main():
    global PHASE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 1000:
        raise SystemExit('Run as yoonhc, not root')
    if (HERE/'activation-result.json').exists() or (HERE/'activation-events.jsonl').exists():
        raise SystemExit('Previous activation exists; review rather than blind rerun')
    before = json.loads((HERE/'operating-before.json').read_text())
    installed_path = OPS/'finish-result.json' if (OPS/'finish-result.json').exists() else OPS/'result.json'
    installed = json.loads(installed_path.read_text())
    if not installed['passed'] or installed['release_id'] != RELEASE_ID or installed['phase'] != 'installed_pending_daily_activation':
        raise RuntimeError('Approved installation not complete')
    manifest = json.loads((CANDIDATE/'manifest.json').read_text())
    for name, expected in manifest['source_sha256'].items():
        if sha(RELEASE/name) != expected:
            raise RuntimeError('Installed source changed: '+name)
    for name, source in [('daily-scheduler.toml','configs/daily-scheduler.toml'),
                         ('daily-publication-probe.toml','configs/daily-publication-probe.example.toml')]:
        if sha(Path('/etc/marketdata')/name) != manifest['source_sha256'][source]:
            raise RuntimeError('Installed configuration changed')
    for name, old in before['daily_files'].items():
        if sha(name) != old['sha256'] or os.readlink(name) != old['symlink']:
            raise RuntimeError('Original daily unit changed')
    check_protected(before)
    template = (RELEASE/'integrations/daily_scheduler/marketdata-daily.service.in').read_text()
    service = template.replace('@RELEASE@', RELEASE_ID)
    timer = (RELEASE/'integrations/daily_scheduler/marketdata-daily.timer').read_text()
    expected = next(l.removeprefix('ExecStart=') for l in service.splitlines() if l.startswith('ExecStart='))
    if not args.run:
        print(json.dumps({'validated': True, 'release_id': RELEASE_ID, 'services_changed': False}))
        return
    result = {'release_id': RELEASE_ID, 'passed': False, 'scope': 'daily only',
              'automatic_retirement': 'masked', 'started_at': datetime.now(timezone.utc).isoformat()}
    try:
        gate = json.loads(command(before['strict_gate']['argv']))
        if not gate.get('ready'):
            raise RuntimeError('Strict current clock preparation not ready')
        verify_service(show(SERVICE), before, commands(properties(before['user_units'][SERVICE]['stdout']), 'ExecStart')[0])
        # Validate the exact rendered units with the installed interpreter before pausing the old timer.
        rendered = HERE/'rendered'
        rendered.mkdir()
        (rendered/SERVICE).write_text(service)
        (rendered/TIMER).write_text(timer)
        command(['/usr/bin/systemd-analyze', '--user', 'verify', rendered/SERVICE, rendered/TIMER])
        PHASE = 'daily_timer_held'
        command(['/usr/bin/systemctl', '--user', 'disable', '--now', TIMER])
        started = time.monotonic()
        while True:
            current = show(SERVICE)
            if one(current, 'ActiveState') in ('inactive', 'failed') and one(current, 'MainPID') == '0' and one(current, 'ControlPID') == '0':
                break
            if time.monotonic()-started > 1800:
                raise RuntimeError('Daily still running; timer held without terminating it')
            print('Waiting for existing daily normal exit', flush=True)
            time.sleep(5)
        check_protected(before)
        PHASE = 'switch_daily_units'
        replace_link_with_file(UNITS/SERVICE, service.encode())
        replace_link_with_file(UNITS/TIMER, timer.encode())
        command(['/usr/bin/systemctl', '--user', 'daemon-reload'])
        effective = show(SERVICE)
        verify_service(effective, before, expected)
        timer_effective = show(TIMER)
        for key, value in {'Unit': SERVICE, 'Persistent': 'yes', 'RandomizedDelayUSec': '30s', 'AccuracyUSec': '1s'}.items():
            if one(timer_effective, key) != value:
                raise RuntimeError('Effective timer differs: '+key)
        if '*:00/5:00' not in one(timer_effective, 'TimersCalendar'):
            raise RuntimeError('Effective calendar differs')
        check_protected(before)
        (HERE/'effective-units.json').write_text(json.dumps({'service':effective,'timer':timer_effective},indent=2)+'\n')
        PHASE = 'first_run_requested_timer_held'
        command(['/usr/bin/systemctl', '--user', 'start', '--no-block', SERVICE])
        result['passed'] = True
        result['next'] = 'Inspect first invocation/report; only then enable daily timer'
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    result.update(phase=PHASE, finished_at=datetime.now(timezone.utc).isoformat())
    (HERE/'activation-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
