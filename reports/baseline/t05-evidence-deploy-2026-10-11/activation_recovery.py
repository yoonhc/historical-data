"""Resume the approved two-entrypoint activation after dependency-order failure.

No release/config/registry changes, new backup, deletion or clock control.
Default is validation only. --rollback reverses only the exact two drop-ins.
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
import activate_evidence as original
from dependency_reader import install_reader

RESULT = HERE / 'activation-recovery-result.json'
EVENTS = HERE / 'activation-recovery-events.jsonl'


def run(argv):
    p = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=60)
    value = {'at': datetime.now(timezone.utc).isoformat(), 'argv': list(map(str, argv)),
             'code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    with EVENTS.open('a') as f:
        f.write(json.dumps(value) + '\n')
        f.flush()
        os.fsync(f.fileno())
    c.require(p.returncode == 0, 'Command failed: ' + str(argv) + '\n' + p.stderr)
    return p.stdout


def provenance():
    failed = json.loads((HERE / 'activation-result.json').read_text())
    c.require(failed['passed'] is False and failed['phase'] == 'activation_failed'
              and failed['error'] == 'RuntimeError: Service property differs: After'
              and failed['rollback_error'] == 'RuntimeError: Service property differs: After',
              'Unexpected original failure; review before recovery')
    for name in ('deploy_common.py', 'activate_evidence.py', 'rollback_evidence.py'):
        c.require((HERE / name).read_bytes() == (c.OPS / name).read_bytes(),
                  'Original root-preserved deployment tool changed')
    validation = json.loads((HERE / 'recovery-validation.json').read_text())
    c.require(validation['passed'], 'Recovery validation incomplete')
    for name, digest in validation['tested_sha256'].items():
        c.require(c.sha((HERE / name).read_bytes()) == digest, 'Recovery code changed after test')
    return {name: c.sha((HERE / name).read_bytes()) for name in (
        'activation-result.json', 'activation-events.jsonl', 'failed-activation-readback.json',
        'activation_recovery.py', 'dependency_reader.py', 'recovery-validation.json')}


def rollback_validation():
    installed = json.loads((c.OPS / 'result.json').read_text())
    activated = json.loads(RESULT.read_text())
    c.require(installed['passed'] and activated['passed'], 'Successful installation/activation required')
    for name, digest in activated['provenance_sha256'].items():
        c.require(c.sha((HERE / name).read_bytes()) == digest, 'Activation provenance changed')
    before = json.loads((c.OPS / 'host-before.json').read_text())
    c.verify_protected(before, installed=True)
    for name, digest in installed['installed_config_files'].items():
        c.require(c.sha((c.CONFIG / name).read_bytes()) == digest, 'Installed configuration changed')
    current = {n: c.show(n, run) for n in c.SERVICES}
    expected = activated['installed_dropins']
    paths = {str(c.USER_UNITS / (n + '.d') / c.DROPIN) for n in c.SERVICES}
    c.require(set(expected) == paths, 'Rollback path set differs')
    for service, actual in current.items():
        c.verify_service(actual, activated['before'][service], c.expected_command(service))
    for name, digest in expected.items():
        path = Path(name)
        c.require(not path.is_symlink() and c.sha(path.read_bytes()) == digest,
                  'Changed drop-in requires review: ' + name)
    original.verify_timers(before)
    return before, current, expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    c.require(os.geteuid() == 1000, 'Run as yoonhc')
    install_reader()
    original.run = run
    hashes = provenance()
    result_path = HERE / 'activation-recovery-rollback-result.json' if args.rollback else RESULT
    if args.run:
        c.require(not result_path.exists(), 'Prior recovery result requires review')
    if args.rollback:
        before, actual, expected = rollback_validation()
    else:
        before, actual = original.validate()
        original.verify_timers(before)
        expected = {}
    if not args.run:
        print(json.dumps({'validated': True, 'rollback': args.rollback,
                          'idle': all(c.idle(v) for v in actual.values()),
                          'provenance_sha256': hashes}, indent=2))
        return
    if not all(c.idle(v) for v in actual.values()):
        value = {'deferred': 'backup_or_archiver_executing', 'units': actual, 'operating_changed': False}
        c.write_new(HERE / ('recovery-deferred-' + uuid.uuid4().hex + '.json'), c.blob(value))
        print(json.dumps({'deferred': value['deferred'], 'operating_changed': False}))
        raise SystemExit(75)
    result = {'passed': False, 'release_id': c.RELEASE_ID, 'rollback': args.rollback,
              'started_at': datetime.now(timezone.utc).isoformat(), 'before': actual,
              'queued_jobs_before': {n: c.one(v, 'Job', '') for n, v in actual.items()},
              'provenance_sha256': hashes, 'scope': 'two user ExecStart drop-ins only',
              'clock_control': False, 'automatic_retirement': 'masked',
              'backup_run_requested': False, 'evidence_retained': True, 'data_retained': True}
    created = []
    try:
        if args.rollback:
            original.remove_own_dropins([Path(p) for p in sorted(expected)], expected)
        else:
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
            previous = c.properties(before['user_units'][service]) if args.rollback else actual[service]
            c.verify_service(new, previous, None if args.rollback else c.expected_command(service))
            result['after'][service] = new
        result['timers_after'] = original.verify_timers(before)
        c.verify_protected(before, installed=True)
        result.update(passed=True, installed_dropins=expected,
                      phase='entrypoints_rolled_back' if args.rollback else 'activated_next_scheduled_backup_pending')
    except Exception as exc:
        result.update(error=f'{type(exc).__name__}: {exc}', phase='recovery_failed')
        if created:
            try:
                original.remove_own_dropins(created, expected)
                run(['/usr/bin/systemctl', '--user', 'daemon-reload'])
                for service in c.SERVICES:
                    c.verify_service(c.show(service, run), actual[service])
                result['entrypoints_rolled_back'] = True
            except Exception as rollback_exc:
                result['rollback_error'] = f'{type(rollback_exc).__name__}: {rollback_exc}'
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    c.write_new(result_path, c.blob(result))
    print(json.dumps({k: v for k, v in result.items() if k not in ('before', 'after', 'timers_after')}, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
