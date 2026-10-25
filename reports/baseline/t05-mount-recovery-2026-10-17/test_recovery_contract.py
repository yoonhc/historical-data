"""No operating writes: exact frozen contract + real atomic files in tmp_path."""
import copy
import importlib.util
import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path('/home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-mount-recovery-2026-10-17')


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


m = module('mount_recovery_tested', HERE / 'repair_and_resume.py')
h = module('scope_atomic_tested', HERE.parent / 't05-disabled-scope-2026-10-17/refresh_scope.py')
app = module('resume_tested', HERE.parent / 't05-first-manual-resume-2026-10-17/manual_resume.py')


@pytest.fixture
def contract():
    p = json.loads((HERE / 'plan.json').read_bytes())
    r = json.loads(Path(p['files']['retirement_plan']['path']).read_bytes())
    j = json.loads(Path(p['files']['journal_copy']['path']).read_bytes())
    return p, r, j


def test_exact_bridge_preserves_original_contract_and_failure(contract):
    p, r, j = contract
    before = copy.deepcopy((p, r, j))
    m.validate_transition(p)
    m.reviewed_journal(j, r)
    recurring = json.loads(p['changes'][-1]['after']['text'])
    bridge = m.scope_transition_proposal(p, r, recurring)
    assert (p, r, j) == before
    assert bridge['plan_id'] == r['plan_id'] and bridge['resume_interrupted']
    assert not bridge['enabled'] and bridge['approved_at'] is None
    assert bridge['scope_id'] == p['after_scope_id'] != r['automation']['scope_id']
    assert bridge['scope']['code'] == r['code']
    enabled = app.enabled_grant(bridge, '2026-10-17T08:00:00Z', '/isolated/approval.md')
    assert enabled['enabled'] and enabled['scope_transition'] == bridge['scope_transition']


@pytest.mark.parametrize('mutation', ['extra_file', 'extra_unit_option', 'recurring_enable', 'binding', 'ownership', 'scope', 'plan', 'run'])
def test_unreviewed_change_is_rejected(contract, mutation):
    p, _, _ = contract
    if mutation == 'extra_file':
        p['changes'].append(copy.deepcopy(p['changes'][0]))
    elif mutation == 'extra_unit_option':
        row = p['changes'][0]['after']
        row['text'] += 'User=root\n'
        row['fingerprint']['sha256'] = m.sha(row['text'].encode())
    elif mutation == 'recurring_enable':
        row = p['changes'][-1]['after']
        value = json.loads(row['text']); value['enabled'] = True
        row['text'] = json.dumps(value)
    elif mutation == 'binding':
        row = p['changes'][-1]['after']
        value = json.loads(row['text']); value['scope']['clock_binding']['bootstrap_profile_sha256'] = '0' * 64
        row['text'] = json.dumps(value)
    elif mutation == 'ownership':
        p['changes'][0]['after']['fingerprint']['uid'] = 1000
    elif mutation == 'scope':
        p['after_scope_id'] = '0' * 64
    elif mutation == 'plan':
        p['retirement_plan_id'] = '0' * 64
    else:
        p['reviewed_run_id'] = '0' * 32
    with pytest.raises(RuntimeError):
        m.validate_transition(p)


@pytest.mark.parametrize('mutation', ['unlink', 'moved', 'target', 'another_attempt'])
def test_only_exact_prepared_journal_is_accepted(contract, mutation):
    _, r, j = contract
    item = next(iter(j['items'].values()))
    if mutation == 'unlink':
        item['unlinked_files'] = 1
    elif mutation == 'moved':
        item['phase'] = 'moved'
    elif mutation == 'target':
        item['staged'] += '-other'
    else:
        j['attempts'].append(copy.deepcopy(j['attempts'][0]))
    with pytest.raises(RuntimeError):
        m.reviewed_journal(j, r)


def test_three_explicit_approvals_required(contract):
    p, _, _ = contract
    m.require_approval('check', p, None, None, None)
    good = [p['operation_plan_id'], m.PLAN_ID, m.RUN_ID]
    for i in range(3):
        args = list(good); args[i] = None
        with pytest.raises(RuntimeError):
            m.require_approval('apply', p, *args)
    m.require_approval('apply', p, *good)


