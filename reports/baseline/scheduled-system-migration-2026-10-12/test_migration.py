"""Real isolated filesystem transactions, old-reader compatibility, boot inventory."""
import copy
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'candidate/migration'))
import bootstrap_system
import pinned
import run_system_archive
import systemd_read as reader
import transaction as tx

PLAN = json.loads((HERE / 'plan.json').read_text())
POINTS = ['intent', 'pending', *['file:' + str(i) for i in range(len(PLAN['operations']))],
          'staged', 'receipt', 'committed']

def signed(plan):
    plan['plan_id'] = tx.sha(tx.encode({k: v for k, v in plan.items() if k != 'plan_id'}))
    return {'purpose': tx.PURPOSE, 'plan_id': plan['plan_id'], 'enabled': True,
            'approval_reference': 'isolated-test-only', 'directions': ['apply', 'rollback']}

class Host:
    busy = False
    pending_jobs = False
    loaded = True
    ready = True

    def drained(self):
        if self.busy or self.pending_jobs:
            raise tx.Hold('Both managers must drain')

    def activation(self, direction):
        if not self.loaded:
            raise tx.Hold('Effective units differ')
        return {'direction': direction, 'backend': 'isolated-manager-model'}

    def clock_ready(self):
        return self.ready

def prepared(tmp_path):
    plan = copy.deepcopy(PLAN)
    # Replace only secret-bearing/read-only external pins with synthetic bytes.
    for path, node in list(plan['protected'].items()):
        if node['kind'] == 'digest':
            raw = ('fixture:' + path).encode()
            plan['protected'][path] = {'kind': 'file', 'mode': 0o644,
                                      'text': raw.decode(), 'sha256': tx.sha(raw)}
    approval = signed(plan)
    root = tmp_path / 'isolated'
    root.mkdir()
    (root / '.migration-fixture').touch()
    fs = tx.Files(root)
    for path, node in plan['protected'].items():
        fs.write(path, node)
    for path, operation in plan['operations'].items():
        fs.write(path, operation['before'])
    for key, node in plan['retirement_masks'].items():
        manager, name = key.split(':', 1)
        base = '/etc/systemd/system/' if manager == 'system' else '/home/yoonhc/.config/systemd/user/'
        fs.write(base + name, node)
    fs.save(tx.LEDGER, plan['ledger_before'])
    fs.path('/var/lib/marketdata-clock/coordinator.lock').touch()
    host = Host()
    transition = tx.Transaction(plan, approval, fs, host, boot=lambda: plan['boot_id'])
    return transition

def unchanged_clock(before, after):
    allowed = {'profile_sha256', 'configuration_history'}
    assert {k: v for k, v in before.items() if k not in allowed} == {
        k: v for k, v in after.items() if k not in allowed}

def old_gate(transition, *, ready=True):
    gate = pinned.load_gate()
    profile = json.loads(transition.fs.path(tx.PROFILE).read_text())
    return gate.check_gate(profile, {}, ledger=gate.Ledger(transition.fs.path(tx.LEDGER).parent),
        checker=lambda _: {'ready': ready, 'reasons': [] if ready else ['reference_age']},
        current_boot=transition.plan['boot_id'])

def crash(point):
    def fault(where):
        if where == point:
            raise InterruptedError(where)
    return fault

@pytest.mark.parametrize('point', POINTS)
def test_every_apply_boundary_resumes_and_preserves_boot(tmp_path, point):
    t = prepared(tmp_path)
    t.fault = crash(point)
    with pytest.raises(InterruptedError):
        t.stage()
        t.commit()
    state = t.fs.json(tx.LEDGER)
    if state.get('schema') == 2:
        with pytest.raises(Exception, match='ledger|state|profile'):
            old_gate(t)
    t.fault = lambda _: None
    t.stage()
    result = t.commit()
    assert result['configuration'] == 'apply_committed'
    unchanged_clock(t.plan['ledger_before'], t.fs.json(tx.LEDGER))
    assert old_gate(t)['ready']
    assert t.commit()['idempotent']

@pytest.mark.parametrize('point', POINTS)
def test_every_apply_boundary_can_rollback_without_data_rewind(tmp_path, point):
    t = prepared(tmp_path)
    t.fault = crash(point)
    with pytest.raises(InterruptedError):
        t.stage()
        t.commit()
    # New content/ledger appears after the migration began and must survive rollback.
    data = t.fs.path('/home/yoonhc/projects/algoTrade/historical_data/data/new-artifact')
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_bytes(b'new application evidence')
    dbpath = data.parent / 'catalog.sqlite'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE daily_tasks (id TEXT PRIMARY KEY, state TEXT)')
        db.execute("INSERT INTO daily_tasks VALUES ('new-id', 'complete')")
    t.fault = lambda _: None
    t.stage('rollback')
    t.commit()
    assert all(t.fs.node(p) == op['before'] for p, op in t.plan['operations'].items())
    unchanged_clock(t.plan['ledger_before'], t.fs.json(tx.LEDGER))
    assert data.read_bytes() == b'new application evidence'
    with sqlite3.connect(dbpath) as db:
        assert db.execute('SELECT * FROM daily_tasks').fetchall() == [('new-id', 'complete')]
    assert old_gate(t)['ready']

