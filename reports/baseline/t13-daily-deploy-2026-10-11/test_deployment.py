"""Narrow deployment regressions: real show format and link replacement."""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('activation', HERE/'activate_daily.py')
act = importlib.util.module_from_spec(spec)
spec.loader.exec_module(act)


def test_actual_service_and_all_repeated_pre_checks():
    before = json.loads((HERE/'operating-before.json').read_text())
    raw = before['user_units'][act.SERVICE]['stdout']
    props = act.properties(raw)
    command = act.commands(props, 'ExecStart')[0]
    act.verify_service(props, before, command)
    extra = 'ExecStartPre={ path=/bin/true ; argv[]=/bin/true ; ignore_errors=no ; }\n'
    both = act.properties(raw+extra)
    assert len(act.commands(both,'ExecStartPre')) == len(act.commands(props,'ExecStartPre'))+1
    with pytest.raises(RuntimeError,match='ExecStartPre'):
        act.verify_service(both,before,command)


def test_single_valued_duplicate_rejected():
    with pytest.raises(RuntimeError,match='repeated property'):
        act.properties('MainPID=0\nMainPID=12\n')


def test_missing_clock_gate_rejected():
    before = json.loads((HERE/'operating-before.json').read_text())
    props = act.properties(before['user_units'][act.SERVICE]['stdout'])
    command = act.commands(props,'ExecStart')[0]
    props['ExecStartPre'] = []
    with pytest.raises(RuntimeError,match='ExecStartPre'):
        act.verify_service(props,before,command)


def test_atomic_unit_replacement_preserves_workspace_link_target(tmp_path):
    source=tmp_path/'workspace.service'; source.write_bytes(b'old source\n')
    link=tmp_path/'daily.service'; link.symlink_to(source)
    act.replace_link_with_file(link,b'fixed release\n')
    assert not link.is_symlink()
    assert link.read_bytes()==b'fixed release\n'
    assert source.read_bytes()==b'old source\n'
    assert link.stat().st_mode&0o777==0o644


def test_existing_replacement_temporary_file_is_not_overwritten(tmp_path):
    source=tmp_path/'workspace.service'; source.write_bytes(b'old source\n')
    link=tmp_path/'daily.service'; link.symlink_to(source)
    temp=link.with_name(link.name+'.t13-new'); temp.write_bytes(b'earlier partial\n')
    with pytest.raises(FileExistsError):
        act.replace_link_with_file(link,b'new bytes\n')
    assert link.is_symlink() and source.read_bytes()==b'old source\n'
    assert temp.read_bytes()==b'earlier partial\n'


def test_transaction_excludes_only_two_approved_base_paths():
    spec = importlib.util.spec_from_file_location('continuation', HERE/'continue_activation.py')
    continuation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(continuation)
    before = json.loads((HERE/'operating-before.json').read_text())
    result = continuation.unchanged_scope(before)
    assert set(before['protected_hashes'])-set(result['protected_hashes']) == {
        str(act.UNITS/act.SERVICE), str(act.UNITS/act.TIMER)}
    guard = '/etc/systemd/user/marketdata-daily.service.d/90-marketdata-clock.conf'
    assert result['protected_hashes'][guard] == before['protected_hashes'][guard]
    assert result['protected_hashes']['/var/lib/marketdata-clock/boot-state.json'] == before['protected_hashes']['/var/lib/marketdata-clock/boot-state.json']


def test_disabled_link_absent_is_safe_to_replace_without_source_change(tmp_path):
    source = tmp_path/'workspace.timer'; source.write_bytes(b'old timer\n')
    destination = tmp_path/'daily.timer'
    act.replace_link_with_file(destination,b'approved timer\n')
    assert source.read_bytes()==b'old timer\n'
    assert destination.read_bytes()==b'approved timer\n'