def tmp_transition(tmp_path):
    rows = []
    for i in range(4):
        path = tmp_path / str(i)
        path.write_text('before-' + str(i))
        before = {'text': path.read_text(), 'fingerprint': h.fingerprint(path)}
        after = {'text': 'after-' + str(i), 'fingerprint': {**before['fingerprint'], 'sha256': m.sha(('after-' + str(i)).encode())}}
        rows.append({'path': str(path), 'before': before, 'after': after})
    return {'changes': rows}


@pytest.mark.parametrize('boundary', [1, 2, 3, 4])
def test_each_atomic_commit_can_resume_and_roll_back_without_touching_history(tmp_path, boundary):
    plan = tmp_transition(tmp_path)
    history = tmp_path / 'journal.json'; history.write_bytes(b'original failure and new work')
    count = 0
    def interrupted(*args):
        nonlocal count
        h.replace_checked(*args)
        count += 1
        if count == boundary:
            raise KeyboardInterrupt('after durable file replacement')
    interrupted_helper = SimpleNamespace(fingerprint=h.fingerprint, replace_checked=interrupted)
    with pytest.raises(KeyboardInterrupt):
        m.transition_files(plan, interrupted_helper, 'after', lambda: None)
    assert list(m.configuration_positions(plan, h).values()).count('after') == boundary
    m.transition_files(plan, h, 'after', lambda: None)
    assert set(m.configuration_positions(plan, h).values()) == {'after'}
    m.transition_files(plan, h, 'before', lambda: None)
    assert set(m.configuration_positions(plan, h).values()) == {'before'}
    assert history.read_bytes() == b'original failure and new work'


def test_unknown_configuration_cannot_be_overwritten(tmp_path):
    plan = tmp_transition(tmp_path)
    path = Path(plan['changes'][1]['path']); path.write_text('external modification')
    with pytest.raises(RuntimeError, match='Unknown configuration'):
        m.transition_files(plan, h, 'after', lambda: None)
    assert path.read_text() == 'external modification'
    assert Path(plan['changes'][0]['path']).read_text() == plan['changes'][0]['before']['text']


