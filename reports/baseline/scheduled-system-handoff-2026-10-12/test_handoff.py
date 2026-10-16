"""Real filesystem transaction + deterministic two-manager command boundary.

Exceptions model interruption at durable boundaries, not power-loss testing.
Real PID/job/bus/mask behavior belongs to the temporary-unit root preflight.
"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import handoff as h
import handoff_common as c
sys.path.insert(0, str(c.BASE))
import test_migration as fixture
fixture.PLAN = json.loads((HERE / 'configuration-plan.json').read_bytes())

class Managers:
    def __init__(self, fs):
        self.fs, self.trace, self.ticks = fs, [], 0
        self.units = {}
        for manager in ('user', 'system'):
            for name in (*c.SERVICES, *c.TIMERS, c.WAITER):
                self.units[manager, name] = {'Id': name, 'ActiveState': 'inactive', 'SubState': 'dead',
                    'MainPID': '0', 'ControlPID': '0', 'InvocationID': '', 'queued': False,
                    'ActiveEnterTimestampMonotonic': '0'}
        for name in c.TIMERS:
            self.units['user', name].update(ActiveState='active', SubState='waiting')
        self.units['user', c.SERVICES[0]].update(ActiveState='activating', SubState='start', MainPID='77')
        self.units['user', c.SERVICES[1]]['queued'] = True
        self.units['user', c.SERVICES[2]].update(ActiveState='activating', SubState='auto-restart')
        self.units['user', c.WAITER].update(ActiveState='activating', SubState='start', MainPID='78')

    def show(self, manager, name):
        value = dict(self.units[manager, name])
        root = c.USER_ROOT if manager == 'user' else c.SYSTEM_ROOT
        node = self.fs.node(root + name)
        value['LoadState'] = 'masked' if node == h.MASK else 'not-found' if node == h.ABSENT else 'loaded'
        return value

    def jobs(self, manager):
        return [{'unit': n, 'job': i + 1, 'state': 'waiting'} for i, ((m, n), p) in enumerate(self.units.items()) if m == manager and p['queued']]

    def states(self, manager, names):
        jobs = self.jobs(manager)
        return {n: {'properties': (p := self.show(manager, n)), 'classification': c.classify(p, jobs),
                    'jobs': [j for j in jobs if j['unit'] == n]} for n in names}

    def command(self, manager, action, *args, **kwargs):
        names = [a for a in args if not a.startswith('--')]
        self.trace.append((manager, action, names))
        assert all(n in (*c.SERVICES, *c.TIMERS, c.WAITER) for n in names)
        assert action in ('stop', 'start', 'daemon-reload')
        for name in names:
            p = self.units[manager, name]
            if action == 'stop':
                if name in c.SERVICES:
                    assert p['MainPID'] == p['ControlPID'] == '0', 'business process was stopped'
                if name == c.WAITER:
                    assert all(self.units[manager, n]['MainPID'] == '0' for n in c.SERVICES), 'waiter stopped before work drained'
                p.update(ActiveState='inactive', SubState='dead', MainPID='0', ControlPID='0', queued=False)
            elif action == 'start':
                self.ticks += 1
                assert self.show(manager, name)['LoadState'] == 'loaded'
                p.update(ActiveState='active' if name.endswith('.timer') else 'inactive',
                         SubState='waiting' if name.endswith('.timer') else 'dead',
                         InvocationID='inv-' + str(self.ticks), ActiveEnterTimestampMonotonic=str(self.ticks))
        return subprocess.CompletedProcess([], 0, '', '')

def setup(tmp_path):
    original = fixture.prepared(tmp_path)
    plan = json.loads((HERE / 'host-plan.json').read_bytes())
    plan['inner'] = original.plan
    # Actual secret pins are replaced only by fixture.prepared, as before.
    plan['plan_id'] = c.sha(c.encode({k: v for k, v in plan.items() if k != 'plan_id'}))
    approval = {'purpose': h.PURPOSE, 'plan_id': plan['plan_id'], 'enabled': True,
                'approval_reference': 'isolated-only', 'directions': ['apply', 'rollback']}
    # Local contract fixture updates allowed fixed plan hash, not live code.
    original_id = original.plan['plan_id']
    real_validate = h.validate_outer
    def validate(p, a):
        # Same checks except fixture secret substitution changes the inner ID.
        v = copy.deepcopy(p)
        v['inner']['plan_id'] = json.loads((HERE / 'configuration-plan.json').read_bytes())['plan_id']
        v['plan_id'] = c.sha(c.encode({k: val for k, val in v.items() if k != 'plan_id'}))
        real_validate(v, {**a, 'plan_id': v['plan_id']})
        assert p['inner']['plan_id'] == original_id
    manager = Managers(original.fs)
    host = original.host
    host.immutable = lambda: None
    host.baseline = lambda: None
    host.drained = lambda: c.require(all(v['classification'] == 'idle' for m in ('user', 'system')
                        for v in manager.states(m, (*c.SERVICES, *c.TIMERS)).values()), 'not drained')
    moment = [0]
    def pause(n):
        moment[0] += n
        if moment[0] >= 3:
            manager.units['user', c.SERVICES[0]].update(MainPID='0', ActiveState='inactive', SubState='dead')
    # Validate uses a monkeypatch context in the caller; the fixture keeps exact
    # plan+approval persistence checks in Transaction and Handoff intact.
    return plan, approval, original.fs, manager, host, moment, pause, validate

def make(tmp_path, monkeypatch):
    p, a, fs, m, host, tick, pause, validate = setup(tmp_path)
    monkeypatch.setattr(h, 'validate_outer', validate)
    executor = h.Handoff(p, a, fs, m, host, boot=lambda: p['boot_id'], pause=pause, monotonic=lambda: tick[0])
    return executor

PHASE_FAULTS = ['phase:' + x for x in h.PHASES[1:]]
BARRIER_FAULTS = ['barrier:user:' + n for n in (*c.SERVICES, *c.TIMERS)]
START_FAULTS = ['start-return:system:' + n for n in c.TIMERS]

@pytest.mark.parametrize('point', PHASE_FAULTS + BARRIER_FAULTS + START_FAULTS)
def test_resume_durable_handoff_boundaries(tmp_path, monkeypatch, point):
    e = make(tmp_path, monkeypatch)
    def fail(where):
        if where == point:
            raise InterruptedError(where)
    e.fault = fail
    with pytest.raises(InterruptedError):
        e.run()
    e.fault = lambda _: None
    e.run()
    assert e.state['phase'] == 'complete'
    fixture.unchanged_clock(e.plan['inner']['ledger_before'], e.fs.json(h.tx.LEDGER))
    assert all(e.fs.node(c.USER_ROOT + n) == h.MASK for n in (*c.SERVICES, *c.TIMERS))
    assert len(e.state['cancelled']) == 2
    assert all(e.fs.node(p) == op['after'] for p, op in e.plan['enable_links'].items())
    starts = [n for manager, action, names in e.m.trace if manager == 'system' and action == 'start' for n in names]
    assert len(starts) == len(set(starts)) == 10  # 8 timers + 2 recorded waiting requests

@pytest.mark.parametrize('point', ['phase:intent', 'barrier:user:' + c.SERVICES[0], 'phase:held', 'phase:drained', 'phase:staged', 'phase:committed', 'phase:activated', 'phase:complete'])
def test_return_to_user_after_partial_or_complete_handoff(tmp_path, monkeypatch, point):
    e = make(tmp_path, monkeypatch)
    e.fault = lambda where: (_ for _ in ()).throw(InterruptedError()) if where == point else None
    with pytest.raises(InterruptedError):
        e.run()
    evidence = e.fs.path('/home/yoonhc/new-manager-record.json')
    evidence.write_text('preserve mixed-manager receipt and application state')
    e.fault = lambda _: None
    e.run('rollback')
    assert all(e.fs.node(p) == op['before'] for p, op in e.plan['inner']['operations'].items())
    fixture.unchanged_clock(e.plan['inner']['ledger_before'], e.fs.json(h.tx.LEDGER))
    assert evidence.read_text() == 'preserve mixed-manager receipt and application state'
    assert all(e.fs.node(p) == op['before'] for p, op in e.plan['enable_links'].items())
    with pytest.raises(c.Hold, match='cannot be reapplied'):
        e.run()

def test_timeout_preserves_closed_ledger_and_does_not_kill_work(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch)
    with pytest.raises(c.Hold, match='Normal drain pending'):
        e.run(drain_seconds=1)
    assert e.fs.json(h.tx.LEDGER) == e.plan['inner']['ledger_before']
    assert e.state['phase'] == 'held'
    assert e.m.units['user', c.SERVICES[0]]['MainPID'] == '77'
    e.run()
    assert e.state['phase'] == 'complete'

def test_clock_not_ready_changes_no_service_or_ledger(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch)
    e.host.ready = False
    with pytest.raises(c.Hold, match='Strict readiness'):
        e.run()
    assert e.m.trace == []
    assert e.fs.json(h.tx.LEDGER) == e.plan['inner']['ledger_before']

def test_uncertain_start_is_not_blindly_replayed(tmp_path, monkeypatch):
    e = make(tmp_path, monkeypatch)
    point = 'start-intent:system:' + c.TIMERS[0]
    e.fault = lambda where: (_ for _ in ()).throw(InterruptedError()) if where == point else None
    with pytest.raises(InterruptedError):
        e.run()
    e.fault = lambda _: None
    with pytest.raises(c.Hold, match='Indeterminate start'):
        e.run()
    # Explicit reviewed operator resolution (fixture only) proves actual start.
    e.m.command('system', 'start', '--no-block', c.TIMERS[0])
    e.run()
    assert e.state['start_requests']['system:' + c.TIMERS[0]]['reconciled']

@pytest.mark.parametrize('state', [
    {'ActiveState': 'inactive', 'SubState': 'dead', 'MainPID': '123'},
    {'ActiveState': 'activating', 'SubState': 'start', 'MainPID': '123'},
    {'ActiveState': 'activating', 'SubState': 'start-pre', 'ControlPID': '123'},
])
def test_pid_always_means_work_in_flight(state):
    assert c.classify(state, []) == 'running'

@pytest.mark.parametrize('substate', ['start-pre', 'start', 'start-post'])
def test_pid_zero_gap_in_running_job_is_never_cancellable(substate):
    p = {'Id': 'a.service', 'MainPID': '0', 'ControlPID': '0', 'SubState': substate, 'ActiveState': 'activating'}
    assert c.classify(p, [{'unit': 'a.service', 'job': 1, 'state': 'running'}]) == 'unknown'

def test_safe_writer_replaces_leaf_without_following_and_refuses_parent_link(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    target = tmp_path / 'protected'
    target.write_bytes(b'preserve')
    (root / 'leaf').symlink_to(target)
    fs = c.SafeFiles(root, ['/leaf', '/parent/leaf'], {})
    fs.write('/leaf', {'kind': 'file', 'mode': 0o644, 'text': 'new', 'sha256': c.sha(b'new')})
    assert target.read_bytes() == b'preserve' and (root / 'leaf').read_bytes() == b'new'
    (root / 'parent').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        fs.write('/parent/leaf', h.MASK)
    with pytest.raises(c.Hold, match='Unapproved write'):
        fs.write('/not-approved', h.ABSENT)

def test_repeated_command_and_scalar_parsing():
    assert c.parse('ExecStartPre=a\nExecStartPre=b')['ExecStartPre'] == 'a\nb'
    with pytest.raises(c.Hold):
        c.parse('User=a\nUser=b')

@pytest.mark.parametrize('raw,count', [('', 0), (
    '533794 marketdata-daily.service          start running\n'
    '533922 marketdata-pack.service           start running\n'
    '533890 marketdata-restic-archive.service start running\n', 3)])
def test_real_host_plain_job_output(raw, count):
    m = c.Managers(runner=lambda *a, **k: subprocess.CompletedProcess([], 0, raw, ''))
    assert len(m.jobs('system')) == count

@pytest.mark.parametrize('raw', ['JOB UNIT TYPE STATE\n', '42 a.service start waiting\n42 b.service start waiting\n', '42 a.service nonsense\n'])
def test_job_parser_rejects_ambiguous_output(raw):
    m = c.Managers(runner=lambda *a, **k: subprocess.CompletedProcess([], 0, raw, ''))
    with pytest.raises(c.Hold):
        m.jobs('system')

@pytest.mark.parametrize('job', ['oi', 'oi-probe', 'oi-quality', 'pack'])
def test_corrected_real_entrypoints_start_without_store(tmp_path, job):
    plan = json.loads((HERE / 'configuration-plan.json').read_bytes())
    command = plan['topology']['units']['marketdata-' + job + '.service']['ExecStart'][0]
    import shlex
    args = shlex.split(command.replace('/opt/marketdata/releases/' + plan['release_id'], str(c.BASE / 'candidate')))
    # argparse help exits before opening even a nonexistent isolated Store.
    index = args.index('--root')
    args[index + 1] = str(tmp_path / 'must-not-be-created')
    result = subprocess.run(args + ['--help'], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    assert not Path(args[index + 1]).exists()

def test_live_manager_never_accepts_collector_or_clock_control():
    m = c.Managers(allowed=[('system', c.SERVICES[0])])
    for action, unit in [('stop', c.IDENTITIES[0]), ('start', c.IDENTITIES[1]), ('restart', c.SERVICES[0])]:
        with pytest.raises(c.Hold):
            m.command('system', action, unit)
