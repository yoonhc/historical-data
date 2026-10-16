"""Build source/unit/config candidate only. No systemctl, Store, network or deployment."""
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CANDIDATE = HERE / 'candidate'
sys.path.insert(0, str(CANDIDATE / 'migration'))
from systemd_read import commands, parse

NAMES = ('oi', 'oi-probe', 'oi-quality', 'daily', 'pack', 'backup', 'restic-archive', 'network-observer')
BOOT = Path('/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee')
T05 = Path('/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7')
PROFILE = '/etc/marketdata/clock-bootstrap.json'
TOPOLOGY = '/etc/marketdata/scheduled-system/topology.json'

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def encode(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode() + b'\n'

def file_node(text, mode=0o644):
    return {'kind': 'file', 'mode': mode, 'sha256': sha(text.encode()), 'text': text}

def actual_node(path):
    p = Path(path)
    if p.is_symlink():
        return {'kind': 'symlink', 'target': os.readlink(p)}
    if p.exists():
        return file_node(p.read_text(), p.stat().st_mode & 0o777)
    return {'kind': 'absent'}

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(value))

def main():
    baseline = json.loads((HERE / 'baseline.json').read_text())
    # Freeze existing workspace application bytes, without importing its Store.
    names = ['pyproject.toml', 'uv.lock', 'scripts/network_observer.py']
    for package in sorted((REPO / 'packages').iterdir()):
        if (package / 'pyproject.toml').exists():
            names.append(str((package / 'pyproject.toml').relative_to(REPO)))
            names.extend(str(p.relative_to(REPO)) for p in sorted((package / 'src').rglob('*.py')))
    workspace = {}
    for name in names:
        source, target = REPO / name, CANDIDATE / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        workspace[name] = sha(source.read_bytes())
    parent_files = {}
    for directory in (T05 / 'scripts', T05 / 'packages', BOOT / 'integrations/runtime'):
        for path in sorted(directory.rglob('*.py')):
            parent_files[str(path)] = sha(path.read_bytes())
    parents = {'gate': str(BOOT / 'integrations/runtime/clock_gate.py'), 't05': str(T05),
               'files': parent_files}
    write_json(CANDIDATE / 'migration/parents.json', parents)
    release_names = names + [str(p.relative_to(CANDIDATE))
        for p in (CANDIDATE / 'migration').glob('*.py')] + ['migration/parents.json']
    source_files = {name: sha((CANDIDATE / name).read_bytes()) for name in sorted(release_names)}
    release_id = 'scheduled-manager-20261012-' + sha(encode(source_files))[:16]
    release = '/opt/marketdata/releases/' + release_id
    py = release + '/.venv/bin/python -B -I'
    old_p = json.loads(baseline['files'][PROFILE]['text'])
    p = json.loads(json.dumps(old_p))
    units, specs, operations = {}, {}, {}

    def install(path, text):
        before = baseline['files'].get(path, actual_node(path))
        after = file_node(text)
        if before != after:
            operations[path] = {'before': before, 'after': after}

    netenv = Path('/home/yoonhc/.config/marketdata/network-observer.env').read_text()
    env = dict(line.split('=', 1) for line in netenv.splitlines() if line and not line.startswith('#'))
    netprofile = env['MARKETDATA_NETWORK_PROFILE'].strip('"\'')
    assert Path(netprofile).is_absolute() and not any(c in netprofile for c in '\n\r;$% ')
    cmd = {}
    for name in NAMES:
        unit = f'marketdata-{name}.service'
        observed = baseline['effective_user_units'][unit]
        old = commands(parse(observed['show'])['ExecStart'])
        assert len(old) == 1
        cmd[name] = old[0]
        if name in ('oi', 'oi-probe', 'oi-quality', 'pack'):
            cmd[name] = old[0].replace(str(REPO / '.venv/bin/marketdata'), py + ' -m marketdata')
            cmd[name] = cmd[name].replace('--root .', '--root ' + str(REPO))
            cmd[name] = cmd[name].replace('--config configs/oi-live.toml',
                                         '--config /etc/marketdata/scheduled-system/oi-live.toml')
        if name == 'network-observer':
            cmd[name] = py + ' ' + release + '/scripts/network_observer.py --profile ' + netprofile
        if name == 'restic-archive':
            cmd[name] = (str(T05 / '.venv/bin/python') + ' -B -I ' + release +
                '/migration/run_system_archive.py --profile /home/yoonhc/.config/marketdata/restic-archive.toml run')
        text = observed['base'].replace('%h', '/home/yoonhc')
        for drop in observed['dropins'].values():
            text += '\n' + drop
        text += ('\n[Service]\nUser=yoonhc\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n'
                 'ExecStart=\nExecStart=' + cmd[name] + '\nProtectClock=yes\n')
        path = '/etc/systemd/system/' + unit
        units[unit] = text
        specs[unit] = {'kind': 'work', 'fragment': path, 'User': 'yoonhc',
            'WorkingDirectory': '/home/yoonhc' if name in ('network-observer', 'restic-archive') else str(REPO),
            'ExecStart': [cmd[name]], 'ExecStartPre': [p['guard_command']],
            'Requires': [p['wait_unit']], 'After': [p['wait_unit']]}
        install(path, text)
        timer = f'marketdata-{name}.timer'
        units[timer] = baseline['effective_user_units'][timer]['base']
        install('/etc/systemd/system/' + timer, units[timer])
        specs[timer] = {'kind': 'timer', 'fragment': '/etc/systemd/system/' + timer,
                        'Unit': unit}
        for item in (unit, timer):
            source = '/home/yoonhc/.config/systemd/user/' + item
            operations[source] = {'before': baseline['files'][source],
                                  'after': {'kind': 'symlink', 'target': '/dev/null'}}
            p['user_units'][item] = 'masked'
        wants = '/home/yoonhc/.config/systemd/user/timers.target.wants/' + timer
        operations[wants] = {'before': baseline['files'][wants], 'after': {'kind': 'absent'}}

    waiter_path = '/etc/systemd/system/' + p['wait_unit']
    waiter = baseline['files']['/etc/systemd/user/' + p['wait_unit']]['text']
    waiter += '\n[Service]\nUser=yoonhc\nProtectClock=yes\n'
    units[p['wait_unit']] = waiter
    install(waiter_path, waiter)
    specs[p['wait_unit']] = {'kind': 'waiter', 'fragment': waiter_path, 'User': 'yoonhc',
                             'ExecStart': [p['wait_command']]}
    bootstrap_path = '/etc/systemd/system/marketdata-clock-bootstrap.service'
    old_bootstrap = baseline['files'][bootstrap_path]['text']
    old_script = str(BOOT / 'integrations/runtime/clock_gate.py')
    bootstrap = old_bootstrap.replace(old_script + ' bootstrap',
                                      release + '/migration/bootstrap_system.py bootstrap')
    install(bootstrap_path, bootstrap)
    units['marketdata-clock-bootstrap.service'] = bootstrap
    inventory = {line.split()[0] for line in baseline['system_inventory'].splitlines() if line}
    inventory.update(specs)
    topology = {'schema': 1, 'purpose': 'scheduled-manager-only-v1',
                'system_inventory': sorted(inventory), 'units': specs,
                'policy_changes': [], 'retirement': 'both managers remain masked'}
    install(TOPOLOGY, encode(topology).decode())
    install('/etc/marketdata/scheduled-system/oi-live.toml', (REPO / 'configs/oi-live.toml').read_text())
    for name, content in units.items():
        p['guard_files']['/etc/systemd/system/' + name] = sha(content.encode())
    p['guard_files'][TOPOLOGY] = sha(encode(topology))
    install(PROFILE, encode(p).decode())
    binding_path = '/etc/marketdata/checkpoint-clock-retirement/clock-binding.json'
    binding = {'bootstrap_profile': PROFILE, 'bootstrap_profile_sha256': sha(encode(p))}
    install(binding_path, encode(binding).decode())
    protected = {path: value for path, value in baseline['files'].items()
                 if path not in operations and not path.startswith('/home/yoonhc/.config/systemd/user/')}
    # Pin external settings and environment files by digest only; never bundle credentials.
    for path in ['/etc/marketdata/daily-scheduler.toml', '/etc/marketdata/daily-publication-probe.toml',
                 '/home/yoonhc/.config/marketdata/restic-archive.toml',
                 '/home/yoonhc/.config/marketdata/restic-service.env',
                 '/home/yoonhc/.config/marketdata/network-observer.env', netprofile]:
        protected[path] = {'kind': 'digest', 'sha256': sha(Path(path).read_bytes())}
    plan = {'schema': 1, 'purpose': 'scheduled-manager-only-v1', 'release_id': release_id,
        'boot_id': baseline['boot_id'], 'ledger_before': baseline['ledger'],
        'bootstrap_path': PROFILE, 'ledger_path': '/var/lib/marketdata-clock/boot-state.json',
        'operations': operations, 'protected': protected,
        'retirement_masks': baseline['retirement_masks'], 'topology': topology,
        'source_sha256': source_files,
        'approval_required': 'new exact-plan apply and rollback authorization',
        'service_control': 'external reviewed handoff; transaction never starts or stops services',
        'policy_changes': []}
    plan['plan_id'] = sha(encode(plan))
    write_json(HERE / 'plan.json', plan)
    write_json(HERE / 'approval.template.json', {'purpose': plan['purpose'], 'plan_id': plan['plan_id'],
               'enabled': False, 'directions': ['apply', 'rollback'], 'approval_reference': None})
    for name, text in units.items():
        dest = HERE / 'rendered/units' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    write_json(HERE / 'rendered/topology.json', topology)
    write_json(HERE / 'rendered/clock-bootstrap.json', p)
    write_json(HERE / 'rendered/clock-binding.json', binding)
    write_json(HERE / 'manifest.json', {'release_id': release_id, 'source_sha256': source_files,
        'workspace_identical_sha256': workspace, 'parent_files': parent_files,
        'plan_id': plan['plan_id'], 'contract_sha256': sha((REPO / 'docs/scheduled-jobs-system-migration-contract.md').read_bytes())})
    print(json.dumps({'release_id': release_id, 'plan_id': plan['plan_id'],
                      'operations': len(operations), 'source_files': len(source_files),
                      'workspace_unchanged': len(workspace)}))

if __name__ == '__main__':
    main()
