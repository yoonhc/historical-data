"""Regression for real systemctl multi-valued properties and pending recovery."""

import importlib.util
import json
from pathlib import Path

import pytest


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


HERE = Path(__file__).resolve().parent
repair = load('reader_repair', HERE / 'continue_runtime.py')
fixtures = load('old_transition_fixtures', HERE.parent / 'clock-runtime-transition-2026-10-09/release-final/test_transition.py')
m = fixtures.m


def real_format(properties, mutation=None):
    rows = []
    for key, value in properties.items():
        if key == 'ExecStartPre':
            values = value.replace('} {', '}\n{').splitlines()
            if mutation == 'missing':
                values = values[1:]
            elif mutation == 'extra':
                values.append('{ argv[]=/unexpected ; }')
            elif mutation == 'wrong':
                values[0] = '{ argv[]=/wrong ; }'
            rows.extend(key + '=' + x for x in values)
        else:
            rows.append(key + '=' + value)
    return '\n'.join(rows)


def prepared(tmp_path, monkeypatch, direction='apply', mutation=None):
    plan, approval, _ = fixtures.setup.__wrapped__(tmp_path)
    side = 'after' if direction == 'apply' else 'before'
    host = repair.repaired_host(m)(plan)
    provider = {'pid': 10, 'invocation_id': 'unchanged'}
    monkeypatch.setattr(host, 'snapshot', lambda: {'provider': provider})
    monkeypatch.setattr(host, 'audit', lambda: None)
    rid = m.pins()[side]['release_id']
    monkeypatch.setattr(m, 'check_release', lambda *_: {'release_id': rid})
    monkeypatch.setattr(m.rt, 'probe', lambda _: {'ready': False, 'reasons': ['reference_age'], 'provider': provider})

    def command(argv):
        if argv[0] == '/usr/bin/systemctl':
            stream = argv[2].split('@')[1].split('.')[0]
            return real_format(fixtures.effective_unit(plan, side, stream), mutation)
        assert argv[0] == '/usr/sbin/runuser' and 'environment_check' in argv[argv.index('-c') + 1]
        return json.dumps({'release_id': rid})

    monkeypatch.setattr(m.rt, 'command', command)
    return plan, approval, host


def test_actual_repeated_property_form_exposes_old_overwrite(tmp_path, monkeypatch):
    plan, _, host = prepared(tmp_path, monkeypatch)
    old = m.LiveHost(plan).unit('marketdata-collector@depth.service')
    new = host.unit('marketdata-collector@depth.service')
    assert old['ExecStartPre'].count('argv[]=') == 1
    assert new['ExecStartPre'].count('argv[]=') == 2
    assert {k: v for k, v in old.items() if k != 'ExecStartPre'} == {k: v for k, v in new.items() if k != 'ExecStartPre'}


@pytest.mark.parametrize('direction', ['apply', 'rollback'])
def test_unmodified_activation_checks_accept_both_entries(tmp_path, monkeypatch, direction):
    _, _, host = prepared(tmp_path, monkeypatch, direction)
    result = host.activation({'baseline_provider': host.snapshot()}, direction)
    assert result['clock_at_commit']['ready'] is False
    assert result['systemctl_reader_recovery']['sha256'] == repair.digest((HERE / 'continue_runtime.py').read_bytes())
    assert all(u['ExecStartPre'].count('argv[]=') == 2 for u in result['activation']['units'].values())


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'wrong'])
def test_bad_start_checks_still_rejected(tmp_path, monkeypatch, mutation):
    _, _, host = prepared(tmp_path, monkeypatch, mutation=mutation)
    with pytest.raises(m.Hold, match='strict start checks'):
        host.activation({'baseline_provider': host.snapshot()}, 'apply')


def test_unexpected_duplicate_scalar_is_not_silently_overwritten():
    with pytest.raises(ValueError, match='repeated'):
        repair.parse_properties('User=yoonhc\nUser=root\n')


def test_pending_same_plan_commit_preserves_step_and_provenance(tmp_path, monkeypatch):
    plan, approval, host = prepared(tmp_path, monkeypatch)
    tx = m.Transition(plan, approval, host, boot=lambda: 'boot-A', trust=fixtures.fixture_trust)
    tx.stage()
    original_plan = (tx.directory / 'plan.json').read_bytes()
    outcome = tx.commit()
    assert outcome['configuration'] == 'apply_committed' and outcome['step_reopened'] is False
    assert outcome['current_clock_ready'] is False
    receipt = json.loads((tx.directory / 'apply-receipt.json').read_text())
    assert receipt['systemctl_reader_recovery']['original_tool_sha256'] == plan['tools_sha256']
    fixtures.assert_preserved(plan, receipt['ledger_after'])
    assert (tx.directory / 'plan.json').read_bytes() == original_plan
    assert tx.commit()['idempotent'] is True


def test_prior_concatenated_and_repeated_formats_agree():
    a = 'ExecStartPre={ argv[]=one ; } { argv[]=two ; }\nUser=yoonhc'
    b = 'ExecStartPre={ argv[]=one ; }\nExecStartPre={ argv[]=two ; }\nUser=yoonhc'
    assert repair.parse_properties(a)['ExecStartPre'].split() == repair.parse_properties(b)['ExecStartPre'].split()
