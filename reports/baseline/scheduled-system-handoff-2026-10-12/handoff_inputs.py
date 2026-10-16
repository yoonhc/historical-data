"""Seal validation, fixed environment copy, and guarded live entrypoint."""
import fcntl
import json
import os
import stat
import subprocess
from pathlib import Path

import handoff_common as c

def load_checked(path):
    seal = json.loads((c.HERE / 'tool-seal.json').read_bytes())
    for name, digest in seal['files'].items():
        c.require(c.sha((c.HERE / name).read_bytes()) == digest, 'Handoff tool changed: ' + name)
    for name, digest in seal['base_files'].items():
        c.require(c.sha((c.BASE / name).read_bytes()) == digest, 'Fixed candidate evidence changed: ' + name)
    manifest = json.loads((c.BASE / 'manifest.json').read_bytes())
    for name, digest in manifest['source_sha256'].items():
        c.require(c.sha((c.BASE / 'candidate' / name).read_bytes()) == digest, 'Fixed source changed: ' + name)
    plan = json.loads(Path(path).read_bytes())
    c.require(plan['tool_id'] == seal['tool_id'], 'Plan/tool mismatch')
    c.require(plan['inner'] == json.loads((c.HERE / 'configuration-plan.json').read_bytes()), 'Embedded plan differs')
    c.require(plan['plan_id'] == c.sha(c.encode({k: v for k, v in plan.items() if k != 'plan_id'})), 'Host plan hash differs')
    return plan

def environment_files():
    env = json.loads((c.BASE / 'environment.json').read_bytes())
    manifest = json.loads((c.BASE / 'manifest.json').read_bytes())
    files = {name: {'sha256': value, 'source': c.BASE / 'candidate' / name, 'mode': 0o644}
             for name, value in manifest['source_sha256'].items()}
    files.update({'.venv/' + name: {'sha256': value, 'source': c.BASE / 'candidate/.venv' / name,
                 'mode': 0o755 if name.startswith('bin/') else 0o644}
                  for name, value in env['venv_files'].items()})
    files.update({'.venv/' + name: {'link': value} for name, value in env['venv_links'].items()})
    return files

