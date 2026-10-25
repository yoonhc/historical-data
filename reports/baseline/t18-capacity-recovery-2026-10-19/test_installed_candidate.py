"""Isolated real restic / installed preservation-module compatibility."""
import importlib.util
import json
from datetime import datetime
from pathlib import Path

from marketdata import backup, checkpoint_evidence as evidence, checkpoint_clock as clock
from marketdata.domain import sha256
from conftest import store
from test_restic_archive import setup_archive, agent
from test_retirement_clock import Samples, provider


def test_installed_completion_bundle_and_existing_request_survive_shared_outputs(setup_archive, store, tmp_path, monkeypatch):
    p = setup_archive
    reg = evidence.Registry(tmp_path / 'evidence', p.identity, 'd' * 64)
    samples = Samples()
    with reg.lock(create=True):
        reg.baseline([])

    def observe():
        return clock.Guard(lambda: provider(samples), tmp_path / 'clock.jsonl', sampler=samples).check(fresh=True)

    with monkeypatch.context() as m:
        m.setattr(backup, 'now', lambda: datetime.fromisoformat('2024-01-03T04:10:00+00:00'))
        created = backup.create_backup(store, p.backup_dir / '2024-01-03' / 'evidence', min_free_gib=0,
                                      evidence=evidence.Producer(reg, observe))
    first = agent(p, 'first-bundle')
    result = first.run()
    assert result['processed'] == 3
    record = first.state['checkpoints'][created['backup_id']]
    expected = evidence.read_bundle(created['path'])
    assert record['stored']['checkpoint_evidence']['sha256'] == expected['sha256']
    sid = record['stored']['snapshot_id']
    raw, _ = first.restic.command(['dump', sid, '/' + evidence.SIDECAR])
    assert sha256(raw) == expected['sha256']
    assert evidence.decode_bundle(raw)['bundle']['identity']['backup_id'] == created['backup_id']

    actual = Path('/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11/policy/request_contract.py')
    spec = importlib.util.spec_from_file_location('installed_request_contract', actual)
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    report = first.restic.reports / 'isolated.json'
    completed = {'run_id': 'isolated', 'ok': True, 'archive': result}
    report.write_bytes(contract.encode(completed))
    outputs = [str(path) for path in first.restic.reports.glob('*.stdout')]
    committed = contract.seal_result({'job': 'restic-archive', 'result': completed,
                                     'files': [str(report), *outputs], 'records': []})
    second = agent(p, 'after-request')
    assert second.run()['processed'] == 0
    assert contract.classify('restic-archive', 0, committed, {}, [])['state'] == 'complete'
    assert second.state['repository_check'] == first.state['repository_check']
    assert second.state['checkpoints'][created['backup_id']]['stored'] == record['stored']
    commands = [json.loads(path.read_bytes()) for path in second.restic.reports.glob('*.json')]
    verified = [r for r in commands if 'verified_stdout' in r]
    assert len(verified) == 3
    for r in verified:
        assert Path(r['stdout']).stat().st_ino == Path(r['verified_stdout']['body']).stat().st_ino
