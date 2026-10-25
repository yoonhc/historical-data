"""Refresh one disabled T05 grant. No service control, preview or deletion.

Default check is read-only. Apply/rollback need root and this exact plan ID.
The existing coordinator SH lock excludes configuration transitions; an EX
flock on the config directory serializes this tool without creating a lock file.
There is no wait for a running backup and no coordinator EX lock.
"""
import argparse
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = Path('/etc/marketdata/checkpoint-clock-retirement')
TARGET = CONFIG / 'recurring.json'
MANUAL = CONFIG / 'manual.json'
COORDINATOR = Path('/var/lib/marketdata-clock/coordinator.lock')
LEDGER = Path('/var/lib/marketdata-clock/boot-state.json')
BOOT = Path('/proc/sys/kernel/random/boot_id')
RELEASE = Path('/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7')
OPS = Path('/var/lib/marketdata-clock/t05-disabled-scope-updates')
PURPOSE = 't05-disabled-recurring-scope-refresh-v1'
PROCESS_UNITS = ('chrony.service', 'marketdata-collector@depth.service',
                 'marketdata-collector@aggtrade.service')
RETIREMENT_UNITS = ('marketdata-checkpoint-retirement.service',
                    'marketdata-checkpoint-retirement.timer')
MASKS = tuple(Path(root) / name for root in
              ('/etc/systemd/system', '/home/yoonhc/.config/systemd/user')
              for name in RETIREMENT_UNITS)


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def blob(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def fingerprint(path):
    path = Path(path)
    s = path.lstat()
    result = {'uid': s.st_uid, 'gid': s.st_gid, 'mode': stat.S_IMODE(s.st_mode)}
    if stat.S_ISLNK(s.st_mode):
        return {**result, 'kind': 'symlink', 'target': os.readlink(path)}
    require(stat.S_ISREG(s.st_mode), 'Expected regular file: ' + str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        current = os.fstat(handle.fileno())
        require((s.st_dev, s.st_ino) == (current.st_dev, current.st_ino), 'File changed while opening')
        return {**result, 'kind': 'file', 'nlink': current.st_nlink, 'sha256': sha(handle.read())}


def trusted_directory(path):
    for parent in (path, *path.parents):
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'Untrusted directory: ' + str(parent))


@contextmanager
def locks():
    trusted_directory(CONFIG)
    require(fingerprint(COORDINATOR)['uid'] == 0, 'Coordinator ownership differs')
    descriptors = []
    try:
        for path, flags, mode in ((COORDINATOR, os.O_RDONLY, fcntl.LOCK_SH),
                                  (CONFIG, os.O_RDONLY | os.O_DIRECTORY, fcntl.LOCK_EX)):
            fd = os.open(path, flags | os.O_NOFOLLOW)
            descriptors.append(fd)
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        yield
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def command(argv):
    done = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=45,
                          cwd='/', env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin',
                                        'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'})
    require(done.returncode == 0, 'Read-only command failed: ' + done.stderr[-1000:] + done.stdout[-1000:])
    return done.stdout


def proposal():
    argv = [RELEASE / '.venv/bin/python', '-B', '-I', RELEASE / 'scripts/run_clock_retirement.py',
            '--settings', CONFIG / 'settings.toml', 'propose']
    if os.geteuid() == 0:
        argv = ['/usr/sbin/runuser', '-u', 'yoonhc', '--', *argv]
    else:
        require(os.geteuid() == 1000, 'Expected service account')
    return json.loads(command(argv))


def manager():
    names = (*PROCESS_UNITS, *RETIREMENT_UNITS,
             'marketdata-checkpoint-retirement-preview.service')
    raw = command(['/usr/bin/systemctl', '--system', 'show', *names,
                   '--property=Id,LoadState,ActiveState,MainPID,ControlPID,InvocationID,Job'])
    values = {}
    for block in raw.strip().split('\n\n'):
        row = {}
        for line in block.splitlines():
            key, value = line.split('=', 1)
            require(key not in row, 'Duplicate scalar systemd property')
            row[key] = value
        values[row['Id']] = row
    require(set(values) == set(names), 'Systemd unit set differs')
    for name in PROCESS_UNITS:
        require(values[name]['ActiveState'] == 'active' and int(values[name]['MainPID']) > 0,
                'Protected process not active: ' + name)
    for name in names[len(PROCESS_UNITS):]:
        row = values[name]
        require(row['ActiveState'] == 'inactive' and row['Job'] in ('', '0'),
                'Retirement unit is executing or queued: ' + name)
        if name.endswith('.service'):
            require(row['MainPID'] == '0' and row['ControlPID'] == '0',
                    'Retirement service still has a process: ' + name)
        if name in RETIREMENT_UNITS:
            require(row['LoadState'] == 'masked', 'Retirement unit is not masked')
    return values


def validate_transition(before, after):
    for item in (before, after):
        require(item.get('enabled') is False and item.get('approved_at') is None
                and item.get('approval_reference') is None, 'Grant must stay disabled and unapproved')
        require(item.get('scope_id') == sha(canonical(item['scope'])), 'Scope ID differs')
    expected = json.loads(json.dumps(before))
    expected['scope']['clock_binding']['bootstrap_profile_sha256'] = after['scope']['clock_binding']['bootstrap_profile_sha256']
    expected['scope_id'] = sha(canonical(expected['scope']))
    require(expected == after, 'Change exceeds the bootstrap hash and derived scope ID')
    require(before != after, 'No scope change')
    require(after['purpose'] == 'system-clock-bounded-checkpoint-retirement-v2'
            and after['schema'] == 1 and after['grant_kind'] == 'recurring'
            and after['action'] == 'run' and after['scope']['uid'] == 1000,
            'Grant contract differs')


