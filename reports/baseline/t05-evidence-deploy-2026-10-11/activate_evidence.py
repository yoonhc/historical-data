"""Switch only two inactive user-service ExecStart commands; preserve all timers.

A queued start waiting on the existing clock gate is recorded, not cancelled.
No backup is started by this tool. New evidence, the fixed release and masks
remain if exact new drop-ins are rolled back after an activation failure.
"""
import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import deploy_common as c


def run(argv):
    p = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=60)
    value = {'at': datetime.now(timezone.utc).isoformat(), 'argv': list(map(str, argv)),
             'code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    with (HERE / 'activation-events.jsonl').open('a') as f:
        f.write(json.dumps(value) + '\n'); f.flush(); os.fsync(f.fileno())
    c.require(p.returncode == 0, 'Command failed: ' + str(argv) + '\n' + p.stderr)
    return p.stdout


def remove_own_dropins(paths, expected):
    # Exact newly created files only; never remove a directory or data/state.
    for path in paths:
        c.require(not path.is_symlink() and c.sha(path.read_bytes()) == expected[str(path)],
                  'Changed activation file requires manual review: ' + str(path))
    for path in reversed(paths):
        path.unlink()
        c.sync_dir(path.parent)


def verify_timers(before):
    values = {}
    for service in c.SERVICES:
        timer = service.removesuffix('.service') + '.timer'
        actual, previous = c.show(timer, run), c.properties(before['user_units'][timer])
        for key in ('LoadState', 'ActiveState', 'UnitFileState', 'FragmentPath', 'DropInPaths',
                    'Persistent', 'AccuracyUSec', 'RandomizedDelayUSec'):
            c.require(actual.get(key) == previous.get(key), 'Timer changed: ' + timer + '/' + key)
        values[timer] = actual
    return values


def validate():
    c.require(os.geteuid() == 1000, 'Run activation as yoonhc')
    installed = json.loads((c.OPS / 'result.json').read_text())
    c.require(installed['passed'] and installed['release_id'] == c.RELEASE_ID
              and installed['phase'] == 'installed_pending_user_entrypoint_activation', 'Installation not complete')
    for name in ('deploy_common.py', 'activate_evidence.py', 'rollback_evidence.py'):
        c.require((HERE / name).read_bytes() == (c.OPS / name).read_bytes(), 'Reviewed deployment tool changed')
    before = json.loads((c.OPS / 'host-before.json').read_text())
    c.verify_protected(before, installed=True)
    for name, expected in installed['installed_config_files'].items():
        c.require(c.sha((c.CONFIG / name).read_bytes()) == expected, 'Installed configuration changed')
    manifest = json.loads((c.OPS / 'manifest.json').read_text())
    for name, expected in manifest['source_sha256'].items():
        c.require(c.sha((c.RELEASE / name).read_bytes()) == expected, 'Installed source changed')
    for name, target in installed['system_units'].items():
        c.require(os.readlink(Path('/etc/systemd/system') / name) == target, 'System unit/mask changed')
    c.require(json.loads((c.CONFIG / 'recurring.json').read_text())['enabled'] is False, 'Unexpected retirement enablement')
    actual = {}
    for service in c.SERVICES:
        actual[service] = c.show(service, run)
        c.verify_service(actual[service], c.properties(before['user_units'][service]))
        target = c.USER_UNITS / (service + '.d') / c.DROPIN
        c.require(not target.exists() and not target.is_symlink(), 'Existing drop-in requires review')
    return before, actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    c.require(not (HERE / 'activation-result.json').exists(), 'Previous activation exists; inspect before retry')
    before, actual = validate()
    if not args.run:
        print(json.dumps({'validated': True, 'idle': all(c.idle(v) for v in actual.values())}))
        return
    if not all(c.idle(v) for v in actual.values()):
        value = {'deferred': 'backup_or_archiver_executing', 'units': actual, 'operating_changed': False}
        c.write_new(HERE / ('activation-deferred-' + uuid.uuid4().hex + '.json'), c.blob(value))
        print(json.dumps({'deferred': value['deferred'], 'operating_changed': False}))
        raise SystemExit(75)
    result = {'passed': False, 'release_id': c.RELEASE_ID, 'started_at': datetime.now(timezone.utc).isoformat(),
              'before': actual, 'queued_jobs_before': {n:c.one(v, 'Job', '') for n,v in actual.items()},
              'scope': 'two user ExecStart drop-ins only', 'clock_control': False,
              'automatic_retirement': 'masked', 'backup_run_requested': False}
    created, expected = [], {}
    try:
        # No base-unit replacement or disable operation: existing symlinks stay.
        for service in c.SERVICES:
            path = c.USER_UNITS / (service + '.d') / c.DROPIN
            for parent in (path.parent, *path.parent.parents):
                c.require(not parent.is_symlink(), 'Symlink in activation path')
            data = (c.CONFIG / 'user-dropins' / (service + '.d') / c.DROPIN).read_bytes()
            expected[str(path)] = c.sha(data)
            c.publish_new(path, data)
            created.append(path)
        run(['/usr/bin/systemctl', '--user', 'daemon-reload'])
        result['after'] = {}
        for service in c.SERVICES:
            new = c.show(service, run)
            data = (c.CONFIG / 'user-dropins' / (service + '.d') / c.DROPIN).read_text()
            command = next(x.removeprefix('ExecStart=') for x in data.splitlines() if x.startswith('ExecStart=') and x != 'ExecStart=')
            c.verify_service(new, actual[service], command)
            result['after'][service] = new
        result['timers_after'] = verify_timers(before)
        c.verify_protected(before, installed=True)
        result.update(passed=True, installed_dropins=expected, phase='activated_next_scheduled_backup_pending')
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        result['phase'] = 'activation_failed'
        if created:
            try:
                remove_own_dropins(created, expected)
                run(['/usr/bin/systemctl', '--user', 'daemon-reload'])
                for service in c.SERVICES:
                    c.verify_service(c.show(service, run), actual[service])
                result['entrypoints_rolled_back'] = True
            except Exception as rollback_exc:
                result['rollback_error'] = f'{type(rollback_exc).__name__}: {rollback_exc}'
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    c.write_new(HERE / 'activation-result.json', c.blob(result))
    print(json.dumps({k:v for k,v in result.items() if k not in ('before','after','timers_after')}, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