@pytest.fixture
def finalization(tmp_path, monkeypatch):
    manual = tmp_path / 'manual.json'; manual.write_text('{"enabled":false}')
    before = {'text': manual.read_text(), 'fingerprint': h.fingerprint(manual)}
    enabled_raw = b'{"enabled":true,"plan_id":"explicit"}'
    manual.write_bytes(enabled_raw)
    (tmp_path / 'manual-enabled.json').write_bytes(enabled_raw)
    helper = SimpleNamespace(MANUAL=manual, fingerprint=h.fingerprint, replace_checked=h.replace_checked,
                             blob=h.blob, write_new=h.write_new)
    idle = {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0', 'Job': ''}
    unit = [idle]
    waits = []
    def wait(before_runs, record):
        waits.append(True)
        return {'ok': False, 'run_id': 'new', 'state': 'failed', 'exit_code': 1, 'deleted_this_run': 0}
    fake = SimpleNamespace(unit_state=lambda: unit[0], idle=app.idle, wait_result=wait,
        restore_when_idle=lambda helper, before, enabled: app.restore_when_idle(helper, before, enabled, lambda: unit[0]))
    monkeypatch.setattr(m, 'assert_original_work', lambda *a: None)
    monkeypatch.setattr(m, 'verify_operating', lambda *a, **k: {'positions': {'only': 'after'}})
    state = tmp_path / 'state'; (state / 'retention/plans').mkdir(parents=True)
    (state / 'retention/plans' / (m.PLAN_ID + '.json')).write_text('{}')
    monkeypatch.setattr(m, 'STATE', state)
    plan = {'manual_before': before, 'retirement_plan_sha256': m.sha(b'{}')}
    return plan, helper, fake, tmp_path, unit, waits


def test_crash_after_grant_before_dispatch_finalizes_without_start(finalization):
    plan, helper, fake, out, unit, waits = finalization
    record = {'dispatch_intent': False}
    assert m.finalize(plan, helper, fake, out, record) == 1
    assert helper.MANUAL.read_text() == plan['manual_before']['text']
    assert not waits and record['cancelled_before_dispatch'] and record['recovery_review_required']


def test_failed_worker_restores_only_owned_grant_and_preserves_failure(finalization):
    plan, helper, fake, out, unit, waits = finalization
    record = {'dispatch_intent': True, 'runs_before_dispatch': ['old']}
    assert m.finalize(plan, helper, fake, out, record) == 1
    assert helper.MANUAL.read_text() == plan['manual_before']['text']
    assert record['run_id'] == 'new' and record['run_state'] == 'failed'
    assert len(waits) == 1 and not record['passed']


@pytest.mark.parametrize('case', ['worker_active', 'foreign_grant'])
def test_finalizer_never_revokes_active_or_foreign_grant(finalization, case):
    plan, helper, fake, out, unit, waits = finalization
    if case == 'worker_active':
        unit[0] = {'ActiveState': 'activating', 'MainPID': '123', 'ControlPID': '0', 'Job': '1'}
        def timeout(*args):
            raise RuntimeError('Wait limit reached; service not stopped or retried')
        fake.wait_result = timeout
    else:
        helper.MANUAL.write_text('foreign approval')
    before = helper.MANUAL.read_bytes()
    record = {'dispatch_intent': True, 'runs_before_dispatch': ['old']}
    assert m.finalize(plan, helper, fake, out, record) == 1
    assert helper.MANUAL.read_bytes() == before and 'finalization_error' in record


def test_corrupt_original_plan_does_not_receive_scope_bridge(contract):
    p, r, _ = contract
    r['candidates'].pop()
    with pytest.raises(RuntimeError, match='digest'):
        m.scope_transition_proposal(p, r, json.loads(p['changes'][-1]['after']['text']))


@pytest.mark.parametrize('start_error', [False, True])
def test_full_dispatch_path_releases_runner_and_never_reissues_intent(tmp_path, monkeypatch, start_error):
    plan = tmp_transition(tmp_path)
    plan.update(operation_plan_id='isolated-operation', old_manual_proposal={'old': True})
    manual = tmp_path / 'manual.json'; manual.write_text('{"enabled":false}')
    plan['manual_before'] = {'text': manual.read_text(), 'fingerprint': h.fingerprint(manual)}
    out = tmp_path / 'root-audit'; out.mkdir()
    helper = SimpleNamespace(MANUAL=manual, fingerprint=h.fingerprint, replace_checked=h.replace_checked,
                             blob=h.blob, write_new=h.write_new, locks=nullcontext, proposal=lambda: {})
    held = [False]
    commands = []
    @contextmanager
    def runner():
        assert not held[0]
        held[0] = True
        try:
            yield
        finally:
            held[0] = False
    def command(argv):
        commands.append(argv)
        if 'start' in argv:
            assert not held[0]
            record = json.loads((out / 'state.json').read_bytes())
            assert record['dispatch_intent'] and not record['service_start_requested']
            if start_error:
                raise RuntimeError('ambiguous start-command transport error')
    fake = SimpleNamespace(runner_idle=runner, idle=lambda row: True,
        unit_state=lambda: {'NeedDaemonReload': 'no'}, manual_proposal=lambda helper: plan['old_manual_proposal'],
        check_preservation_idle=lambda: {}, check_preview_anchor=lambda *args: {'ready': True},
        command=command, installed_unit_check=lambda helper: None, enabled_grant=lambda *args: {'enabled': True},
        runs=lambda: {'old': '/unused'}, UNIT='isolated.service')
    plan['files'] = {'retirement_plan': {'path': '/unused'}}
    monkeypatch.setattr(m, 'seal_operation', lambda *args: out)
    monkeypatch.setattr(m, 'assert_original_work', lambda *args: {})
    monkeypatch.setattr(m, 'verify_operating', lambda *args, **kwargs: {'positions': m.configuration_positions(plan, helper)})
    monkeypatch.setattr(m, 'scope_transition_proposal', lambda *args: {'scope_id': 'explicit-new-scope'})
    def final(plan, helper, app, out, record):
        assert not held[0]
        assert record['dispatch_intent']
        if start_error:
            assert 'start_command_error' in record
        manual.write_text(plan['manual_before']['text'])
        return 1 if start_error else 0
    monkeypatch.setattr(m, 'finalize', final)
    assert m.operate(plan, helper, fake, 'apply') == (1 if start_error else 0)
    assert len([c for c in commands if 'start' in c]) == 1
    with pytest.raises(RuntimeError, match='Operation consumed'):
        m.operate(plan, helper, fake, 'apply')
    assert len([c for c in commands if 'start' in c]) == 1
