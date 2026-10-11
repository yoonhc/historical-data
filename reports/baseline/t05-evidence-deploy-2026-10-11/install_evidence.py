"""Install the approved fixed T05 evidence producer and register seven copies.

No collector/Chrony control, backup run, data deletion, or timer activation.
Existing user ExecStart commands remain until activate_evidence.py is reviewed
against this installation's result. A partial install must be inspected, not
rerun blindly. --check writes nothing; --run requires administrator credentials.
"""
import argparse
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import deploy_common as c

PHASE = 'validate'


def run(argv, *, timeout=120):
    argv = list(map(str, argv))
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd='/',
                       env={**os.environ, 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'})
    item = {'at': datetime.now(timezone.utc).isoformat(), 'argv': argv,
            'code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    if c.OPS.exists():
        fd = os.open(c.OPS / 'commands.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'wb') as f:
            f.write(json.dumps(item).encode() + b'\n'); f.flush(); os.fsync(f.fileno())
    c.require(p.returncode == 0, f'Command failed ({p.returncode}): {argv}\n{p.stdout[-4000:]}\n{p.stderr[-2000:]}')
    return p.stdout


def validate():
    inputs = json.loads((HERE / 'install-inputs.json').read_text())
    c.require(inputs['release_id'] == c.RELEASE_ID and inputs['approved_scope'] == 'install_and_register_evidence_only', 'Approval scope differs')
    for path, digest in inputs['files'].items():
        c.require(c.sha(Path(path).read_bytes()) == digest, 'Reviewed input changed: ' + path)
    before = json.loads((HERE / 'host-before.json').read_text())
    c.verify_protected(before)
    for path in (c.RELEASE, c.CONFIG, c.STATE, c.OPS):
        c.require(not path.exists() and not path.is_symlink(), 'Existing installation requires review: ' + str(path))
    manifest = json.loads((c.CANDIDATE / 'manifest.json').read_text())
    c.require(manifest['release_id'] == c.RELEASE_ID, 'Manifest differs')
    c.require(c.sha((c.CANDIDATE / 'source.tar.gz').read_bytes()) == manifest['source_archive_sha256'], 'Frozen source archive differs')
    environment = json.loads((c.CANDIDATE / 'environment-files.json').read_text())
    for name, digest in manifest['source_sha256'].items():
        c.checked_relative(name)
        c.require(c.sha((c.CANDIDATE / 'reproduced' / name).read_bytes()) == digest, 'Candidate source differs: ' + name)
    for name, value in environment.items():
        c.checked_relative(name)
        path = c.CANDIDATE / 'reproduced/.venv' / name
        actual = {'link': os.readlink(path)} if path.is_symlink() else {'sha256': c.sha(path.read_bytes())}
        c.require(actual == value, 'Candidate environment differs: ' + name)
        if 'link' in value:
            c.require(name in ('bin/python', 'bin/python3', 'bin/python3.12', 'lib64')
                      and value['link'] in ('/usr/bin/python3', 'python', 'lib'), 'Unreviewed environment symlink')
    deployment = json.loads((c.CANDIDATE / 'deployment-files.json').read_text())
    for name, digest in deployment['sha256'].items():
        c.require(c.sha((c.CANDIDATE / 'deployment' / name).read_bytes()) == digest, 'Deployment input differs: ' + name)
    preview = json.loads((c.CANDIDATE / 'system-preflight-result-5dde262e40b34192.json').read_text())
    c.require(preview['passed'] and preview['operating_unchanged'] and preview['cleanup']['paths_absent'], 'Protected system preview incomplete')
    c.require(inputs['expected_entries'] == json.loads(preview['test_state']['control/inventory.json'])['entries'], 'Approved existing-copy list differs')
    c.require(len(inputs['expected_entries']) == 7, 'Unexpected registration scope')
    for service in c.SERVICES:
        actual = c.show(service, run)
        c.verify_service(actual, c.properties(before['user_units'][service]))
    return inputs, before, manifest, environment, deployment


def seal_check(root):
    count = 0
    for path in (root, *root.rglob('*')):
        s = path.lstat()
        c.require(s.st_uid == 0, 'Non-root installed object: ' + str(path))
        if path.is_symlink():
            c.require(path.resolve() == Path('/usr/bin/python3').resolve() or path.resolve().is_relative_to(root), 'External installed symlink')
        else:
            c.require(not stat.S_IMODE(s.st_mode) & 0o022, 'Writable installed object: ' + str(path))
        count += 1
    return count


def step(name):
    global PHASE
    PHASE = name
    print(name, flush=True)


def operating_services():
    return {name: {key: c.one(values, key) for key in ('MainPID', 'InvocationID', 'ActiveState')}
            for name in ('chrony.service', 'marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service')
            for values in (c.show(name, run, user=False),)}


def install(inputs, before, manifest, environment, deployment, result):
    c.OPS.mkdir(mode=0o755)
    for name in ('install_evidence.py', 'deploy_common.py', 'inspect_installed.py', 'activate_evidence.py', 'rollback_evidence.py',
                 'install-inputs.json', 'host-before.json', 'authorization.md', 'application.md', 'deployment-validation.json'):
        c.write_new(c.OPS / name, (HERE / name).read_bytes())
    c.write_new(c.OPS / 'manifest.json', (c.CANDIDATE / 'manifest.json').read_bytes())
    c.write_new(c.OPS / 'environment-files.json', (c.CANDIDATE / 'environment-files.json').read_bytes())
    c.write_new(c.OPS / 'deployment-files.json', (c.CANDIDATE / 'deployment-files.json').read_bytes())
    result['operating_services_before'] = operating_services()
    c.require(all(v['ActiveState'] == 'active' and int(v['MainPID']) > 0 for v in result['operating_services_before'].values()), 'Operating collectors/Chrony not active')
    step('fixed_release')
    c.RELEASE.mkdir(mode=0o755)
    c.require(not os.statvfs(c.RELEASE).f_flag & os.ST_NOEXEC, 'Release filesystem is noexec')
    for name, digest in manifest['source_sha256'].items():
        blob = (c.CANDIDATE / 'reproduced' / name).read_bytes()
        c.require(c.sha(blob) == digest, 'Source changed while copying')
        c.write_new(c.RELEASE / name, blob)
    for name, value in environment.items():
        destination = c.RELEASE / '.venv' / name
        if 'link' in value:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(value['link'])
            c.sync_dir(destination.parent)
        else:
            blob = (c.CANDIDATE / 'reproduced/.venv' / name).read_bytes()
            c.require(c.sha(blob) == value['sha256'], 'Environment changed while copying')
            c.write_new(destination, blob, 0o755 if name.startswith('bin/') else 0o644)
    result['sealed_release_objects'] = seal_check(c.RELEASE)
    for name, digest in manifest['source_sha256'].items():
        c.require(c.sha((c.RELEASE / name).read_bytes()) == digest, 'Installed source mismatch')
    for name, value in environment.items():
        path = c.RELEASE / '.venv' / name
        c.require(({'link': os.readlink(path)} if path.is_symlink() else {'sha256': c.sha(path.read_bytes())}) == value, 'Installed environment mismatch')

    step('configuration_and_masked_system_units')
    c.CONFIG.mkdir(mode=0o755)
    for name in deployment['sha256']:
        if name == 'recurring.json':
            continue  # Derive a disabled grant for the actual installed scope.
        c.write_new(c.CONFIG / name, (c.CANDIDATE / 'deployment' / name).read_bytes())
    c.STATE.mkdir(mode=0o700)
    os.chown(c.STATE, 1000, 1000)
    c.sync_dir(c.STATE.parent)
    python = c.RELEASE / '.venv/bin/python'
    for source_mode in (False, True):
        argv = [python, '-B', '-I', c.OPS / 'inspect_installed.py', c.RELEASE, c.CONFIG / 'settings.toml']
        if source_mode:
            argv.append('--source-entrypoint')
        inspected = json.loads(run(c.user_command(argv)))
        for module, value in inspected['imports'].items():
            original = 'packages/marketdata/src/' + module.replace('.', '/') + '.py'
            c.require(value['sha256'] == manifest['source_sha256'][original], 'Actual import differs')
        c.write_new(c.OPS / ('source-imports.json' if source_mode else 'installed-imports.json'), c.blob(inspected))
    proposal = json.loads(run(c.user_command([python, '-B', '-I', c.RELEASE / 'scripts/run_clock_retirement.py',
                                            '--settings', c.CONFIG / 'settings.toml', 'propose'])))
    c.require(proposal['enabled'] is False and proposal['purpose'] == 'system-clock-bounded-checkpoint-retirement-v2', 'Unexpected deletion grant')
    c.write_new(c.CONFIG / 'recurring.json', c.blob(proposal))
    result['disabled_retirement_scope_id'] = proposal['scope_id']
    for name in c.SYSTEM_UNITS:
        path = Path('/etc/systemd/system') / name
        c.require(not path.exists() and not path.is_symlink(), 'System unit already exists')
        path.symlink_to('/dev/null' if name in c.RETIREMENT else c.CONFIG / 'units' / name)
        c.sync_dir(path.parent)
    run(['/usr/bin/systemd-analyze', 'verify', *[c.CONFIG / 'units' / name for name in c.SYSTEM_UNITS]])
    run(['/usr/bin/systemctl', '--system', 'daemon-reload'])
    for name in c.RETIREMENT:
        unit = c.show(name, run, user=False)
        c.require(c.one(unit, 'LoadState') == 'masked' and c.one(unit, 'ActiveState') == 'inactive', 'System retirement not held')
    c.require(not (c.STATE / 'evidence').exists(), 'Unexpected existing registry')

    step('current_inventory_and_explicit_registration')
    entrypoint = [python, '-B', '-I', c.RELEASE / 'scripts/run_evidenced_backup.py', '--settings', c.CONFIG / 'settings.toml']
    inventory = json.loads(run(c.user_command([*entrypoint, 'inventory']), timeout=300))
    c.require(inventory['entries'] == inputs['expected_entries'], 'Existing-copy inventory changed; review before registration')
    c.require(inventory['scope'] == inputs['archive_identity'], 'Archive identity changed')
    c.write_new(c.OPS / 'inventory.json', c.blob(inventory))
    approval = {'schema': 1, 'enabled': True, 'purpose': 'checkpoint-evidence-registration-v1',
                'action': 'register', 'plan_id': inventory['plan_id'],
                'approval_reference': str(c.OPS / 'authorization.md'),
                'approved_at': datetime.now(timezone.utc).isoformat(),
                'release_id': c.RELEASE_ID, 'expected_entries': inputs['expected_entries'],
                'deletion_authority': False}
    c.write_new(c.OPS / 'registration-approval.json', c.blob(approval))
    registered = json.loads(run(c.user_command([*entrypoint, 'register', '--plan', c.OPS / 'inventory.json',
                                                '--approval', c.OPS / 'registration-approval.json']), timeout=300))
    c.require(registered.get('registered') is True and registered['deletion_authority'] is False, 'Registration did not complete')
    c.require(set(registered['results']) == {x['backup_id'] for x in inputs['expected_entries']}, 'Registered targets differ')
    c.write_new(c.OPS / 'registration-result.json', c.blob(registered))
    inspected = json.loads(run(c.user_command([python, '-B', '-I', c.OPS / 'inspect_installed.py', c.RELEASE,
                                               c.CONFIG / 'settings.toml', '--registry'])))
    c.require(inspected['registry']['entries'] == inventory['entries'], 'Registered baseline differs')
    for bid, value in registered['results'].items():
        proof = inspected['registry']['proofs'][bid]
        if value['state'] == 'available':
            c.require(proof and proof['object_id'] == value['object_id'], 'Registration readback differs')
        else:
            c.require(value['state'] == 'missing' and proof is None, 'Missing witness hidden')
    c.write_new(c.OPS / 'registered-readback.json', c.blob(inspected))
    result['registration'] = {'plan_id': inventory['plan_id'], 'baseline_id': inspected['registry']['baseline_id'],
        'available': sum(x['state'] == 'available' for x in registered['results'].values()),
        'missing': sum(x['state'] == 'missing' for x in registered['results'].values()), 'deletion_authority': False}

    step('installed_pending_user_entrypoint_activation')
    c.verify_protected(before, installed=True)
    for service in c.SERVICES:
        c.verify_service(c.show(service, run), c.properties(before['user_units'][service]))
        c.require(not (c.USER_UNITS / (service + '.d') / c.DROPIN).exists(), 'Unexpected activation')
    for name in c.RETIREMENT:
        c.require(os.readlink(c.USER_UNITS / name) == '/dev/null', 'User retirement mask changed')
    result['installed_config_files'] = {str(p.relative_to(c.CONFIG)): c.sha(p.read_bytes()) for p in c.CONFIG.rglob('*') if p.is_file()}
    result['system_units'] = {name: os.readlink(Path('/etc/systemd/system') / name) for name in c.SYSTEM_UNITS}
    result['protected_files_unchanged'] = True
    result['operating_services_after'] = operating_services()
    c.require(result['operating_services_before'] == result['operating_services_after'], 'Operating service invocation changed during install')
    result['passed'] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--run', action='store_true')
    args = parser.parse_args()
    values = validate()
    if args.check:
        print(json.dumps({'inputs_valid': True, 'release_id': c.RELEASE_ID, 'expected_checkpoints': 7, 'operating_writes': False}))
        return
    c.require(os.geteuid() == 0, 'Administrator authentication required')
    os.umask(0o022)
    result = {'release_id': c.RELEASE_ID, 'passed': False, 'started_at': datetime.now(timezone.utc).isoformat(),
              'clock_control': False, 'checkpoint_deletion': False, 'automatic_retirement': 'masked',
              'user_entrypoints_changed': False, 'backup_run_requested': False}
    try:
        install(*values, result)
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    result.update(phase=PHASE, finished_at=datetime.now(timezone.utc).isoformat())
    if c.OPS.exists():
        c.write_new(c.OPS / 'result.json', c.blob(result))
    print(json.dumps(result, indent=2), flush=True)
    print('Root evidence: ' + str(c.OPS / 'result.json'), flush=True)
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
