"""Actual frozen T05 + tiny independent restic repository, no operating mutation.

Chrony, manager provenance and ownership providers are fixture inputs. The
installed planner, validator, inventory, journal, deletion and evidence code
are real. Actual systemd rename behavior has a separate root trial receipt.
"""
import errno
import json
from pathlib import Path

import pytest
from marketdata import backup
from marketdata.domain import json_bytes, sha256
from test_clock_retirement import (
    ready as ready, case as case, prepared as prepared, setup_archive as setup_archive,
    cold, approve_manual, allow_test_system_context,
)
from scripts import clock_retirement as safe


def test_reviewed_unit_scope_recovers_original_exdev_journal(case, monkeypatch):
    p, policy, settings, _, _, _, _, host = case
    plan = cold(case)['plan']
    assert len(plan['candidates']) == 2 and len(plan['keep']) == 3
    pid = plan['plan_id']
    plan_file = settings.state_root / 'retention/plans' / (pid + '.json')
    plan_bytes = plan_file.read_bytes()
    archive_bytes = p.state_file.read_bytes()
    registry_before = {str(f): sha256(f.read_bytes()) for f in (settings.state_root / 'evidence').rglob('*.json')}
    sources = {Path(c['entry']['path']) for c in plan['candidates']}
    approve_manual(settings, plan)
    original_proposal = safe.manual_proposal(settings, pid, resume=True)
    allow_test_system_context(monkeypatch)
    real_rename = Path.rename

    def fail_first_move(source, target):
        if source in sources:
            raise OSError(errno.EXDEV, 'Invalid cross-device link', str(source), None, str(target))
        return real_rename(source, target)

    with monkeypatch.context() as fault:
        fault.setattr(Path, 'rename', fail_first_move)
        failed = safe.execute(settings, 'apply-plan', pid, host_factory=host)
    assert failed['state'] == 'failed' and failed['deleted_this_run'] == 0, failed
    journal_path = Path(failed['retirement_journal'])
    original = json.loads(journal_path.read_bytes())
    assert original['status'] == 'interrupted' and len(original['attempts']) == 1
    assert all(i['phase'] == 'prepared' and i['unlinked_files'] == 0 for i in original['items'].values())
    inventories = {str(f): sha256(f.read_bytes()) for f in (settings.state_root / 'retention/inventories' / pid).iterdir()}
    assert all(source.exists() for source in sources)

    # Only the two mount declarations change. Ordinary generation STILL rejects
    # a historic plan, while an explicit new-scope exact-plan grant is accepted.
    for name in ('marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement-apply@.service'):
        path = settings.unit_directory / name
        text = path.read_text()
        old = 'ReadWritePaths=' + str(p.backup_dir) + ' ' + str(p.backup_dir.parent / ('.' + p.backup_dir.name + '.retiring'))
        assert text.count(old) == 1, text
        path.write_text(text.replace(old, 'ReadWritePaths=' + str(p.backup_dir.parent)))
    after = safe.proposal(settings)
    assert after['scope_id'] != original_proposal['scope_id']
    expected_scope = dict(original_proposal['scope'])
    expected_scope['system_units'] = after['scope']['system_units']
    assert expected_scope == after['scope']
    with pytest.raises(Exception, match='Plan scope differs'):
        safe.manual_proposal(settings, pid, resume=True)
    settings.authorization_file.write_bytes(json_bytes(after))
    disabled = safe.execute(settings, 'run', host_factory=host)
    assert disabled['state'] == 'deferred' and disabled['reason'] == 'authorization_pending', disabled

    grant = {**original_proposal, 'scope': after['scope'], 'scope_id': after['scope_id'],
             'enabled': True, 'approved_at': safe.now(), 'approval_reference': 'isolated reviewed unit-only transition',
             'scope_transition': {'before_scope_id': original_proposal['scope_id'], 'after_scope_id': after['scope_id'],
                                  'reviewed_run_id': failed['run_id'], 'original_plan_sha256': sha256(plan_bytes)}}
    settings.manual_authorization_file.write_bytes(json_bytes(grant))
    result = safe.execute(settings, 'apply-plan', pid, host_factory=host)
    assert result['state'] == 'complete' and result['deleted_this_run'] == 2, result
    current = json.loads(journal_path.read_bytes())
    assert current['status'] == 'complete' and current['attempts'][:-1] == original['attempts']
    assert len(current['attempts']) == 2 and current['attempts'][-1]['ok']
    assert all(i['phase'] == 'complete' for i in current['items'].values())
    assert all(not source.exists() for source in sources)
    assert all(backup.verify_backup(e['path'])['ok'] for e in plan['keep'])
    assert plan_file.read_bytes() == plan_bytes and p.state_file.read_bytes() == archive_bytes
    assert inventories == {str(f): sha256(f.read_bytes()) for f in (settings.state_root / 'retention/inventories' / pid).iterdir()}
    assert registry_before == {str(f): sha256(f.read_bytes()) for f in (settings.state_root / 'evidence').rglob('*.json')}
    # Engine idempotence is an additional backstop, not permission to dispatch
    # twice. The operating wrapper consumes dispatch intent before service start.
    repeated = safe.execute(settings, 'apply-plan', pid, host_factory=host)
    assert repeated['already_complete'] and repeated['deleted_this_run'] == 0
    assert json.loads(journal_path.read_bytes())['attempts'] == current['attempts']
