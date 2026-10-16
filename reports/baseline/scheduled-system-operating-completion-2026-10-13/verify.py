"""Bounded read-only T15 audit: actual noon work and first system backup/archive.

No live layout lock/market reads, Store initialization, acquisition, service control,
backup, repository check or bulk restore. Read the immutable checkpoint's selected
noon data; read only the small evidence bundle from an explicit restic snapshot.
"""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tomllib

from marketdata.checkpoint_evidence import Graph, decode_bundle, identity, read_bundle
from marketdata.fileio import packed_record, read_packed, safe_file

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CP = Path('/mnt/backup/marketdata-historical/automatic/2026-10-13/9d4a3f83517044ebaf08b045514f15c5')
BID = '10dd21822fcc0371772f3cbd3dc5b0ec0a8d086829fb78e1e0a1cab023685665'
SID = 'a2dbcdd442459fbde79cb29a7244e3acf95f40c5ac69e37d5b3337a16adba378'
NOON_RUN = '3e9b9c059eaf4624902a3c4286efb168'
SCHEDULED = '4e629fc0390345c793c94ae0107c4cd5'
OLD = HERE.parent / 't13-operating-review-2026-10-11/review.py'
spec = importlib.util.spec_from_file_location('previous_read_only', OLD)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def save(name, value):
    with (HERE / name).open('x') as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, default=str)
        handle.write('\n')


def retain(name, blob):
    with (HERE / name).open('xb') as handle:
        handle.write(blob)
    return {'path': name, 'sha256': sha(blob), 'bytes': len(blob)}


def mapping(db, table):
    return {v['id']: v for v in (json.loads(r[0]) for r in db.execute('SELECT record FROM ' + table))}


