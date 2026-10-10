"""Verify the first operating v2 clock evidence backup and explicit-snapshot restore.

No backup creation, repository check rerun, service control, clock change, deletion,
or archive-state mutation. Reuse the scheduled full-check receipt. The execute
phase extracts the entire snapshot and uses the existing application restore.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
HERE = Path(__file__).resolve().parent
RAW = ROOT / 'reports/t04-operating-clock-restore-2026-10-10'
FOLLOWUP = ROOT / 'reports/baseline/t11-first-child-boundary-2026-10-09/backup-followup.json'
RELEASE = Path('/opt/marketdata/releases/clock-evidence-policy-20261009-65296619a374c325')
PROFILE = Path('/home/yoonhc/.config/marketdata/restic-archive.toml')
DAY = '2026-10-10'
sys.path.insert(0, str(ROOT))

from scripts.archive_backups import Archive, Profile, checkpoint, repository_lock, timestamp
from marketdata import backup
from marketdata.checkpoint_locks import checkpoint_locks
from marketdata.fileio import packed_record, read_packed, safe_file


def utc():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def read(path):
    return json.loads(Path(path).read_text())


def save(name, value):
    HERE.mkdir(parents=True, exist_ok=True)
    path = HERE / name
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + '\n')
    tmp.replace(path)


def observe():
    profile = Profile.load(PROFILE)
    daily = read(ROOT / 'state/maintenance-daily-latest.json')
    state = read(profile.state_file)
    result = {'observed_at': utc(), 'ready': False, 'daily': {
        k: daily.get(k) for k in ('id', 'state', 'phase', 'ok', 'requested_at', 'started_at',
                                  'updated_at', 'finished_at', 'backup')},
        'repository_check': state.get('repository_check')}
    record = None
    if not daily.get('ok') or daily.get('state') != 'complete':
        result['hold'] = 'scheduled_daily_not_complete'
    elif timestamp(daily['requested_at']).date().isoformat() != DAY:
        result['hold'] = 'scheduled_daily_is_not_target_day'
    else:
        bid = daily.get('backup', {}).get('backup_id')
        record = state['checkpoints'].get(bid)
        if not record or not record.get('stored'):
            result['hold'] = 'same_backup_id_not_archived'
        else:
            check = state.get('repository_check') or {}
            sid = record['stored']['snapshot_id']
            result['entry'], result['stored'] = record['entry'], record['stored']
            if not (check.get('ok') and check.get('status') == 'complete'
                    and check.get('read_data') and sid in check.get('snapshot_ids', [])):
                result['hold'] = 'same_snapshot_full_read_check_not_complete'
            else:
                run = Path(check['receipt']['stdout']).parent / 'run.json'
                if not run.is_file() or not read(run).get('ok'):
                    result['hold'] = 'scheduled_archive_run_not_complete'
                else:
                    result['ready'] = True
    save('latest-observation.json', result)
    with (HERE / 'observations.jsonl').open('a') as f:
        f.write(json.dumps({k:v for k,v in result.items() if k != 'repository_check'},
                           ensure_ascii=False) + '\n')
    return profile, daily, state, record, result


def prepare():
    targets = read(FOLLOWUP)['targets']
    checks = {}
    for stream, target in targets.items():
        summary = read(FOLLOWUP.parent / (stream + '-clock-query.json'))
        original = ROOT / summary['cli']['retained_stdout_gzip']['path']
        query = json.loads(gzip.decompress(original.read_bytes()))
        require(hashlib.sha256(canonical(query)).hexdigest() == target['query_semantic_sha256'],
                'Original query semantic hash differs')
        require(query['link'] == target['link'] and query['evidence_state'] == 'available',
                'Original collection link/evidence differs')
        require(len(query['intervals']) == target['intervals'], 'Original interval count differs')
        checks[stream] = {'collection_id': target['collection_id'], 'link': target['link'],
            'query_sha256': target['query_semantic_sha256'], 'intervals': target['intervals'],
            'prefix_chunks': len(target['artifact_chain_prefix']),
            'prefix_bytes': sum(x['bytes'] for x in target['artifact_chain_prefix']),
            'original_query_gzip': str(original), 'original_query_file_sha256': digest(original)}
    files = [Path(__file__), FOLLOWUP, PROFILE,
             RELEASE / '.venv/lib/python3.12/site-packages/marketdata/clock_evidence.py',
             ROOT / 'scripts/archive_backups.py', ROOT / 'packages/marketdata/src/marketdata/backup.py']
    proof = {'prepared_at': utc(), 'passed': True, 'release_id': RELEASE.name, 'targets': checks,
             'inputs': {str(p): digest(p) for p in files},
             'restore_scope': 'full snapshot extraction and existing application restore; no new backup',
             'preserve': ['market partial', 'reference stale history', 'original timestamps',
                          'archive state', 'boot/step ledger', 'automatic retirement mask']}
    save('preparation.json', proof)
    return proof


def clock_query(root, target, label):
    RAW.mkdir(parents=True, exist_ok=True)
    command = [str(RELEASE / '.venv/bin/python'), '-B', '-I', '-m', 'marketdata.cli',
               '--root', str(root), 'clock-evidence', '--collection-id', target['collection_id']]
    started = utc()
    run = subprocess.run(command, capture_output=True, timeout=180)
    blob = RAW / (label + '.json.gz')
    blob.write_bytes(gzip.compress(run.stdout, mtime=0))
    (RAW / (label + '.stderr')).write_bytes(run.stderr)
    require(run.returncode == 0, f'Clock query failed: {label}; see retained output')
    q = json.loads(run.stdout)
    sha = hashlib.sha256(canonical(q)).hexdigest()
    require(sha == target['query_semantic_sha256'], f'Query differs from fixed baseline: {label}')
    require(q['evidence_state'] == 'available' and not q['unknown_reasons'],
            f'Incomplete clock evidence: {label}')
    return {'started_at': started, 'finished_at': utc(), 'command': command, 'exit_code': 0,
            'query_sha256': sha, 'matches_fixed_baseline': True, 'link': q['link'],
            'evidence_state': q['evidence_state'], 'intervals': len(q['intervals']),
            'stale_intervals': sum(x['reference_stale'] is True for x in q['intervals']),
            'unusable_intervals': sum(not x['current_estimate_usable'] for x in q['intervals']),
            'artifact_ids': q['chunks'], 'retained_stdout_gzip': str(blob),
            'retained_stdout_sha256': digest(blob)}


def selected_contents(root, manifest, targets):
    physical = {f['path']: f for f in manifest['files']}
    catalog = physical['state/catalog.sqlite']
    require(digest(root / catalog['path']) == catalog['sha256'], 'Catalog hash differs')
    output = {'catalog_sha256': catalog['sha256'], 'collections': {}}
    with closing(backup.open_catalog(root / catalog['path'])) as db:
        for stream, target in targets.items():
            row = db.execute('SELECT report FROM event_collections WHERE id=?',
                             (target['collection_id'],)).fetchone()
            require(row is not None, 'Collection absent from checkpoint')
            report = json.loads(row[0])
            require(hashlib.sha256(canonical(report)).hexdigest() == target['collection_report_sha256'],
                    'Collection report changed from fixed baseline')
            verified = []
            for a in target['artifact_chain_prefix']:
                raw = db.execute('SELECT path,bytes FROM artifacts WHERE id=?', (a['id'],)).fetchone()
                require(raw is not None and raw[0] == a['path'] and raw[1] == a['bytes'],
                        'Artifact catalog identity differs')
                fetched = db.execute('SELECT artifact_id,metadata FROM fetches WHERE url=?',
                                     (a['url'],)).fetchall()
                require(len(fetched) == 1 and fetched[0][0] == a['id']
                        and json.loads(fetched[0][1]) == a['metadata'], 'Artifact provenance differs')
                pack = packed_record(db, a['path'])
                path = pack['pack_path'] if pack else a['path']
                require(path in physical, 'Artifact physical file absent from manifest')
                blob = read_packed(root, pack) if pack else safe_file(root, a['path']).read_bytes()
                require(hashlib.sha256(blob).hexdigest() == a['id'] and len(blob) == a['bytes'],
                        'Clock artifact bytes differ')
                verified.append({'id':a['id'], 'path':a['path'], 'physical_path':path,
                                 'bytes':len(blob), 'sha256':hashlib.sha256(blob).hexdigest()})
            output['collections'][stream] = {'collection_id': target['collection_id'],
                'collection_report_sha256': target['collection_report_sha256'],
                'terminal_state': report['runtime_clock']['terminal_state'], 'artifacts': verified}
    return output


def execute():
    preparation = read(HERE / 'preparation.json')
    require(all(digest(p) == h for p,h in preparation['inputs'].items()), 'Prepared input changed')
    profile, daily, state, record, observation = observe()
    require(observation['ready'], observation.get('hold', 'Not ready'))
    targets = read(FOLLOWUP)['targets']
    token = uuid.uuid4().hex
    work = profile.restore_work_root / ('t04-clock-evidence-20261010-' + token)
    reports = HERE / 'commands' / token
    result = {'started_at':utc(), 'passed':False, 'work_root':str(work),
              'scope':'full snapshot extraction, application restore, two fixed collection queries',
              'new_repository_check':False, 'new_backup':False, 'services_started':False}
    save('execution.json', result)
    try:
        with repository_lock(profile):
            before = digest(profile.state_file)
            archive = Archive(profile, reports)
            entry, stored = record['entry'], record['stored']
            sid, cp = stored['snapshot_id'], Path(entry['path'])
            require(archive.state['checkpoints'][entry['backup_id']] == record, 'Archive state advanced')
            check = archive.state['repository_check']
            require(check['ok'] and check['read_data'] and check['status'] == 'complete'
                    and sid in check['snapshot_ids'], 'Full-check coverage differs')
            require(check['receipt']['arguments'] == ['check','--read-data']
                    and check['receipt']['returncode'] == 0, 'Not a successful full data check')
            require(timestamp(stored['archive_finished_at']) <= timestamp(check['started_at']),
                    'Full check predates archive completion')
            receipt_path = Path(check['receipt']['stdout']).with_suffix('.json')
            require(read(receipt_path) == check['receipt'], 'Full-check receipt differs')
            scheduled = read(receipt_path.parent / 'run.json')
            require(scheduled['ok'] and scheduled['result']['processed'] > 0
                    and sid in scheduled['result']['stored_snapshots'], 'No scheduled new archive proof')
            inputs = []
            for p in receipt_path.parent.glob('*-verify-input.json'):
                v = read(p)
                if v.get('result',{}).get('backup_id') == entry['backup_id']:
                    require(v['result']['ok'], 'Archive input verification failed')
                    inputs.append({'path':str(p), 'sha256':digest(p), 'proof':v})
            require(len(inputs) == 1, 'Input verification proof missing or ambiguous')
            save('scheduled-daily.json', daily)
            save('scheduled-archive.json', scheduled)
            save('scheduled-full-check.json', {'check':check, 'input_verification':inputs,
                'evidence':[{'path':str(p), 'sha256':digest(p)} for p in
                            (receipt_path, Path(check['receipt']['stdout']), Path(check['receipt']['stderr']))],
                'stdout':Path(check['receipt']['stdout']).read_text(),
                'stderr':Path(check['receipt']['stderr']).read_text()})
            result.update(backup=entry, stored=stored, restic_version=archive.restic.identify())
            snapshots = archive.restic.snapshots()
            current_ids = {s['id'] for s in snapshots}
            recorded_ids = {r['stored']['snapshot_id'] for r in archive.state['checkpoints'].values() if r['stored']}
            require(current_ids == recorded_ids == set(check['snapshot_ids']), 'Snapshot inventory differs')
            matches = [s for s in snapshots if f"backup_id={entry['backup_id']}" in s.get('tags',[])]
            require(len(matches) == 1 and matches[0]['id'] == sid, 'Duplicate/wrong target snapshot')
            result['matched_attempt_id'] = archive.match(matches[0], entry)
            save('snapshot-inventory.json', {'selected':matches[0], 'all_snapshot_ids':sorted(current_ids)})
            with checkpoint_locks([cp], wait=False):
                require(checkpoint(cp,profile) == entry, 'Checkpoint changed')
                manifest = read(cp / 'backup.json')
                require(daily['backup']['backup_id'] == entry['backup_id']
                        and daily['backup']['ok'] and daily['validation']['ok']
                        and not daily['validation']['failures'], 'Daily validation/backup differs')
                save('checkpoint-contents.json', selected_contents(cp, manifest, targets))
                result['checkpoint_queries'] = {s:clock_query(cp,t,s+'-checkpoint') for s,t in targets.items()}
            profile.guard(reserve=2*entry['bytes'])
            require(not work.exists(), 'Restore destination already exists')
            work.mkdir(parents=True)
            extracted, restored = work / 'extracted', work / 'store'
            result.update(phase='restic_restore', snapshot_id=sid, extracted=str(extracted), restored=str(restored))
            save('execution.json', result)
            print(json.dumps({'phase':'restic_restore','snapshot_id':sid,'bytes':entry['bytes']}),flush=True)
            _, receipt = archive.restic.command(['restore',sid,'--target',str(extracted),'--verify'])
            require(digest(extracted / 'backup.json') == entry['manifest_sha256'], 'Extracted manifest differs')
            result['restic_restore_receipt'] = receipt
            after = archive.restic.snapshots()
            require({s['id'] for s in after} == current_ids, 'Snapshot inventory changed during extraction')
            require(digest(profile.state_file) == before, 'Archive state mutated during verification')
            result['archive_state_sha256_before_after'] = before
            result['snapshot_inventory_unchanged'] = True
        result['phase'] = 'application_restore'
        save('execution.json', result)
        print(json.dumps({'phase':'application_restore'}),flush=True)
        receipt = backup.restore_backup(extracted, restored, min_free_gib=profile.min_free_gib,
                                        progress=lambda r:print(json.dumps(r),flush=True))
        save('application-restore.json',receipt)
        require(receipt['ok'] and receipt['backup_id'] == entry['backup_id'], 'Application restore failed')
        save('restored-contents.json', selected_contents(restored, manifest, targets))
        result['restored_queries'] = {s:clock_query(restored,t,s+'-restored') for s,t in targets.items()}
        physical = {'state/catalog.sqlite'}
        for s in read(HERE / 'restored-contents.json')['collections'].values():
            physical.update(a['physical_path'] for a in s['artifacts'])
        for path in physical:
            stats = [(root/path).stat() for root in (extracted,restored)]
            require((stats[0].st_dev,stats[0].st_ino) != (stats[1].st_dev,stats[1].st_ino),
                    'Selected restored file shares extracted inode')
        result.update(passed=True, phase='complete', independent_selected_files=len(physical),
                      application_restore_receipt=str(HERE/'application-restore.json'))
    except Exception as exc:
        result.update(error=repr(exc),traceback=traceback.format_exc())
        raise
    finally:
        result['finished_at'] = utc()
        save('execution.json', result)
    return result


def await_execute():
    """One foreground verification run; no installed timer or service."""
    deadline = time.monotonic() + 3 * 3600
    previous = None
    announced = 0.0
    while time.monotonic() < deadline:
        _, daily, state, _, observation = observe()
        summary = {'observed_at': observation['observed_at'], 'ready': observation['ready'],
                   'hold': observation.get('hold'), 'daily_state': daily.get('state'),
                   'daily_phase': daily.get('phase'),
                   'archive_phase': (state.get('last_phase_started') or {}).get('name')}
        identity = {k:v for k,v in summary.items() if k != 'observed_at'}
        if identity != previous or time.monotonic() - announced >= 300:
            print(json.dumps(summary), flush=True)
            previous, announced = identity, time.monotonic()
        require(daily.get('state') not in ('failed','partial','interrupted'),
                'Scheduled checkpoint did not complete successfully; inspect without retry')
        require((state.get('repository_check') or {}).get('status') != 'failed',
                'Scheduled repository check failed; inspect without retry')
        if observation['ready']:
            return execute()
        time.sleep(30)
    raise RuntimeError('Scheduled evidence is still pending after the bounded observation window')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('phase',choices=['prepare','observe','execute','await-execute'])
    phase=parser.parse_args().phase
    if phase == 'prepare':
        result=prepare()
        print(json.dumps({'prepared':result['passed'],'targets':list(result['targets'])}))
    elif phase == 'observe':
        result=observe()[-1]
        print(json.dumps({k:result.get(k) for k in ('observed_at','ready','hold','daily','entry','stored')},ensure_ascii=False))
    else:
        result=await_execute() if phase == 'await-execute' else execute()
        print(json.dumps({k:result.get(k) for k in ('passed','phase','backup','snapshot_id','work_root')},ensure_ascii=False))
