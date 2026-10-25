"""Disposable filesystem/lock tests only. No production service or file mutation."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('t19_reclaim', HERE / 'reclaim.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
spec = importlib.util.spec_from_file_location('old_test_helpers', r.OLD.with_name('test_deduplicate.py'))
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
OID = 'c' * 64


def plan(tmp_path):
    return {'groups': [old.group(tmp_path)], 'counts': {'replacement_paths': 2},
            'capacity': {'expected_reclaim_bytes': 32768}}


def manager(active_service=False):
    value = {}
    for name in (r.TIMER, r.SERVICE):
        value[name] = dict(Id=name, LoadState='loaded', NeedDaemonReload='no', DropInPaths='',
                           FragmentPath='/etc/systemd/system/' + name, Job='', MainPID='0', ControlPID='0',
                           ActiveState='active' if name == r.TIMER else 'inactive', UnitFileState='enabled')
    if active_service:
        value[r.SERVICE].update(ActiveState='activating', MainPID='12', Job='123')
    return value


def test_existing_lock_content_and_inode_unchanged_and_busy_is_nonblocking(tmp_path):
    lock = tmp_path / 'lock'
    lock.write_bytes(b'original lock metadata\n')
    before = lock.stat()
    with r.exclusive(lock):
        with pytest.raises(r.Deferred, match='lock_busy'):
            with r.exclusive(lock):
                pytest.fail('second lock acquired')
    assert lock.read_bytes() == b'original lock metadata\n'
    assert (lock.stat().st_ino, lock.stat().st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_active_timer_required_and_starting_service_deferred_only_at_entry():
    r.manager_guard(manager(), idle=True)
    with pytest.raises(r.Deferred, match='not_idle'):
        r.manager_guard(manager(True), idle=True)
    r.manager_guard(manager(True), idle=False)
    value = manager()
    value[r.TIMER]['ActiveState'] = 'inactive'
    with pytest.raises(RuntimeError, match='active/enabled'):
        r.manager_guard(value, idle=False)


@pytest.mark.parametrize('change', ['DropInPaths', 'FragmentPath', 'NeedDaemonReload'])
def test_manager_configuration_drift_refused(change):
    value = manager()
    value[r.SERVICE][change] = 'unexpected'
    with pytest.raises(RuntimeError, match='configuration differs'):
        r.manager_guard(value, idle=True)


def test_metadata_only_check_does_not_claim_payload_read(tmp_path):
    p = plan(tmp_path)
    before = [Path(i['path']).stat().st_ino for i in p['groups'][0]['targets']]
    assert r.inspect(p, OID) == 0
    assert before == [Path(i['path']).stat().st_ino for i in p['groups'][0]['targets']]


def test_completed_group_hashes_one_body_and_keeps_every_path(tmp_path):
    p = plan(tmp_path)
    reads = []
    def read(path):
        reads.append(path)
        return Path(path).read_bytes()
    assert r.inspect(p, OID, reader=read) == 0
    assert len(reads) == 3
    assert r.replace_all(p, OID, lambda e: None, r.ReadBudget(10**12), lambda: None) == 2
    reads.clear()
    assert r.inspect(p, OID, reader=read) == 2
    assert reads == [p['groups'][0]['source']['path']]
    assert r.replace_all(p, OID, lambda e: None, r.ReadBudget(10**12), lambda: None) == 0


@pytest.mark.parametrize('boundary', ['temporary', 'replace'])
def test_real_sigkill_releases_lock_and_exact_resume_preserves_original_receipts(tmp_path, boundary):
    p = plan(tmp_path)
    pfile = tmp_path / 'plan.json'
    pfile.write_text(json.dumps(p))
    receipt = tmp_path / 'receipt.json'
    receipt.write_bytes(b'{"unchanged":"original receipt"}')
    lock = tmp_path / 'lock'
    lock.touch()
    code = '''
import importlib.util,json,os,signal,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('test_child',sys.argv[1]);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
p=json.loads(Path(sys.argv[2]).read_bytes());g=p['groups'][0]
name='link' if sys.argv[4]=='temporary' else 'replace'
original=getattr(r.d.os,name)
def crash(*a,**k):
    original(*a,**k)
    os.kill(os.getpid(),signal.SIGKILL)
setattr(r.d.os,name,crash)
with r.exclusive(Path(sys.argv[3])):
    r.replace(g,g['targets'][0],'c'*64,lambda e:None,r.ReadBudget(10**12))
'''
    child = subprocess.run([sys.executable, '-B', '-I', '-c', code, str(HERE / 'reclaim.py'),
                            str(pfile), str(lock), boundary], capture_output=True, text=True)
    assert child.returncode == -signal.SIGKILL, child.stderr
    with r.exclusive(lock):
        assert r.inspect(p, OID, reader=lambda x: Path(x).read_bytes()) == (boundary == 'replace')
        r.replace_all(p, OID, lambda e: None, r.ReadBudget(10**12), lambda: None)
        assert r.inspect(p, OID, reader=lambda x: Path(x).read_bytes()) == 2
    assert receipt.read_bytes() == b'{"unchanged":"original receipt"}'
    assert all(not r.d.temporary(t, OID).exists() for t in p['groups'][0]['targets'])


def test_directory_sync_failure_after_replace_repeated_on_resume(tmp_path, monkeypatch):
    p = plan(tmp_path)
    group, target = p['groups'][0], p['groups'][0]['targets'][0]
    original = r.d.sync_directory
    calls = []
    def fail_after_replace(path):
        calls.append(path)
        if len(calls) == 2:
            raise OSError('directory sync failed after replace')
        original(path)
    monkeypatch.setattr(r.d, 'sync_directory', fail_after_replace)
    with pytest.raises(OSError):
        r.replace(group, target, OID, lambda e: None, r.ReadBudget(10**12))
    assert not r.d.temporary(target, OID).exists()
    assert r.inspect(p, OID) == 1
    assert r.replace(group, target, OID, lambda e: None, r.ReadBudget(10**12)) is False
    assert len(calls) == 3


def test_space_hold_and_audit_failure_never_start_target_replacement(tmp_path, monkeypatch):
    p = plan(tmp_path)
    monkeypatch.setattr(r, 'available', lambda: 999)
    with pytest.raises(r.Deferred, match='space'):
        r.replace_all(p, OID, lambda e: None, r.ReadBudget(10**12),
                      lambda: r.space_guard({'collector_reserve_bytes': 1000, 'audit_budget_bytes': 10}))
    assert r.inspect(p, OID) == 0
    def fail(_):
        raise OSError('cannot record intent')
    with pytest.raises(OSError):
        r.replace_all(p, OID, fail, r.ReadBudget(10**12), lambda: None)
    assert r.inspect(p, OID) == 0


def test_authority_is_once_and_budget_is_bounded(tmp_path):
    audit = r.Audit(tmp_path / 'audit', 8 * 1024**2, 'attempt')
    first = audit.authority(OID, 'apply')
    raw = (audit.root / 'authority.json').read_bytes()
    assert audit.authority(OID, 'resume') == first
    assert audit.authority(OID, 'apply') == first
    assert (audit.root / 'authority.json').read_bytes() == raw
    audit.budget = 1024
    with pytest.raises(RuntimeError, match='budget'):
        audit.emit({'phase': 'before_replace'})


def test_receipt_change_refuses_without_touching_outputs(tmp_path):
    p = plan(tmp_path)
    for item in [p['groups'][0]['source'], *p['groups'][0]['targets']]:
        for prefix in ('command_receipt', 'run_receipt'):
            path = Path(item['path']).with_suffix('.' + prefix)
            path.write_bytes(b'original')
            item[prefix] = str(path)
            item[prefix + '_sha256'] = r.digest(b'original')
    assert r.verify_receipts(p) == 6
    Path(p['groups'][0]['targets'][0]['command_receipt']).write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='Receipt changed'):
        r.verify_receipts(p)
    assert r.inspect(p, OID) == 0


def test_exact_real_selection_paths_and_traversal_rejection():
    p = json.loads(r.SELECTION.read_bytes())['proposal']
    r.validate_paths(p)
    changed = copy.deepcopy(p)
    changed['groups'][0]['targets'][0]['path'] = str(r.ROOT / '..' / 'outside.stdout')
    with pytest.raises(RuntimeError, match='absolute path'):
        r.validate_paths(changed)


def test_adapter_apply_with_natural_dispatch_and_locked_busy_entry(tmp_path, monkeypatch):
    p = plan(tmp_path)
    lock, state, request = tmp_path / 'lock', tmp_path / 'state.json', tmp_path / 'request.json'
    lock.touch()
    state.write_text('{"active_operation": null}')
    request.write_text('{"request_id":"same","pins":{"day":"2026-10-25"},"state":"complete"}')
    monkeypatch.setattr(r, 'LOCK', lock)
    monkeypatch.setattr(r, 'STATE', state)
    monkeypatch.setattr(r, 'REQUEST', request)
    monkeypatch.setattr(r, 'ROOT', tmp_path)
    monkeypatch.setattr(r, 'static_guard', lambda op: None)
    monkeypatch.setattr(r, 'verify_receipts', lambda p: 0)
    audit = r.Audit(tmp_path / 'audit', 8 * 1024**2, 'attempt')
    operation = {'read_budget_bytes_per_second': 10**12,
                 'collector_reserve_bytes': 0, 'audit_budget_bytes': 8 * 1024**2}
    calls = []
    def status():
        calls.append(1)
        return manager(active_service=len(calls) >= 3)
    monkeypatch.setattr(r, 'units', status)
    with r.exclusive(lock):
        with pytest.raises(r.Deferred, match='lock_busy'):
            r.execute(operation, OID, p, 'apply', audit, {})
    assert r.inspect(p, OID) == 0
    calls.clear()
    report = {'passed': False}
    r.execute(operation, OID, p, 'apply', audit, report)
    assert report['passed'] and report['state'] == 'complete'
    assert report['replaced_this_attempt'] == report['verified_targets'] == 2
    assert report['archive_state_unchanged_while_locked']
    assert report['manager_before_unlock'][r.SERVICE]['MainPID'] == '12'
    assert state.read_text() == '{"active_operation": null}'
    assert json.loads(request.read_bytes())['pins'] == {'day': '2026-10-25'}


def test_checkpoint_and_new_logs_are_not_frozen_inputs(tmp_path):
    p = plan(tmp_path)
    (tmp_path / 'unrelated-new-checkpoint.json').write_text('{"new":true}')
    (tmp_path / 'unrelated-new-run.json').write_text('{"ok":true}')
    assert r.inspect(p, OID, reader=lambda path: Path(path).read_bytes()) == 0
    assert r.replace_all(p, OID, lambda e: None, r.ReadBudget(10**12), lambda: None) == 2
    assert (tmp_path / 'unrelated-new-checkpoint.json').read_text() == '{"new":true}'


def test_real_installed_lock_scheduler_wrapper_preserve_failed75_then_same_request(tmp_path, monkeypatch):
    """Real installed control flow/files/thread/locks; isolated clock and body.

    No systemd timer, Chrony query, restic repository or production request used.
    """
    release = Path('/opt/marketdata/releases/restic-output-20261022-585c3e3686704e5c')
    monkeypatch.syspath_prepend(str(release / 'preserve'))
    monkeypatch.syspath_prepend(str(release / 'preserve/packages/marketdata/src'))
    from scripts import archive_backups as ab, restic_schedule as rs
    from marketdata import job_clock as jc
    from marketdata.store import atomic_write
    from marketdata.domain import json_bytes
    assert Path(ab.__file__).is_relative_to(release)
    assert Path(rs.__file__).is_relative_to(release)
    spec = importlib.util.spec_from_file_location('installed_wrapper', release / 'policy/run_job.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    binding, config = tmp_path / 'binding.json', tmp_path / 'profile.toml'
    binding.write_text('{}')
    config.write_text('isolated = true\n')
    reports, state_root = tmp_path / 'runs', tmp_path / 'state'
    reports.mkdir()
    state_file = state_root / 'repo/scope/state.json'
    state_file.parent.mkdir(parents=True)
    lock = state_root / 'repo/archive.lock'
    lock.touch()
    binary = tmp_path / 'binary'
    binary.write_bytes(b'no restic execution')
    profile = SimpleNamespace(state_root=state_root, expected_repository_id='repo',
        state_file=state_file, identity={'scope': 'isolated'}, reports_root=reports,
        fingerprint='fixture', store_root=tmp_path, restic_binary=binary, validate=lambda: None,
        guard=lambda: None)
    initial = {'schema': 1, 'identity': profile.identity, 'checkpoints': {},
               'repository_check': None, 'restore_checks': []}
    atomic_write(state_file, json_bytes(initial))
    rs.save(profile, {'version': 1, 'identity': profile.identity, 'started_at': ab.now().isoformat(),
                     'qualified_dates': {}, 'required_distinct_dates': 3, 'excluded_backup_ids': [],
                     'runs': [], 'restore': {'status': 'passed'}})
    entered = []
    class ArchiveBody:
        def __init__(self, p, reports):
            entered.append(str(reports))
            self.state = ab.load_state(p)
            self.restic = SimpleNamespace(identify=lambda: 'isolated body')
        def save(self):
            atomic_write(state_file, json_bytes(self.state))
        def run(self):
            return {'ok': True, 'stored': []}
    monkeypatch.setattr(ab, 'Archive', ArchiveBody)
    monkeypatch.setattr(ab, 'watch', lambda p, report, stop: stop.wait())
    monkeypatch.setattr(rs.Profile, 'load', lambda _: profile)
    monkeypatch.setattr(rs, 'service_context', lambda *a, **k: {'scheduled': False, 'classification': 'isolated'})
    monkeypatch.setattr(rs.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0))
    sample = dict(wall_ns=1760000000000000000, monotonic_ns=100000000000,
                  boottime_ns=100000000000, span_ns=1000)
    provider = dict(tracking=dict(reference_id='C0000201', reference_address='192.0.2.1',
        stratum=3, reference_time=1760000000-7200, correction=.001, root_delay=.02,
        root_dispersion=.01, skew_ppm=1., leap='Normal'), boot_id='isolated',
        provider={'pid': 12, 'invocation_id': 'p1'}, config_sha256='c', bootstrap_sha256='b',
        runtime_profile_sha256='r', ledger_sha256='l')
    monkeypatch.setattr(runner, 'query_factory', lambda _: lambda: provider)
    Context = jc.Context
    monkeypatch.setattr(jc, 'Context', lambda *a, **k: Context(*a, sampler=lambda: sample,
                                                           monitor_interval=.01, **k))
    request = tmp_path / 'requests/restic-archive/request.json'
    def run():
        monkeypatch.setattr(sys, 'argv', ['run_job', '--job', 'restic-archive', '--binding', str(binding),
            '--state-root', str(tmp_path / 'requests'), '--run', '--', '--profile', str(config), 'run'])
        return runner.main()
    # Keep a pre-existing logical pin through the real failed request path.
    original_point = Context.point
    def point(self, *a, **k):
        if not self.pins:
            self.pins['retained_fixture_day'] = '2026-10-25'
            self.save_pins(self.pins)
        return original_point(self, *a, **k)
    monkeypatch.setattr(Context, 'point', point)
    with r.exclusive(lock):
        assert run() == 75
    first = json.loads(request.read_bytes())
    assert first['state'] == 'failed' and first['exit_code'] == 75
    assert first['pins'] == {'retained_fixture_day': '2026-10-25'}
    assert entered == [] and ab.load_state(profile) == initial
    failed_run = next(reports.glob('*/run.json'))
    original_failure = failed_run.read_bytes()
    assert json.loads(original_failure)['status'] == 'lock_busy'
    assert run() == 0
    second = json.loads(request.read_bytes())
    assert second['state'] == 'complete' and second['identity'] == first['identity']
    assert second['request_id'] == first['request_id'] and second['pins'] == first['pins']
    assert second['attempt']['id'] != first['attempt']['id'] and len(entered) == 1
    assert failed_run.read_bytes() == original_failure
    old_attempt = request.parent / 'attempts' / first['request_id'] / (first['attempt']['id'] + '.json')
    assert json.loads(old_attempt.read_bytes())['exit_code'] == 75
