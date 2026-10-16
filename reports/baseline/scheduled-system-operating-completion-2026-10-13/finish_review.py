"""Join captured operating evidence; reads only report files, no operating paths."""
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent


def read(name):
    content = (BASE / name).read_bytes()
    return json.loads(gzip.decompress(content) if name.endswith('.gz') else content)


def digest(name):
    return hashlib.sha256((BASE / name).read_bytes()).hexdigest()


def write(name, value):
    with (BASE / name).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main():
    journal = read('target-invocations.json')
    assert journal['code'] == 0
    assert journal['sha256'] == digest('target-invocations-full.jsonl.gz')
    records = journal['records']
    invocations = {
        'noon': ('marketdata-daily.service', '29da05b7ced34a12a98de652f09bfad7'),
        'noon_followup': ('marketdata-daily.service', 'a5c495a11ac04de6a5d1833813df7ce7'),
        'backup': ('marketdata-backup.service', '462ef57f62e342f0909a698839176e8a'),
        'archive': ('marketdata-restic-archive.service', '8f08dc1a9b884110ab14d47a941544c7'),
    }
    final = {}
    for name, (unit, invocation) in invocations.items():
        selected = [r for r in records if r['invocation_id'] == invocation]
        assert selected and all(r['unit'] == unit and r['uid'] == '1000' for r in selected)
        guards = [r['payload'] for r in selected if 'step_window_closed' in r['payload']]
        assert len(guards) == 1 and guards[0]['ready'] and guards[0]['step_window_closed']
        final[name] = selected[-1]['payload']

    ledger = read('noon-ledger.json.gz')
    assert set(final['noon']) - set(ledger['run']) == {'publication_observation'}
    assert {k: v for k, v in final['noon'].items() if k != 'publication_observation'} == ledger['run']
    for name in ('noon', 'noon_followup'):
        assert final[name]['publication_observation'] == {'requests': 0, 'state': 'disabled'}
        assert final[name]['runner_ok'] and not final[name]['review_required']
        assert final[name]['state_counts'] == {'complete': 232, 'planned': 20}
        assert not final[name]['data_complete']
    assert len(final['noon']['processed']) == 10
    assert final['noon_followup']['processed'] == []
    assert final['noon_followup']['run_outcome'] == 'no_due_tasks'
    assert final['noon_followup']['pending'] == final['noon']['pending']
    assert final['backup'] == read('maintenance.json') and final['backup']['ok']
    assert final['archive'] == read('scheduled-archive.json') and final['archive']['ok']

    linkage = {
        'passed': True,
        'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'system_invocations': invocations,
        'uid': 1000,
        'strict_guard_ready_and_closed': True,
        'noon_journal_equals_saved_run': True,
        'noon_followup': {k: final['noon_followup'][k] for k in (
            'id', 'started_at', 'finished_at', 'processed', 'state_counts',
            'run_outcome', 'runner_ok', 'data_complete', 'publication_observation')},
        'maintenance_journal_equals_report': True,
        'archive_journal_equals_report': True,
        'full_journal_capture': 'target-invocations-full.jsonl.gz',
        'full_journal_sha256': digest('target-invocations-full.jsonl.gz'),
    }
    verification = read('verification.json')
    bundle = read('snapshot-bundle-verification.json')
    host = read('host-state.json')
    assert verification['passed'] and bundle['passed'] and host['passed']
    assert all(host['checks'].values())
    for key in ('backup_id', 'snapshot_id'):
        assert verification[key] == bundle[key]
    assert bundle['bytes_equal_checkpoint_sidecar']
    assert len(bundle['object_ids']) == verification['t05_objects'] == 10
    assert bundle['completion_proof']['object_id'] == verification['t05_completion_id']
    write('execution-linkage.json', linkage)
    write('completion.json', {
        'passed': True,
        'completed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'scope': 'T15 first-stage system manager migration operating validation',
        'plan_id': host['plan_id'],
        'backup_id': verification['backup_id'],
        'snapshot_id': verification['snapshot_id'],
        'evidence': {name: digest(name) for name in (
            'verification.json', 'snapshot-bundle-verification.json',
            'host-state.json', 'execution-linkage.json')},
        'earlier_local_verification_snapshot_pending_resolved': True,
        't05_verified_new_successors': 2,
        't05_complete': False,
        'business_clock_policy_changed': False,
        'automatic_retirement': 'masked',
        'publication_observer': 'disabled',
        'operating_mutations': False,
        'bulk_restore': False,
        'repository_check_repeated': False,
    })
    print(json.dumps({'passed': True, 'completion': str(BASE / 'completion.json')}))


if __name__ == '__main__':
    main()