@pytest.mark.parametrize('point', ['rollback_intent', 'pending', 'file:0', 'file:22', 'file:45', 'staged', 'receipt', 'committed'])
def test_rollback_itself_recovers_from_interruption(tmp_path, point):
    t = prepared(tmp_path)
    t.stage()
    t.commit()
    t.fault = crash(point)
    with pytest.raises(InterruptedError):
        t.stage('rollback')
        t.commit()
    t.fault = lambda _: None
    t.stage('rollback')
    t.commit()
    assert t.fs.json(tx.LEDGER)['configuration_history'][-2]['direction'] == 'apply'
    assert t.fs.json(tx.LEDGER)['configuration_history'][-1]['direction'] == 'rollback'
    assert old_gate(t)['ready']

@pytest.mark.parametrize('case', ['busy', 'pending_jobs', 'new_boot', 'clock_config', 'retirement_mask', 'ledger', 'approval'])
def test_preflight_holds_do_not_change_configuration(tmp_path, case):
    t = prepared(tmp_path)
    original = t.fs.json(tx.LEDGER)
    if case in ('busy', 'pending_jobs'):
        setattr(t.host, case, True)
    elif case == 'new_boot':
        t.boot = lambda: 'another-boot'
    elif case == 'clock_config':
        t.fs.path('/etc/chrony/marketdata.conf').write_text('changed')
    elif case == 'retirement_mask':
        t.fs.path('/etc/systemd/system/marketdata-checkpoint-retirement.timer').unlink()
    elif case == 'ledger':
        t.fs.save(tx.LEDGER, {**original, 'step_attempts': 1})
        original = t.fs.json(tx.LEDGER)
    elif case == 'approval':
        bad = {**t.approval, 'enabled': False}
        with pytest.raises(tx.Hold):
            tx.Transaction(t.plan, bad, t.fs, t.host, boot=t.boot)
        return
    with pytest.raises(tx.Hold):
        t.stage()
    assert t.fs.json(tx.LEDGER) == original

def test_activation_mismatch_holds_and_readiness_is_separate(tmp_path):
    t = prepared(tmp_path)
    t.stage()
    t.host.loaded = False
    with pytest.raises(tx.Hold):
        t.commit()
    assert t.fs.json(tx.LEDGER)['schema'] == 2
    t.host.loaded, t.host.ready = True, False
    assert t.commit()['current_clock_ready'] is False
    with pytest.raises(Exception, match='reference_age'):
        old_gate(t, ready=False)
    assert old_gate(t, ready=True)['ready']

def test_real_repeated_command_properties_and_scalar_rejection():
    value = reader.parse('ExecStartPre={ argv[]=first ; }\nExecStartPre={ argv[]=second ; }\nAfter=a b\n')
    assert reader.commands(value['ExecStartPre']) == ['first', 'second']
    with pytest.raises(ValueError, match='scalar'):
        reader.parse('User=yoonhc\nUser=root\n')

def effective(spec, *, active='inactive'):
    values = {'LoadState': 'loaded', 'NeedDaemonReload': 'no', 'DropInPaths': '',
              'FragmentPath': spec['fragment'], 'ActiveState': active, 'MainPID': '0', 'ControlPID': '0'}
    for key in ('User', 'WorkingDirectory', 'Unit'):
        if key in spec:
            values[key] = spec[key]
    for key in ('After', 'Requires'):
        values[key] = ' '.join(reversed(spec.get(key, [])))
    for key in reader.COMMANDS:
        values[key] = '\n'.join('{ path=/binary ; argv[]=' + cmd + ' ; }' for cmd in spec.get(key, []))
    return values

@pytest.mark.parametrize('name', list(PLAN['topology']['units']))
def test_effective_unit_contracts_accept_only_exact_commands(name):
    spec = PLAN['topology']['units'][name]
    props = effective(spec)
    reader.verify(props, spec, quiescent=spec['kind'] == 'work')
    props['ExecStartPost'] = '{ argv[]=/bad/helper ; }'
    with pytest.raises(ValueError, match='command'):
        reader.verify(props, spec)

