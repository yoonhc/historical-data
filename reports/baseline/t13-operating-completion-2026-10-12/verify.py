"""Fixed T13 operating audit and one selective explicit-snapshot restore.

No scheduler/Store construction, HTTP collection, backup, service control, clock
control, deletion, or operational-state writes. Only this report and a fresh
restore directory are written. The six daily tables are read with mode=ro.
"""
import argparse
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import time
import tomllib
import uuid
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = HERE.parent / 't13-operating-review-2026-10-11'
CP = Path('/mnt/backup/marketdata-historical/automatic/2026-10-12/ea0b6b077d354a9290f2d919522771a9')
BID = 'fd4bf8417a5203f4dfbd467118740ecc2441d642096471737727fcbcdad00fdf'
SID = '616ed4d6dbd3636b1970c5e623e3a770d42d31ae9ee3b9b26102e9675110a470'
spec = importlib.util.spec_from_file_location('prior_t13_read_only', OLD / 'review.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_bytes())


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def file_hash(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def save(name, value):
    with (HERE / name).open('x') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
        handle.write('\n')


def compressed(name, value):
    blob = gzip.compress(canonical(value), mtime=0)
    with (HERE / name).open('xb') as handle:
        handle.write(blob)
    return digest(blob)


def profile():
    return tomllib.loads(Path('/home/yoonhc/.config/marketdata/restic-archive.toml').read_text())


def record_map(records):
    return {record['id']: record for record in records}


def target_check(ledger, target):
    tasks, attempts = record_map(ledger['daily_tasks']), record_map(ledger['daily_attempts'])
    for item in target['attempts']:
        task, attempt = tasks[item['task_id']], attempts[item['attempt_id']]
        assert task['state'] == attempt['state'] == 'complete', item['task_id']
        assert digest(canonical(task)) == item['task_record_sha256'], ('task', item['task_id'])
        assert digest(canonical(attempt)) == item['attempt_record_sha256'], ('attempt', item['attempt_id'])
        assert digest(canonical(task['proof'])) == item['completed_proof_sha256']
    for item in target['shared_success_refreshes']:
        task = tasks[item['task_id']]
        assert task['state'] == 'complete' and task['covered_by'] == item['covered_by']
        assert digest(canonical(task)) == item['task_record_sha256']
        assert digest(canonical(task['proof'])) == item['proof_sha256']
    for item in target['existing_complete_acquisitions']:
        task = tasks[item['task_id']]
        assert task['state'] == 'complete' and task['purpose'] == 'acquire'
        assert digest(canonical(task['proof'])) == item['proof_sha256']
    return {'fixed_attempts': len(target['attempts']), 'related_tasks': len(target['selected_task_ids']),
            'old_complete_acquisitions': len(target['existing_complete_acquisitions']), 'unchanged': True}


def contents(root, db, target):
    from marketdata.fileio import packed_record, read_packed, safe_file
    checked, inputs = [], {}
    for item in target['attempts']:
        checked.append({'attempt_id': item['attempt_id'], 'task_id': item['task_id'],
                        'verified': previous.verify_proof(root, db, item['proof'], item['key'],
                            None if item['purpose'] == 'observe' else item['day'])})
        for identity in item['input_artifact_ids']:
            if identity in inputs:
                continue
            row = dict(db.execute('SELECT * FROM artifacts WHERE id=?', (identity,)).fetchone())
            packed = packed_record(db, row['path'])
            blob = read_packed(root, packed) if packed else safe_file(root, row['path']).read_bytes()
            assert digest(blob) == identity and len(blob) == row['bytes']
            inputs[identity] = {**row, 'physical_path': packed['pack_path'] if packed else row['path']}
    return {'attempts': checked, 'input_artifacts': inputs}


def boundary(runs, at):
    before = max((r for r in runs if r['started_at'] < at), key=lambda r: r['started_at'])
    after = min((r for r in runs if r['started_at'] >= at), key=lambda r: r['started_at'])
    old = {t['id'] for t in before['pending']}
    assert old <= {t['id'] for t in after['pending']} and not after['processed']
    assert before['completed_tasks'] == after['completed_tasks'] == 188
    assert after['runner_ok'] and not after['data_complete']
    added = [t for t in after['pending'] if t['id'] not in old]
    assert all(t['next_at'] > after['as_of'] for t in added)
    return {'boundary_utc': at, 'before': before, 'after': after, 'added': added,
            'added_by_purpose': dict(Counter(t['purpose'] for t in added)), 'previous_pending_preserved': True}


def prepare():
    target = read(OLD / 'completion-targets.json')
    assert digest(canonical({k: v for k, v in target.items() if k != 'target_id'})) == target['target_id']
    with ExitStack() as stack:
        fd = os.open(CP.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        live_db = previous.connection(ROOT / 'state/catalog.sqlite')
        stack.callback(live_db.close)
        live = previous.ledger(live_db)
        live_db.close()  # Release live read transaction before longer checkpoint reads.
        db = previous.connection(CP / 'state/catalog.sqlite', immutable=True)
        stack.callback(db.close)
        checkpoint = previous.ledger(db)
        checks = {label: target_check(records, target) for label, records in [('live', live), ('checkpoint', checkpoint)]}
        runs = live['daily_runs']
        boundaries = {'kst_cycle': boundary(runs, '2026-10-11T15:00:00'),
                      'utc_day': boundary(runs, '2026-10-12T00:00:00')}
        assert boundaries['kst_cycle']['added_by_purpose'] == {'refresh': 18, 'observe': 2}
        assert boundaries['utc_day']['added_by_purpose'] == {'acquire': 6, 'refresh': 6}
        assert all(t['day'] == '2026-10-11' for t in boundaries['utc_day']['added'])
        tasks = record_map(live['daily_tasks'])
        old_acquires = {t['task_id'] for t in target['existing_complete_acquisitions']}
        today_attempts = [a for a in live['daily_attempts'] if a['started_at'] >= '2026-10-11T15:00:00']
        assert not old_acquires & {a['task_id'] for a in today_attempts}
        assert all(a['started_at'] >= tasks[a['task_id']]['first_eligible_at'] for a in live['daily_attempts'])
        assert len({(a['task_id'], a['id']) for a in live['daily_attempts']}) == len(live['daily_attempts'])
        actual = contents(CP, db, target)
        save('checkpoint-contents.json', actual)
        ledger_sha = compressed('ledger-records.json.gz', {'live': live, 'checkpoint': checkpoint})
        save('schedule-review.json', {'as_of': now(), 'target_id': target['target_id'], 'checks': checks,
             'ledger_sha256': ledger_sha, 'boundaries': boundaries,
             'live_counts': {k: len(v) for k, v in live.items()},
             'live_states': dict(Counter(t['state'] for t in live['daily_tasks'])),
             'checkpoint_counts': {k: len(v) for k, v in checkpoint.items()},
             'checkpoint_states': dict(Counter(t['state'] for t in checkpoint['daily_tasks'])),
             'today_attempts': today_attempts, 'old_acquisitions_retried': 0,
             'all_attempts_respect_first_eligible_at': True, 'latest_run': max(runs, key=lambda r: r['started_at']),
             'passed': True})
        manifest = read(CP / 'backup.json')
        assert manifest['backup_id'] == BID
        manifest_files = {f['path']: f for f in manifest['files']}
        paths = {'state/catalog.sqlite', 'backup.json', 'checkpoint-evidence.json'}
        for row in actual['attempts']:
            for kind in ['revisions', 'artifacts']:
                paths.update(r['physical_path'] for r in row['verified'][kind])
        paths.update(r['physical_path'] for r in actual['input_artifacts'].values())
        selected = []
        for path in sorted(paths):
            assert not Path(path).is_absolute() and '..' not in Path(path).parts
            file = CP / path
            assert file.is_file() and not file.is_symlink()
            item = {'path': path, 'bytes': file.stat().st_size, 'sha256': file_hash(file)}
            if path in manifest_files:
                assert item['bytes'] == manifest_files[path]['bytes'] and item['sha256'] == manifest_files[path]['sha256']
            else:
                assert path in {'backup.json', 'checkpoint-evidence.json'}
            selected.append(item)
    prof = profile()
    with (Path(prof['state_root']) / prof['expected_repository_id'] / 'archive.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        state = read(Path(prof['state_root']) / prof['expected_repository_id'] / prof['archive_scope'] / 'state.json')
        entry, check = state['checkpoints'][BID], state['repository_check']
        assert entry['stored']['snapshot_id'] == SID and check['read_data'] and check['ok']
        assert check['status'] == 'complete' and SID in check['snapshot_ids']
        assert entry['entry']['manifest_sha256'] == file_hash(CP / 'backup.json')
        assert entry['stored']['checkpoint_evidence']['sha256'] == file_hash(CP / 'checkpoint-evidence.json')
        save('archive-evidence.json', {'checkpoint_record': entry, 'full_check': check})
        for name in ['stdout', 'stderr']:
            source = Path(check['receipt'][name])
            with (HERE / ('scheduled-full-check.' + name)).open('xb') as out:
                out.write(source.read_bytes())
    save('maintenance.json', read(ROOT / 'state/maintenance-daily-latest.json'))
    work = Path(prof['restore_work_root']) / ('t13-completion-20261012-' + uuid.uuid4().hex[:16])
    plan = {'scope': 'selective T13 ledger/26 proofs/data and included T05 evidence; not full-store restore',
            'prepared_at': now(), 'target_id': target['target_id'], 'backup_id': BID, 'snapshot_id': SID,
            'checkpoint': str(CP), 'restore_path': str(work), 'files': selected,
            'bytes': sum(f['bytes'] for f in selected), 'includes_t05': True,
            'new_backup_or_repository_check': False}
    save('restore-plan.json', plan)
    with (HERE / 'restore-includes.txt').open('x') as handle:
        handle.write(''.join('/' + f['path'] + '\n' for f in selected))
    print(json.dumps({'passed': True, 'selected_files': len(selected), 'bytes': plan['bytes'],
                      'live_target_checks': checks['live'], 'checkpoint_target_checks': checks['checkpoint'],
                      'restore_path': str(work)}, indent=2), flush=True)


def host(label):
    commands = {
        'daily': ['systemctl', '--user', 'show', 'marketdata-daily.service', 'marketdata-daily.timer',
                  'marketdata-clock-ready.service', '--property=Id,ActiveState,SubState,Result,ExecStart,ExecStartPre,UnitFileState'],
        'protected_services': ['systemctl', 'show', 'chrony.service', 'marketdata-collector@depth.service',
                  'marketdata-collector@aggtrade.service', '--property=Id,ActiveState,MainPID,InvocationID'],
        'jobs': ['systemctl', '--user', 'list-jobs', '--no-pager']}
    result = {'as_of': now(), 'commands': {}}
    for name, argv in commands.items():
        p = subprocess.run(argv, capture_output=True, text=True, timeout=40, check=True)
        result['commands'][name] = {'argv': argv, 'stdout': p.stdout, 'returncode': p.returncode}
    config_paths = [Path(p) for p in ['/etc/marketdata/daily-scheduler.toml',
        '/etc/marketdata/daily-publication-probe.toml', '/etc/chrony/marketdata.conf',
        '/etc/systemd/user/marketdata-daily.service.d/90-marketdata-clock.conf',
        '/home/yoonhc/.config/systemd/user/marketdata-daily.service',
        '/home/yoonhc/.config/systemd/user/marketdata-daily.timer']]
    result['config_sha256'] = {str(p): file_hash(p) for p in config_paths}
    result['retirement_masks'] = {str(Path(base) / unit): os.readlink(Path(base) / unit)
        for base in ['/etc/systemd/system', '/home/yoonhc/.config/systemd/user']
        for unit in ['marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer']}
    assert set(result['retirement_masks'].values()) == {'/dev/null'}
    result['publication_observer_enabled'] = tomllib.loads(config_paths[1].read_text())['observation']['enabled']
    assert result['publication_observer_enabled'] is False
    if label == 'before':
        argv = ['journalctl', '--user', '-u', 'marketdata-daily.service', '-u', 'marketdata-clock-ready.service',
                '--since', '2026-10-11 21:00:00', '--no-pager', '-o', 'json']
        p = subprocess.run(argv, capture_output=True, timeout=40, check=True)
        with (HERE / 'journal.jsonl.gz').open('xb') as out:
            out.write(gzip.compress(p.stdout, mtime=0))
        result['journal_lines'] = len(p.stdout.splitlines())
    else:
        before = read(HERE / 'host-before.json')
        for field in ['config_sha256', 'retirement_masks', 'publication_observer_enabled']:
            assert result[field] == before[field], field
        assert result['commands']['protected_services']['stdout'] == before['commands']['protected_services']['stdout']
        result['protected_configuration_and_invocations_unchanged'] = True
    save('host-' + label + '.json', result)
    print(json.dumps({'host': label, 'passed': True, 'as_of': result['as_of']}, indent=2))


def restore():
    plan, prof = read(HERE / 'restore-plan.json'), profile()
    work, parent = Path(plan['restore_path']), Path(prof['restore_work_root'])
    assert plan['snapshot_id'] == SID and plan['backup_id'] == BID
    assert work.parent == parent and parent.resolve() == parent and not work.exists()
    assert shutil.disk_usage(parent).free > plan['bytes'] + 20 * 1024**3
    assert (HERE / 'restore-includes.txt').read_text() == ''.join('/' + f['path'] + '\n' for f in plan['files'])
    base = [prof['restic_binary'], '-r', prof['restic_repository'], '--password-file',
            prof['restic_password_file'], '--no-cache', '--no-lock', '--json']
    with (Path(prof['state_root']) / prof['expected_repository_id'] / 'archive.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        p = subprocess.run(base + ['snapshots'], capture_output=True, text=True, timeout=45, check=True)
        snapshots = json.loads(p.stdout)
        snapshot = next(s for s in snapshots if s['id'] == SID)
        assert 'backup_id=' + BID in snapshot['tags']
        save('snapshot.json', snapshot)
        work.mkdir(mode=0o700)
        argv = base + ['restore', SID, '--target', str(work), '--include-file', str(HERE / 'restore-includes.txt'),
                       '--verify', '--overwrite', 'never']
        started, elapsed = now(), time.monotonic()
        with (HERE / 'restic-restore.stdout').open('xb') as out, (HERE / 'restic-restore.stderr').open('xb') as err:
            p = subprocess.run(argv, stdout=out, stderr=err, timeout=900)
        receipt = {'started_at': started, 'finished_at': now(), 'wall_seconds': time.monotonic() - elapsed,
                   'snapshot_id': SID, 'target': str(work), 'returncode': p.returncode,
                   'verify': True, 'overwrite': 'never', 'include_file_sha256': file_hash(HERE / 'restore-includes.txt')}
        save('restore-receipt.json', receipt)
        assert p.returncode == 0, p.returncode
        after = subprocess.run(base + ['snapshots'], capture_output=True, text=True, timeout=45, check=True)
        assert sorted(s['id'] for s in json.loads(after.stdout)) == sorted(s['id'] for s in snapshots)
    print(json.dumps(receipt, indent=2), flush=True)


def verify_restore():
    from marketdata import checkpoint_evidence as evidence
    plan, target = read(HERE / 'restore-plan.json'), read(OLD / 'completion-targets.json')
    work = Path(plan['restore_path'])
    for item in plan['files']:
        path = work / item['path']
        assert path.is_file() and not path.is_symlink()
        assert path.stat().st_size == item['bytes'] and file_hash(path) == item['sha256'], item['path']
        assert (path.stat().st_dev, path.stat().st_ino) != ((CP / item['path']).stat().st_dev, (CP / item['path']).stat().st_ino)
    expected_paths = {i['path'] for i in plan['files']}
    actual_paths = {p.relative_to(work).as_posix() for p in work.rglob('*') if p.is_file()}
    assert actual_paths == expected_paths
    archived = json.loads(gzip.decompress((HERE / 'ledger-records.json.gz').read_bytes()))['checkpoint']
    db = previous.connection(work / 'state/catalog.sqlite', immutable=True)
    try:
        restored = previous.ledger(db)
        assert all(record_map(restored[t]) == record_map(archived[t]) for t in previous.TABLES)
        checks = target_check(restored, target)
        actual = contents(work, db, target)
        assert json.loads(json.dumps(actual, default=str)) == read(HERE / 'checkpoint-contents.json')
    finally:
        db.close()
    save('restored-contents.json', actual)
    bundle = evidence.read_bundle(work, read(work / 'backup.json'))
    archive = read(HERE / 'archive-evidence.json')
    assert bundle['sha256'] == archive['checkpoint_record']['stored']['checkpoint_evidence']['sha256']
    assert bundle['object_ids'] == archive['checkpoint_record']['stored']['checkpoint_evidence']['object_ids']
    graph = evidence.Graph(bundle['bundle']['objects'], bundle['bundle']['scope'])
    completion = bundle['bundle']['objects'][bundle['bundle']['completion']]
    assert completion['mode'] == 'after_completion' and completion['order'] is not None
    ancestors = sorted(graph.ancestors(BID))
    assert len(ancestors) == 7
    proofs = {bid: graph.proof(graph.objects[oid]['identity']) for bid, oid in graph.checkpoints.items()}
    assert len(proofs) == 8 and all(proofs.values())
    save('t05-restored-evidence.json', {'passed': True, 'backup_id': BID, 'snapshot_id': SID,
        'bundle_sha256': bundle['sha256'], 'object_ids': bundle['object_ids'], 'completion': completion,
        'ancestors': ancestors, 'completion_proofs': proofs, 'original_registry_read': False,
        'predecessor_checkpoint_read': False, 'deletion_authority': False,
        'scope': 'first regular completion/order and self-contained evidence restoration; T05 remains open'})
    result = {'passed': True, 'finished_at': now(), 'target_id': target['target_id'],
        'backup_id': BID, 'snapshot_id': SID, 'restored_files': len(plan['files']), 'restored_bytes': plan['bytes'],
        'same_checkpoint_ledger': {t: len(v) for t, v in restored.items()}, 'target_checks': checks,
        'parquet_rows': sum(r['rows'] for a in actual['attempts'] for r in a['verified']['revisions']),
        'verified_input_artifacts': len(actual['input_artifacts']), 'independent_inodes': True,
        't05_evidence_restored': True, 'full_store_restore_claimed': False}
    save('verification.json', result)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'host-before', 'restore', 'verify', 'host-after'])
    action = parser.parse_args().action
    {'prepare': prepare, 'host-before': lambda: host('before'), 'restore': restore,
     'verify': verify_restore, 'host-after': lambda: host('after')}[action]()
