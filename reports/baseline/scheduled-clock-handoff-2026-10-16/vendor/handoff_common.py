"""Narrow host boundary used by the handoff and temporary-unit preflight."""
import contextlib
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.with_name('scheduled-system-migration-2026-10-12')
JOBS = ('oi', 'oi-probe', 'oi-quality', 'daily', 'pack', 'backup', 'restic-archive', 'network-observer')
SERVICES = tuple('marketdata-' + j + '.service' for j in JOBS)
TIMERS = tuple('marketdata-' + j + '.timer' for j in JOBS)
WAITER = 'marketdata-clock-ready.service'
USER_ROOT = '/home/yoonhc/.config/systemd/user/'
SYSTEM_ROOT = '/etc/systemd/system/'
IDENTITIES = ('chrony.service', 'marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service')
COMMANDS = ('ExecStart', 'ExecStartPre', 'ExecStartPost', 'ExecStop', 'ExecStopPost', 'ExecCondition')
PROPERTIES = ('Id,LoadState,ActiveState,SubState,MainPID,ControlPID,InvocationID,Job,'
    'FragmentPath,DropInPaths,NeedDaemonReload,Type,User,Group,WorkingDirectory,'
    'ExecStart,ExecStartPre,ExecStartPost,ExecStop,ExecStopPost,ExecCondition,After,Requires,Wants,'
    'Restart,RestartUSec,KillMode,KillSignal,TimeoutStartUSec,TimeoutStopUSec,RuntimeMaxUSec,'
    'ProtectClock,ProtectSystem,ProtectHome,PrivateTmp,NoNewPrivileges,UMask,Nice,'
    'IOSchedulingClass,IOSchedulingPriority,EnvironmentFiles,Unit,UnitFileState,'
    'Persistent,TimersCalendar,TimersMonotonic,AccuracyUSec,RandomizedDelayUSec,'
    'ExecMainStartTimestampMonotonic,ActiveEnterTimestampMonotonic')

class Hold(RuntimeError):
    pass

def require(condition, message):
    if not condition:
        raise Hold(message)