@pytest.mark.parametrize('mutation', ['running', 'pending', 'unexpected_unit', 'command', 'root_user', 't05_running', 'none'])
def test_bootstrap_system_inventory_and_waiter_are_distinct(tmp_path, monkeypatch, mutation):
    gate = pinned.load_gate()
    topology = copy.deepcopy(PLAN['topology'])
    path = tmp_path / 'topology.json'
    path.write_bytes(tx.encode(topology))
    profile = json.loads(PLAN['operations'][tx.PROFILE]['after']['text'])
    profile['guard_files'][str(path)] = tx.sha(path.read_bytes())
    # Parent audit remains the tested original. This test isolates the added
    # system inventory; we also assert the parent audit was called.
    calls = []
    monkeypatch.setattr(gate, 'trusted', lambda *a, **k: None)
    monkeypatch.setattr(gate.Host, 'audit_quiescent', lambda self: calls.append('parent'))
    work = 'marketdata-daily.service'
    def runner(argv):
        if 'list-unit-files' in argv:
            names = list(topology['system_inventory'])
            if mutation == 'unexpected_unit':
                names.append('marketdata-rogue.service')
            return '\n'.join(x + ' disabled' for x in names)
        if 'list-units' in argv:
            return 'marketdata-checkpoint-retirement-preview.service loaded inactive dead test'
        name = argv[argv.index('show') + 1]
        if 'retirement-preview' in name:
            return 'ActiveState=' + ('active' if mutation == 't05_running' else 'inactive') + '\nMainPID=0\nControlPID=0'
        spec = topology['units'][name]
        props = effective(spec, active='activating' if spec['kind'] == 'waiter' else 'inactive')
        if name == work:
            if mutation == 'running':
                props.update(ActiveState='active', MainPID='123')
            elif mutation == 'pending':
                props.update(ActiveState='activating', ControlPID='123')
            elif mutation == 'command':
                props['ExecStartPre'] = ''
            elif mutation == 'root_user':
                props['User'] = 'root'
        return '\n'.join(k + '=' + v for k, v in props.items())
    host = bootstrap_system.host_class(gate, topology_path=str(path))(profile, {}, runner=runner)
    if mutation == 'none':
        host.audit_quiescent()
    else:
        with pytest.raises((ValueError, gate.Hold)):
            host.audit_quiescent()
    assert calls == ['parent']

def test_system_archive_context_retains_other_fields_and_explicit_manager():
    calls = []
    def original(*args, **kwargs):
        calls.append(kwargs)
        return {'scheduled': True, 'manager': kwargs['manager'], 'prior': {'immutable': True}}
    module = SimpleNamespace(service_context=original, __file__='/fixed/parent/restic_schedule.py')
    assert run_system_archive.bind(module) is original
    output = module.service_context()
    assert calls == [{'manager': 'system'}]
    assert output['prior'] == {'immutable': True}
    assert output['manager_adapter']['sha256']
    with pytest.raises(RuntimeError):
        module.service_context(manager='user')

def test_t05_binding_matches_new_profile_without_registration(tmp_path):
    t = prepared(tmp_path)
    t.stage()
    t.commit()
    binding = t.fs.json('/etc/marketdata/checkpoint-clock-retirement/clock-binding.json')
    assert binding['bootstrap_profile_sha256'] == tx.sha(t.fs.path(tx.PROFILE).read_bytes())
    assert all('registry' not in path and 'registration' not in path for path in t.plan['operations'])
    assert all('/data/' not in path and '/mnt/backup/' not in path for path in t.plan['operations'])

def test_live_root_and_symlink_escape_are_refused(tmp_path):
    with pytest.raises(tx.Hold):
        tx.Files('/')
    t = prepared(tmp_path)
    link = t.fs.root / 'escape'
    link.symlink_to('/etc')
    with pytest.raises(tx.Hold):
        t.fs.path('/escape/passwd')

@pytest.mark.parametrize('mutation', ['missing_mask', 'lost_guard', 'new_guard', 'binding', 'policy'])
def test_exact_approval_cannot_remove_existing_safety_contract(mutation):
    plan = copy.deepcopy(PLAN)
    profile_node = plan['operations'][tx.PROFILE]['after']
    profile = json.loads(profile_node['text'])
    if mutation == 'missing_mask':
        del plan['operations']['/home/yoonhc/.config/systemd/user/marketdata-daily.service']
    elif mutation == 'lost_guard':
        del profile['guard_files']['/etc/systemd/user/marketdata-daily.service.d/90-marketdata-clock.conf']
    elif mutation == 'new_guard':
        del profile['guard_files']['/etc/systemd/system/marketdata-daily.service']
    elif mutation == 'binding':
        node = plan['operations']['/etc/marketdata/checkpoint-clock-retirement/clock-binding.json']['after']
        node['text'] = '{}'
        node['sha256'] = tx.sha(b'{}')
    else:
        profile['policy']['unreviewed'] = True
    profile_node['text'] = tx.encode(profile).decode()
    profile_node['sha256'] = tx.sha(profile_node['text'].encode())
    with pytest.raises(tx.Hold):
        tx.validate(plan, signed(plan))

def test_actual_source_and_environment_are_fixed():
    manifest = json.loads((HERE / 'manifest.json').read_text())
    candidate = HERE / 'candidate'
    for name, digest in manifest['workspace_identical_sha256'].items():
        assert tx.sha((candidate / name).read_bytes()) == digest
    import marketdata
    assert '.venv/lib/' in marketdata.__file__
    for name, digest in manifest['workspace_identical_sha256'].items():
        marker = 'packages/marketdata/src/'
        if name.startswith(marker):
            installed = Path(marketdata.__file__).parent.parent / name.removeprefix(marker)
            assert tx.sha(installed.read_bytes()) == digest