def validate_plan(plan):
    require(plan['purpose'] == PURPOSE and plan['target'] == str(TARGET), 'Wrong operation scope')
    unsigned = {k: v for k, v in plan.items() if k != 'plan_id'}
    require(sha(canonical(unsigned)) == plan['plan_id'], 'Plan digest differs')
    require(sha(Path(__file__).read_bytes()) == plan['tool_sha256'], 'Reviewed tool changed')
    for direction in ('before', 'after'):
        value = plan[direction]
        require(sha(value['text'].encode()) == value['fingerprint']['sha256'], 'Payload hash differs')
        require(value['fingerprint'] == {**plan['before']['fingerprint'],
                                        'sha256': sha(value['text'].encode())}, 'Permissions changed')
    require(plan['before']['fingerprint'] == {'kind': 'file', 'uid': 0, 'gid': 0, 'mode': 0o644,
            'nlink': 1, 'sha256': sha(plan['before']['text'].encode())}, 'Target is not sealed')
    validate_transition(json.loads(plan['before']['text']), json.loads(plan['after']['text']))


def verify_live(plan):
    require(BOOT.read_text().strip() == plan['boot_id'], 'Boot changed; refresh the plan')
    for path, expected in plan['protected'].items():
        require(fingerprint(path) == expected, 'Protected input changed: ' + path)
    for path in MASKS:
        require(path.is_symlink() and os.readlink(path) == '/dev/null', 'Mask changed: ' + str(path))
    require(json.loads(MANUAL.read_bytes()).get('enabled') is False, 'Manual grant is enabled')
    current = fingerprint(TARGET)
    require(current in (plan['before']['fingerprint'], plan['after']['fingerprint']),
            'Current grant differs; do not overwrite')
    require(proposal() == json.loads(plan['after']['text']), 'Current installed scope differs')
    return {'position': 'after' if current == plan['after']['fingerprint'] else 'before',
            'units': manager()}


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    sync_dir(path.parent)


def replace_checked(path, expected, desired, recheck):
    """One atomic rename; old or new bytes remain recoverable after interruption."""
    require(fingerprint(path) == expected, 'Target changed before staging')
    fd, name = tempfile.mkstemp(prefix='.t05-disabled-scope-', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(desired['text'].encode())
            os.fchmod(handle.fileno(), desired['fingerprint']['mode'])
            s = os.fstat(handle.fileno())
            if (s.st_uid, s.st_gid) != (desired['fingerprint']['uid'], desired['fingerprint']['gid']):
                os.fchown(handle.fileno(), desired['fingerprint']['uid'], desired['fingerprint']['gid'])
            handle.flush()
            os.fsync(handle.fileno())
        require(fingerprint(temp) == desired['fingerprint'], 'Staged file differs')
        recheck()
        require(fingerprint(path) == expected, 'Target changed before replacement')
        os.replace(temp, path)
        sync_dir(path.parent)
        require(fingerprint(path) == desired['fingerprint'], 'Installed file differs')
    finally:
        if temp.exists():
            temp.unlink()


def operate(plan, action):
    before = verify_live(plan)
    result = {'plan_id': plan['plan_id'], 'action': action, 'started_at': datetime.now(timezone.utc).isoformat(),
              'position_before': before['position'], 'enabled': False, 'checkpoint_deletion': False,
              'automatic_retirement': 'masked', 'grant_changed': False}
    if action == 'check':
        return {**result, 'passed': True, 'operating_write': False}
    require(os.geteuid() == 0, 'Administrator authentication is required for this one-file update')
    OPS.mkdir(mode=0o755, exist_ok=True)
    trusted_directory(OPS)
    out = OPS / plan['plan_id']
    if not out.exists():
        require(action == 'apply' and before['position'] == 'before', 'Missing installation evidence')
        out.mkdir(mode=0o755)
        sync_dir(out.parent)
    trusted_directory(out)
    # Recreate only missing immutable evidence after an interruption before rename.
    for name, raw in (('plan.json', blob(plan)), ('refresh_scope.py', Path(__file__).read_bytes()),
                      ('before.json', plan['before']['text'].encode()), ('after.json', plan['after']['text'].encode())):
        path = out / name
        if not path.exists():
            require(action == 'apply' and before['position'] == 'before', 'Incomplete installation evidence')
            write_new(path, raw)
        require(not path.is_symlink() and path.read_bytes() == raw, 'Saved evidence changed: ' + name)
    attempt = out / (action + '-' + uuid.uuid4().hex)
    write_new(attempt.with_suffix('.intent.json'), blob(result))
    destination = 'after' if action == 'apply' else 'before'
    try:
        if before['position'] != destination:
            replace_checked(TARGET, plan[before['position']]['fingerprint'], plan[destination],
                            lambda: verify_live(plan))
            result['grant_changed'] = True
        after = verify_live(plan)
        require(after['position'] == destination, 'Final grant state differs')
        for unit in PROCESS_UNITS:
            for key in ('MainPID', 'InvocationID', 'ActiveState'):
                require(before['units'][unit][key] == after['units'][unit][key], 'Protected process changed')
        result.update(passed=True, position_after=after['position'], protected_processes_unchanged=True)
    except Exception as exc:
        result.update(passed=False, error=f'{type(exc).__name__}: {exc}',
                      grant_fingerprint_after_error=fingerprint(TARGET))
        raise
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_new(attempt.with_suffix('.result.json'), blob(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('check', 'apply', 'rollback'), default='check')
    parser.add_argument('--approve-plan')
    args = parser.parse_args()
    plan = json.loads((HERE / 'plan.json').read_bytes())
    validate_plan(plan)
    require(args.action == 'check' or args.approve_plan == plan['plan_id'], 'Exact plan approval required')
    with locks():
        result = operate(plan, args.action)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
