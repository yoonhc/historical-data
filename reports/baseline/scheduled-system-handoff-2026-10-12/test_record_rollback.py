"""Installed T05 wrapper + real tiny restic repo; manager bus alone is simulated.

No operating DB or repository is opened. Actual bus provenance is the separate
administrator preflight, not a claim made by this test.
"""
import copy
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BASE = HERE.with_name('scheduled-system-migration-2026-10-12')
sys.path.insert(0, str(BASE))
from test_migration import prepared, unchanged_clock
import test_migration
test_migration.PLAN = json.loads((HERE / 'configuration-plan.json').read_bytes())
import pinned
import run_system_archive
import transaction as tx

PARENT = Path(pinned.check_parents()['t05'])
sys.path[:0] = [str(PARENT), str(PARENT / 'tests'), str(PARENT / 'packages/marketdata/src')]
from conftest import store as store
import test_restic_archive as fixtures
from test_restic_archive import setup_archive as setup_archive
from test_retirement_clock import Samples, provider
from marketdata import backup, checkpoint_clock as clock, checkpoint_evidence as evidence
from scripts import archive_backups as archive

schedule = pinned.load_archive()
fixtures.BINARY = HERE.parents[2] / '.cache/restic/0.19.1/restic'


def manager_bus(monkeypatch):
    """Return genuine service_context schema, not a hand-written receipt."""
    original = schedule.subprocess.check_output
    invocation = 'e' * 32
    monkeypatch.setenv('INVOCATION_ID', invocation)
    for key in schedule.TRIGGER_KEYS[1:]:
        monkeypatch.delenv(key, raising=False)
    real, mono, job = 1791800000000000, 10000000, 81
    seen = []

    def query(argv, **kwargs):
        if argv[0] not in ('systemctl', 'busctl'):
            return original(argv, **kwargs)
        seen.append(argv[1])
        if argv[0] == 'systemctl':
            return f'Id={schedule.SERVICE}\nType=oneshot\nInvocationID={invocation}\nMainPID={os.getpid()}'
        signature, value = {
            'Job': ('(uo)', [job, f'/org/freedesktop/systemd1/job/{job}']),
            'Unit': ('(so)', [schedule.SERVICE, schedule.unit_bus_path(schedule.SERVICE)]),
            'JobType': ('s', 'start'), 'State': ('s', 'running'),
            'ActivationDetails': ('a(ss)', [
                ['trigger_unit', schedule.TIMER], ['trigger_timer_realtime_usec', str(real)],
                ['trigger_timer_monotonic_usec', str(mono)]]),
            'LastTriggerUSec': ('t', real), 'LastTriggerUSecMonotonic': ('t', mono),
        }[argv[-1]]
        return json.dumps({'type': signature, 'data': value})
    monkeypatch.setattr(schedule.subprocess, 'check_output', query)
    return seen


def test_real_system_records_survive_rollback_and_user_archive_continues(setup_archive, store, tmp_path, monkeypatch):
    p = setup_archive
    t = prepared(tmp_path)
    # Real generated registry/order/completion objects, no change to old binding scope.
    reg = evidence.Registry(tmp_path / 'completion-evidence', p.identity, 'd' * 64)
    samples = Samples()
    def observe():
        return clock.Guard(lambda: provider(samples), tmp_path / (uuid.uuid4().hex + '.jsonl'),
                           sampler=samples).check(fresh=True)
    with reg.lock(create=True):
        reg.baseline([])
    baseline = copy.deepcopy(reg.graph().baselines)
    t.stage()
    t.commit()
    system_copy = backup.create_backup(store, p.backup_dir / '2026-10-12' / 'system',
        min_free_gib=0, evidence=evidence.Producer(reg, observe))
    assert system_copy['clock_evidence_state'] == 'available'
    system_bundle = evidence.read_bundle(system_copy['path'])['bundle']
    o = schedule.start_observation(p)
    o.update(started_at='2023-12-31T00:00:00Z', excluded_backup_ids=[])
    # The separate full restore contract was already tested. This case exercises
    # preserving and continuing mixed-manager records, not another restore drill.
    o['restore']['status'] = 'interrupted_or_failed'
    schedule.save(p, o)
    seen = manager_bus(monkeypatch)
    original = schedule.service_context
    monkeypatch.setattr(schedule, 'service_context', original)
    run_system_archive.bind(schedule)
    first = schedule.run(p)  # actual backup, snapshots and check --read-data
    assert first['ok'] and first['context']['scheduled']
    assert first['context']['manager'] == 'system' and '--system' in seen
    saved_report = p.reports_root / 'scheduled' / (first['run_id'] + '.json')
    saved_bytes = saved_report.read_bytes()
    before_state = copy.deepcopy(archive.load_state(p))
    assert before_state['repository_check']['ok'] and before_state['repository_check']['read_data']
    system_record = before_state['checkpoints'][system_copy['backup_id']]
    assert len(system_record['stored']['snapshot_id']) == 64
    before_objects = {x: y for x, y in reg.graph().objects.items()}
    t.stage('rollback')
    t.commit()
    unchanged_clock(t.plan['ledger_before'], t.fs.json(tx.LEDGER))
    assert reg.graph().baselines == baseline and reg.graph().objects == before_objects
    assert evidence.read_bundle(system_copy['path'])['bundle'] == system_bundle
    # Actual old entrypoint default (user), not the adapter; same catalog/state.
    monkeypatch.setattr(schedule, 'service_context', original)
    samples.advance(60)
    store.put_raw(b'new bytes after return to user manager', 'test:user-after-system', 'test')
    user_copy = backup.create_backup(store, p.backup_dir / '2026-10-13' / 'user',
        min_free_gib=0, evidence=evidence.Producer(reg, observe))
    second = schedule.run(p)
    assert second['ok'] and second['context']['scheduled']
    assert second['context']['manager'] == 'user' and '--user' in seen
    assert 'manager_adapter' not in second['context']
    assert second['archive']['result']['processed'] == 1
    assert saved_report.read_bytes() == saved_bytes
    current = archive.load_state(p)
    assert current['checkpoints'][system_copy['backup_id']]['stored'] == system_record['stored']
    assert user_copy['backup_id'] in current['checkpoints']
    assert current['repository_check']['ok'] and current['repository_check']['read_data']
    assert reg.graph().baselines == baseline
    assert system_copy['backup_id'] in reg.graph().ancestors(user_copy['backup_id'])
    assert all(reg.graph().objects[k] == v for k, v in before_objects.items())
    ids = [r['run_id'] for r in schedule.load(p)['runs']]
    assert ids == [first['run_id'], second['run_id']]