def connection(path, immutable=False):
    db = sqlite3.connect(path.as_uri() + '?mode=ro' + ('&immutable=1' if immutable else ''), uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def main():
    started = datetime.now(timezone.utc).isoformat()
    with closing(connection(ROOT / 'state/catalog.sqlite')) as db:
        live = {name: mapping(db, name) for name in ('daily_tasks', 'daily_attempts', 'daily_runs')}
    run = live['daily_runs'][NOON_RUN]
    assert run['runner_ok'] and len(run['processed']) == 10 and not run['data_complete']
    tasks = {t: live['daily_tasks'][t] for t in run['processed']}
    attempts = {a['id']: a for a in live['daily_attempts'].values()
                if a['task_id'] in tasks and run['started_at'] <= a['started_at'] <= run['finished_at']}
    assert len(attempts) == 10
    assert Counter(t['purpose'] for t in tasks.values()) == {'acquire': 2, 'observe': 2, 'refresh': 6}
    covered = {t['id']: t for t in live['daily_tasks'].values() if t.get('covered_by') in attempts}
    assert len(covered) == 2
    for a in attempts.values():
        t = tasks[a['task_id']]
        assert t['state'] == a['state'] == 'complete' and t['proof'] == a['proof']
        assert t['attempts'] == [a['id']] and a['started_at'] >= t['first_eligible_at']
    assert all(t['state'] == 'complete' and not t['attempts'] for t in covered.values())
    manifest_bytes = (CP / 'backup.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest['backup_id'] == BID and manifest['source_root'] == str(ROOT)
    selected = {'run': run, 'tasks': tasks | covered, 'attempts': attempts}
    ledger_evidence = retain('noon-ledger.json.gz', gzip.compress(json.dumps(selected, sort_keys=True).encode(), mtime=0))
    verified, inputs = [], {}
    # Only immutable checkpoint reads; no live file-layout lock or live raw reads.
    with closing(connection(CP / 'state/catalog.sqlite', immutable=True)) as db:
        checkpoint = {name: mapping(db, name) for name in ('daily_tasks', 'daily_attempts', 'daily_runs')}
        assert checkpoint['daily_runs'][NOON_RUN] == run
        for tid, t in selected['tasks'].items():
            assert checkpoint['daily_tasks'][tid] == t
        for aid, a in attempts.items():
            assert checkpoint['daily_attempts'][aid] == a
            t = tasks[a['task_id']]
            proof = previous.verify_proof(CP, db, a['proof'], t['key'], None if t['purpose'] == 'observe' else t['day'])
            verified.append({'attempt_id': aid, 'task_id': t['id'], 'purpose': t['purpose'], 'day': t['day'],
                             'key': t['key'], 'proof': proof})
            for item in a['inputs']:
                ident = item['id']
                if ident in inputs:
                    continue
                row = dict(db.execute('SELECT * FROM artifacts WHERE id=?', (ident,)).fetchone())
                packed = packed_record(db, row['path'])
                blob = read_packed(CP, packed) if packed else safe_file(CP, row['path']).read_bytes()
                assert sha(blob) == ident and len(blob) == row['bytes']
                inputs[ident] = row
    save('noon-data.json', {'verified': verified, 'inputs': inputs})
    maintenance = read(ROOT / 'state/maintenance-daily-latest.json')
    assert maintenance['ok'] and maintenance['backup']['backup_id'] == BID and not maintenance['backup']['reused']
    assert maintenance['backup']['clock_evidence_state'] == 'available' and maintenance['backup']['clock_evidence_error'] is None
    assert maintenance['validation']['ok'] and not maintenance['validation']['failures']
    save('maintenance.json', maintenance)
    bundle = read_bundle(CP, manifest)
    completion = bundle['bundle']['objects'][bundle['bundle']['completion']]
    old = read(HERE.parent / 'scheduled-system-deploy-2026-10-13/t05-preservation.json')
    assert set(old['object_hashes']) < set(bundle['object_ids']) and len(bundle['object_ids']) == 10
    assert completion['order']['baseline'] == old['baseline_id']
    assert completion['order']['predecessors'] == ['b976e8b04f2bfccbf68a2bc988faac2ed95c1e5dc9172a512396fa9f70dc9192']
    assert completion['mode'] == 'after_completion' and completion['clock_error'] is None
    assert completion['observation']['upper_utc_ns'] > int(datetime.fromisoformat(manifest['completed_at']).timestamp() * 1e9)
    assert completion['observation']['bootstrap_sha256'] == sha(Path('/etc/marketdata/clock-bootstrap.json').read_bytes())
    assert completion['observation']['ledger_sha256'] == sha(Path('/var/lib/marketdata-clock/boot-state.json').read_bytes())
    prof = tomllib.loads(Path('/home/yoonhc/.config/marketdata/restic-archive.toml').read_text())
    archive_root = Path(prof['state_root']) / prof['expected_repository_id']
    with (archive_root / 'archive.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        state = read(archive_root / prof['archive_scope'] / 'state.json')
        entry, check = state['checkpoints'][BID], state['repository_check']
        assert entry['stored']['snapshot_id'] == SID
        assert entry['entry']['manifest_sha256'] == sha(manifest_bytes)
        assert entry['stored']['checkpoint_evidence'] == {'object_ids': bundle['object_ids'], 'sha256': bundle['sha256']}
        assert check['ok'] and check['status'] == 'complete' and check['read_data'] and SID in check['snapshot_ids']
        assert check['receipt']['returncode'] == 0 and check['receipt']['arguments'] == ['check', '--read-data']
        save('archive-evidence.json', {'checkpoint_record': entry, 'full_check': check})
        for name in ('stdout', 'stderr'):
            retain('scheduled-full-check.' + name, Path(check['receipt'][name]).read_bytes())
    scheduled = read(ROOT / 'reports/restic-archive-runs/scheduled' / (SCHEDULED + '.json'))
    context = scheduled['context']
    assert scheduled['ok'] and scheduled['archive']['result']['processed'] == 1
    assert context['uid'] == 1000 and context['manager'] == 'system' and context['scheduled'] and context['service_verified']
    assert context['classification'] == 'verified_timer' and context['provenance_method'] == 'current_start_job_activation_details'
    assert context['start_job']['activation_details']['trigger_unit'] == 'marketdata-restic-archive.timer'
    assert context['service_properties']['MainPID'] == str(context['pid'])
    assert context['service_properties']['InvocationID'] == context['environment']['INVOCATION_ID']
    assert scheduled['archive']['run_id'] in check['receipt']['stdout']
    save('scheduled-archive.json', scheduled)
    save('t05-bundle.json', bundle['bundle'])
    summary = {'passed': True, 'observed_at': started, 'backup_id': BID, 'snapshot_id': SID,
               'noon_run_id': NOON_RUN, 'noon_attempts': len(attempts), 'related_tasks': len(selected['tasks']),
               'purpose_counts': dict(Counter(t['purpose'] for t in tasks.values())), 'same_attempt_refreshes': len(covered),
               'canonical_rows_checked': sum(r['rows'] for v in verified for r in v['proof']['revisions']),
               'http_requests_recorded': sum(len(a['requests']) for a in attempts.values()),
               'input_artifacts_checked': len(inputs), 'ledger_evidence': ledger_evidence,
               'manifest_sha256': sha(manifest_bytes), 'manifest_summary': {k:v for k,v in manifest.items() if k != 'files'},
               't05_completion_id': bundle['bundle']['completion'], 't05_completion': completion,
               't05_objects': len(bundle['object_ids']), 'existing_objects_preserved': True,
               'full_check_finished_at': check['finished_at'], 'archive_finished_at': scheduled['finished_at'],
               'local_checks_passed': True, 'explicit_snapshot_bundle_read_pending': True,
               'new_backup': False, 'repository_check_repeated': False, 'live_market_files_read': False}
    save('verification.json', summary)
    print(json.dumps({k:summary[k] for k in ('passed','noon_attempts','related_tasks','canonical_rows_checked','input_artifacts_checked','t05_objects','backup_id','snapshot_id')}, indent=2))


def snapshot_bundle():
    summary = read(HERE / 'verification.json')
    expected = read(HERE / 't05-bundle.json')
    prof = tomllib.loads(Path('/home/yoonhc/.config/marketdata/restic-archive.toml').read_text())
    base = [prof['restic_binary'], '-r', prof['restic_repository'], '--password-file', prof['restic_password_file'], '--no-cache', '--no-lock']
    commands = []
    with (Path(prof['state_root']) / prof['expected_repository_id'] / 'archive.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        for args, name in [(['snapshots', '--json', SID], 'explicit-snapshot.json'),
                           (['dump', SID, '/checkpoint-evidence.json'], 'snapshot-checkpoint-evidence.json')]:
            started = datetime.now(timezone.utc).isoformat()
            result = subprocess.run(base + args, capture_output=True, timeout=45)
            assert result.returncode == 0, result.stderr.decode()
            commands.append({'arguments': args, 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
                             'returncode': result.returncode, 'stderr': result.stderr.decode(), **retain(name, result.stdout)})
    snapshots = read(HERE / 'explicit-snapshot.json')
    assert len(snapshots) == 1 and snapshots[0]['id'] == SID and 'backup_id=' + BID in snapshots[0]['tags']
    raw = (HERE / 'snapshot-checkpoint-evidence.json').read_bytes()
    decoded = decode_bundle(raw, expected['identity'])
    assert decoded['bundle'] == expected and decoded['sha256'] == sha((CP / 'checkpoint-evidence.json').read_bytes())
    # Reconstruct solely from returned bytes; no registry or predecessor path is used here.
    graph = Graph(decoded['bundle']['objects'], decoded['bundle']['scope'])
    proof = graph.proof(expected['identity'])
    assert proof['object_id'] == summary['t05_completion_id']
    ancestors = sorted(graph.ancestors(BID))
    assert len(ancestors) == 8
    save('snapshot-bundle-verification.json', {'passed': True, 'snapshot_id': SID, 'backup_id': BID, 'commands': commands,
        'bytes_equal_checkpoint_sidecar': True, 'object_ids': decoded['object_ids'], 'completion_proof': proof,
        'ancestors': ancestors, 'registry_read_for_reconstruction': False, 'predecessor_checkpoint_read': False,
        'bulk_restore': False, 'new_repository_check': False, 'deletion_authority': False})
    print(json.dumps({'snapshot_bundle_passed': True, 'bytes': len(raw), 'objects': len(decoded['object_ids']), 'ancestors': len(ancestors)}))


if __name__ == '__main__':
    import sys
    snapshot_bundle() if '--snapshot-bundle' in sys.argv else main()