def encode(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode() + b'\n'

def sha(blob):
    return hashlib.sha256(blob).hexdigest()

def boot():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def parse(text):
    result = {}
    for line in text.splitlines():
        if not line:
            continue
        key, value = line.split('=', 1)
        require(key not in result or key in COMMANDS, 'Duplicate scalar systemd property: ' + key)
        result[key] = result[key] + '\n' + value if key in result else value
    return result

def classify(props, jobs):
    """Any executing main/control PID wins over an apparently inactive state.

    oneshot/start is executing, not idle. Unknown/ambiguous states are never
    candidates for stop. No-PID queued/restart states can be cancelled only
    after a persistent start barrier, re-read immediately before the command.
    """
    pids = [int(props.get(k, '0')) for k in ('MainPID', 'ControlPID')]
    if any(pids):
        return 'running'
    active, sub = props.get('ActiveState'), props.get('SubState')
    if sub == 'auto-restart':
        return 'pending'
    matching = [j for j in jobs if j['unit'] == props.get('Id')]
    if matching:
        # A running job can be between ExecStartPre/ExecStart with PID=0.
        # Only a manager-confirmed WAITING job is cancellable here.
        return 'pending' if all(j.get('state') == 'waiting' for j in matching) else 'unknown'
    if active in ('inactive', 'failed') and sub in ('dead', 'failed', 'exited', None):
        return 'idle'
    if props.get('LoadState') == 'not-found' and active == 'inactive':
        return 'idle'
    return 'unknown'

def user_command(argv):
    command = ['/usr/bin/env', 'XDG_RUNTIME_DIR=/run/user/1000',
        'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus', *map(str, argv)]
    return ['/usr/sbin/runuser', '-u', 'yoonhc', '--', *command] if os.geteuid() == 0 else command

class Managers:
    def __init__(self, *, allowed=(), record=lambda event: None, runner=subprocess.run):
        self.allowed, self.record, self.runner = set(allowed), record, runner

    def command(self, manager, action, *args, check=True):
        require(manager in ('user', 'system'), 'Invalid manager')
        reads = ('show', 'list-unit-files', 'list-units', 'list-jobs')
        if action not in reads:
            require(action in ('start', 'stop', 'daemon-reload', 'reset-failed'), 'Unapproved manager operation')
            units = [x for x in args if not x.startswith('--')]
            require(all((manager, name) in self.allowed for name in units), 'Out-of-scope unit operation')
            require(action == 'daemon-reload' or units, 'Unscoped manager operation')
        argv = ['/usr/bin/systemctl', '--' + manager, action, *args]
        if manager == 'user':
            argv = user_command(argv)
        if action not in reads:
            self.record({'event': 'command_intent', 'argv': argv})
        r = self.runner(argv, text=True, capture_output=True, timeout=45)
        if action not in reads:
            self.record({'event': 'command_result', 'argv': argv, 'code': r.returncode,
                         'stdout': r.stdout, 'stderr': r.stderr})
        require(not check or r.returncode == 0, 'systemctl failed: ' + repr(argv) + '\n' + r.stderr)
        return r

    def show(self, manager, name):
        r = self.command(manager, 'show', name, '--no-pager', '--property=' + PROPERTIES, check=False)
        values = parse(r.stdout)
        require(r.returncode == 0 or values.get('LoadState') == 'not-found', 'Cannot read unit: ' + name)
        return values

    def jobs(self, manager):
        # On this host list-jobs ignores --output=json; use its explicit plain,
        # headerless four-column contract (empty output means no jobs).
        raw = self.command(manager, 'list-jobs', '--all', '--no-pager', '--no-legend', '--plain').stdout
        value = []
        for row in raw.splitlines():
            fields = row.split()
            require(len(fields) == 4 and fields[0].isdigit(), 'Invalid job row: ' + row)
            job, unit, kind, state = fields
            require(state in ('running', 'waiting') and unit.endswith(('.service', '.timer', '.target', '.socket', '.mount', '.automount', '.swap', '.path', '.device', '.scope', '.slice')), 'Invalid job identity')
            value.append({'job': int(job), 'unit': unit, 'type': kind, 'state': state})
        require(len({j['job'] for j in value}) == len(value), 'Repeated job ID')
        return value

    def states(self, manager, units):
        jobs = self.jobs(manager)
        return {name: {'properties': (p := self.show(manager, name)),
                       'classification': classify(p, jobs),
                       'jobs': [j for j in jobs if j['unit'] == name]} for name in units}

def metadata(path):
    try:
        s = Path(path).lstat()
    except FileNotFoundError:
        return {'kind': 'absent'}
    return {'uid': s.st_uid, 'gid': s.st_gid, 'mode': stat.S_IMODE(s.st_mode),
            'kind': 'symlink' if stat.S_ISLNK(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else 'directory'}

class SafeFiles:
    """Exact write allowlist; directory-fd traversal, no symlink parents.

    Uses renameat, never writes through a pre-existing leaf. User directory
    ownership is permitted only for explicitly approved user-unit paths.
    Root control paths are root-owned. Fixture roots exercise the same writer.
    """
    def __init__(self, root, allowed, owners, *, control_prefix=None):
        self.root = Path(root).resolve()
        self.allowed, self.owners, self.control_prefix = set(allowed), owners, control_prefix
        self.live = self.root == Path('/')

    def path(self, name):
        p = Path(name)
        require(p.is_absolute() and '..' not in p.parts, 'Unsafe absolute path')
        return self.root / p.relative_to('/')

    @contextlib.contextmanager
    def parent(self, name, *, create=False):
        p = Path(name)
        self.path(name)
        fd = os.open(self.root, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
        try:
            for part in p.parts[1:-1]:
                if create:
                    try:
                        os.mkdir(part, 0o755, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                s = os.fstat(child)
                if self.live:
                    permitted = {0, 1000} if name.startswith(USER_ROOT) else {0}
                    # read-only protected /home paths also belong to UID 1000
                    if not create and name.startswith('/home/yoonhc/'):
                        permitted = {0, 1000}
                    require(s.st_uid in permitted and not s.st_mode & 0o022, 'Unsafe parent directory: ' + name)
                os.close(fd)
                fd = child
            yield fd, p.name
        finally:
            os.close(fd)

    def node(self, name):
        try:
            with self.parent(name) as (fd, leaf):
                s = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(s.st_mode):
                    return {'kind': 'symlink', 'target': os.readlink(leaf, dir_fd=fd)}
                require(stat.S_ISREG(s.st_mode), 'Expected regular file: ' + name)
                stream = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                with os.fdopen(stream, 'rb') as f:
                    raw = f.read()
                return {'kind': 'file', 'mode': stat.S_IMODE(s.st_mode), 'text': raw.decode(), 'sha256': sha(raw)}
        except FileNotFoundError:
            return {'kind': 'absent'}

    def write(self, name, node):
        require(name in self.allowed or self.control_prefix and name.startswith(self.control_prefix + '/'),
                'Unapproved write: ' + name)
        require(node['kind'] in ('absent', 'file', 'symlink'), 'Invalid node')
        uid, gid = self.owners.get(name, (0, 0))
        with self.parent(name, create=True) as (fd, leaf):
            temporary = '.' + leaf + '.' + uuid.uuid4().hex
            try:
                if node['kind'] == 'absent':
                    try:
                        os.unlink(leaf, dir_fd=fd)
                    except FileNotFoundError:
                        pass
                else:
                    if node['kind'] == 'symlink':
                        os.symlink(node['target'], temporary, dir_fd=fd)
                        if self.live:
                            os.chown(temporary, uid, gid, dir_fd=fd, follow_symlinks=False)
                    else:
                        blob = node['text'].encode()
                        require(sha(blob) == node['sha256'], 'Write payload hash differs')
                        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                      node['mode'], dir_fd=fd)
                        with os.fdopen(out, 'wb') as f:
                            f.write(blob)
                            f.flush()
                            os.fchmod(f.fileno(), node['mode'])
                            if self.live:
                                os.fchown(f.fileno(), uid, gid)
                            os.fsync(f.fileno())
                    os.rename(temporary, leaf, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass

    def json(self, name):
        node = self.node(name)
        require(node['kind'] == 'file', 'Missing JSON: ' + name)
        return json.loads(node['text'])

    def save(self, name, value, *, immutable=False):
        if immutable and self.node(name)['kind'] != 'absent':
            require(self.json(name) == value, 'Immutable record differs: ' + name)
            return
        raw = encode(value)
        self.write(name, {'kind': 'file', 'text': raw.decode(), 'sha256': sha(raw), 'mode': 0o644})

    @contextlib.contextmanager
    def lock(self):
        # Existing common coordinator, same inode as installed guards.
        fd = os.open(self.path('/var/lib/marketdata-clock/coordinator.lock'), os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Hold('Coordinator busy; running work is not interrupted') from exc
            yield
        finally:
            os.close(fd)
