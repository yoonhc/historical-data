"""Narrow T05 installation helpers; no clock control or checkpoint deletion."""

import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CANDIDATE = HERE.parent / "t05-completion-evidence-2026-10-11"
RELEASE_ID = "t05-completion-evidence-20261011-c9d413cfe11ec0e7"
RELEASE = Path("/opt/marketdata/releases") / RELEASE_ID
CONFIG = Path("/etc/marketdata/checkpoint-clock-retirement")
STATE = Path("/var/lib/marketdata-checkpoint-retirement")
OPS = Path("/var/lib/marketdata-t05-evidence-deploy-20261011")
USER_UNITS = Path("/home/yoonhc/.config/systemd/user")
SERVICES = ("marketdata-backup.service", "marketdata-restic-archive.service")
RETIREMENT = ("marketdata-checkpoint-retirement.service", "marketdata-checkpoint-retirement.timer")
SYSTEM_UNITS = RETIREMENT + ("marketdata-checkpoint-retirement-preview.service", "marketdata-checkpoint-retirement-apply@.service")
DROPIN = "80-checkpoint-evidence.conf"
STATIC = ("FragmentPath", "After", "Requires", "Restart", "RestartUSec", "KillMode", "KillSignal", "TimeoutStartUSec", "TimeoutStopUSec", "WorkingDirectory", "User", "Group", "NoNewPrivileges", "ProtectClock", "ProtectSystem", "ProtectHome", "ReadWritePaths", "CapabilityBoundingSet", "AmbientCapabilities", "PrivateTmp", "RestrictAddressFamilies", "UnitFileState")
PROPERTIES = ",".join(STATIC + ("Id", "LoadState", "ActiveState", "SubState", "MainPID", "ControlPID", "Job", "InvocationID", "NeedDaemonReload", "DropInPaths", "ExecStart", "ExecStartPre", "Persistent", "TimersCalendar", "TimersMonotonic", "AccuracyUSec", "RandomizedDelayUSec", "NextElapseUSecRealtime"))


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def record(path):
    p = Path(path)
    if p.is_symlink():
        return {"link": os.readlink(p)}
    if not p.exists():
        return {"absent": True}
    s = p.stat()
    value = {"mode": oct(stat.S_IMODE(s.st_mode)), "uid": s.st_uid}
    return ({"directory": True, "entries": sorted(x.name for x in p.iterdir()), **value}
            if p.is_dir() else {"sha256": sha(p.read_bytes()), **value})


def write_new(path, blob, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    sync_dir(path.parent)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_new(path, data):
    """Publish one complete new drop-in without replacing any existing file."""
    path = Path(path)
    temporary = path.with_name('.t05-pending-' + uuid.uuid4().hex)
    try:
        write_new(temporary, data)
        os.link(temporary, path, follow_symlinks=False)
        sync_dir(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
            sync_dir(temporary.parent)


def blob(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def properties(raw):
    # Same lossless representation as the validated T13 activation reader.
    result = {}
    for line in raw.splitlines():
        if not line:
            continue
        require("=" in line, "Malformed systemctl property")
        key, value = line.split("=", 1)
        require(key not in result or key in ("ExecStart", "ExecStartPre"), "Repeated scalar: " + key)
        result.setdefault(key, []).append(value)
    return result


def one(values, key, default=None):
    value = values.get(key, [] if default is None else [default])
    require(len(value) == 1, "Missing/ambiguous scalar: " + key)
    return value[0]


def commands(values, key):
    return [x.strip() for value in values.get(key, [])
            for x in re.findall(r"argv\[\]=(.*?) ; ignore_errors=", value)]


def user_command(argv):
    if os.geteuid() == 0:
        return ["/usr/sbin/runuser", "-u", "yoonhc", "--", "/usr/bin/env",
                "XDG_RUNTIME_DIR=/run/user/1000", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus", *map(str, argv)]
    require(os.geteuid() == 1000, "Expected yoonhc or administrator")
    return list(map(str, argv))


def show(unit, run, *, user=True):
    argv = ["/usr/bin/systemctl", "--user" if user else "--system", "show", unit, "--property=" + PROPERTIES]
    return properties(run(user_command(argv) if user else argv))


def idle(values):
    # A job waiting on the existing time gate is not an executing backup.
    return (one(values, "ActiveState") == "inactive"
            and one(values, "MainPID", "0") == "0"
            and one(values, "ControlPID", "0") == "0")


def verify_service(actual, previous, expected_command=None):
    expected_pre = commands(previous, "ExecStartPre")
    require(bool(expected_pre) and commands(actual, "ExecStartPre") == expected_pre,
            "Effective strict start checks differ")
    require(commands(actual, "ExecStart") == ([expected_command] if expected_command else commands(previous, "ExecStart")),
            "Effective ExecStart differs")
    for key in STATIC:
        require(actual.get(key, []) == previous.get(key, []), "Service property differs: " + key)
    old_dropins = one(previous, "DropInPaths", "").split()
    new_dropins = one(actual, "DropInPaths", "").split()
    if expected_command:
        expected_path = str(USER_UNITS / (one(actual, "Id") + ".d") / DROPIN)
        require(sorted(new_dropins) == sorted(old_dropins + [expected_path]), "Unexpected drop-in set")
    else:
        require(new_dropins == old_dropins, "Previous drop-ins differ")
    require(one(actual, "NeedDaemonReload") == "no", "Manager still needs reload")


def allowed_paths():
    return {str(CONFIG), str(STATE), *[str(Path('/etc/systemd/system') / n) for n in SYSTEM_UNITS],
            *[str(USER_UNITS / (n + '.d') / DROPIN) for n in SERVICES]}


def verify_protected(before, *, installed=False):
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == before['boot_id'], 'Boot changed')
    for path, expected in before['files'].items():
        if installed and path in allowed_paths():
            continue
        require(record(path) == expected, 'Operating input changed: ' + path)


def checked_relative(value):
    p = Path(value)
    require(not p.is_absolute() and '..' not in p.parts and str(p) == value, 'Unsafe release path')
    return p


def expected_command(service):
    text = (CANDIDATE / 'deployment/user-dropins' / (service + '.d') / DROPIN).read_text()
    return next(line.removeprefix('ExecStart=') for line in text.splitlines() if line.startswith('ExecStart=') and line != 'ExecStart=')