def install_release(destination, *, live=True):
    """Copies only sealed files; no resolver, uv, cache hardlinks or network.

    Resumes only byte-identical partial installs owned by this installation.
    Final UID/mode hardening happens before any entrypoint is run.
    """
    destination = Path(destination)
    entries = environment_files()
    marker = {'purpose': 't15-sealed-release-copy-v1', 'source_manifest': c.sha((c.BASE / 'manifest.json').read_bytes())}
    if destination.exists():
        c.require(not destination.is_symlink() and (destination / '.install-intent.json').is_file(), 'Unrecognised existing release')
        c.require(json.loads((destination / '.install-intent.json').read_bytes()) == marker, 'Install identity differs')
    else:
        # Parent must already be an ordinary trusted directory.
        for parent in (destination.parent, *destination.parent.parents):
            c.require(not parent.is_symlink(), 'Symlink release parent')
            if live:
                m = c.metadata(parent)
                c.require(m['uid'] == 0 and not m['mode'] & 0o022, 'Untrusted release parent')
        destination.mkdir(mode=0o755)
        fd = os.open(destination / '.install-intent.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(c.encode(marker)); stream.flush(); os.fsync(stream.fileno())
    for p in destination.rglob('*'):
        name = str(p.relative_to(destination))
        if p.is_dir() and not p.is_symlink():
            c.require(any(n.startswith(name + '/') for n in entries), 'Unexpected release directory: ' + name)
        else:
            c.require(name in entries or name == '.install-intent.json', 'Unexpected release object: ' + name)
        if live:
            m = c.metadata(p)
            c.require(m['uid'] == 0 and (m['kind'] == 'symlink' or not m['mode'] & 0o022), 'Untrusted installed object')
    for name, spec in entries.items():
        p = destination / name
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if 'link' in spec:
            c.require(spec['link'] in ('/usr/bin/python3', 'python', 'lib'), 'Unexpected interpreter link')
            if not p.is_symlink() and not p.exists():
                p.symlink_to(spec['link'])
            c.require(p.is_symlink() and os.readlink(p) == spec['link'], 'Installed link differs')
            continue
        raw = spec['source'].read_bytes()
        c.require(c.sha(raw) == spec['sha256'], 'Environment source changed: ' + name)
        if not p.exists() and not p.is_symlink():
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, spec['mode'])
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fchmod(stream.fileno(), spec['mode']); os.fsync(stream.fileno())
        c.require(not p.is_symlink() and c.sha(p.read_bytes()) == spec['sha256'], 'Installed bytes differ: ' + name)
        c.require(stat.S_IMODE(p.stat().st_mode) == spec['mode'], 'Installed mode differs: ' + name)
    # Durable copy, then users may import only through explicit .venv/bin/python.
    for directory in [p for p in destination.rglob('*') if p.is_dir() and not p.is_symlink()] + [destination, destination.parent]:
        fd = os.open(directory, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return {'files': len(entries), 'source_release_unchanged': True}

def live_execution(plan, args):
    from handoff import Handoff, Host, STATE, validate_outer
    c.require(os.geteuid() == 0, 'Administrator authentication required')
    c.require(args.approval is not None, 'Exact root-owned approval file required')
    info = c.metadata(args.approval)
    c.require(info['kind'] == 'file' and info['uid'] == 0 and not info['mode'] & 0o022, 'Approval authority invalid')
    approval = json.loads(args.approval.read_bytes())
    validate_outer(plan, approval)
    pre = plan.get('preflight')
    c.require(pre is not None, 'Administrator preflight has not passed')
    report = Path(pre['report'])
    c.require(c.sha(report.read_bytes()) == pre['sha256'], 'Preflight receipt changed')
    checked = json.loads(report.read_bytes())
    c.require(checked['passed'] and checked['operating_unchanged'] and checked['tool_id'] == plan['tool_id'], 'Preflight does not cover this tool')
    c.require(c.boot() == plan['boot_id'], 'Plan is for another boot')
    side = 'after' if args.action == 'apply' else 'before'
    owners = {p: (v[side].get('uid', 0), v[side].get('gid', 0)) for p, v in plan['ownership'].items()}
    inner_directory = '/var/lib/marketdata-clock/scheduled-transitions/' + plan['inner']['plan_id']
    allowed = {*plan['inner']['operations'], *plan['enable_links'], '/var/lib/marketdata-clock/boot-state.json'}
    allowed.update(inner_directory + '/' + n for n in ('plan.json', 'approval.json', 'progress.json', 'apply-receipt.json', 'rollback-receipt.json'))
    fs = c.SafeFiles('/', allowed, owners, control_prefix=STATE + '/' + plan['plan_id'])
    directory = STATE + '/' + plan['plan_id']
    fs.save(directory + '/plan.json', plan, immutable=True)
    # Serialize entire handoff separately from the shared clock coordinator.
    lockpath = directory + '/handoff.lock'
    if fs.node(lockpath)['kind'] == 'absent':
        fs.write(lockpath, {'kind': 'file', 'text': '', 'sha256': c.sha(b''), 'mode': 0o600})
    fd = os.open(lockpath, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        events = fs.json(directory + '/commands.json') if fs.node(directory + '/commands.json')['kind'] != 'absent' else []
        def record(value):
            value['boottime_ns'] = __import__('time').clock_gettime_ns(__import__('time').CLOCK_BOOTTIME)
            events.append(value)
            fs.save(directory + '/commands.json', events)
        managers = c.Managers(allowed=[(m, n) for m in ('user', 'system') for n in (*c.SERVICES, *c.TIMERS, c.WAITER)], record=record)
        host = Host(plan, fs, managers)
        def install():
            root = Path('/opt/marketdata/releases') / plan['inner']['release_id']
            result = install_release(root)
            # Read-only imports and actual CLI startup at the final fixed path.
            probe = subprocess.run(c.user_command([root / '.venv/bin/python', '-B', '-I', '-m', 'marketdata.cli', '--help']),
                capture_output=True, text=True, timeout=30)
            c.require(probe.returncode == 0, 'Installed CLI import failed: ' + probe.stderr)
            fs.save(directory + '/installed.json', result)
        executor = Handoff(plan, approval, fs, managers, host, installer=install)
        result = executor.run(args.action, drain_seconds=args.drain_seconds)
        print(json.dumps(result['result'], indent=2))
    finally:
        os.close(fd)
