"""Read-only T13 schedule, stored-result and checkpoint review.

No Store/Scheduler creation, plan(), HTTP collection, backup, restore or service
control. Only evidence under this report directory is written.
"""
import argparse
import fcntl
import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import tomllib
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TABLES = ('daily_tasks', 'daily_runs', 'daily_attempts', 'daily_cooldowns', 'daily_reviews', 'daily_probe_targets')


def save(name, value):
    with (HERE/name).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.write('\n')


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def connection(path, immutable=False):
    db = sqlite3.connect(path.as_uri()+'?mode=ro'+('&immutable=1' if immutable else ''), uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def ledger(db):
    return {t: [json.loads(r[0]) for r in db.execute('SELECT record FROM '+t)] for t in TABLES}


def key_id(key):
    from marketdata.domain import Key
    return Key(**key).id


def verify_proof(root, db, proof, key, expected_day=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from marketdata.fileio import packed_record, read_packed, safe_file

    def read(path):
        record = packed_record(db, path)
        return (read_packed(root, record) if record else safe_file(root, path).read_bytes(),
                record['pack_path'] if record else path)

    verified = {'queries': [], 'revisions': [], 'artifacts': []}
    assert proof and proof['query_ids'] and proof['source_ids']
    for identity in proof['query_ids']:
        q = dict(db.execute('SELECT * FROM queries WHERE id=?', (identity,)).fetchone())
        assert q['key_id'] == key_id(key) and q['source_id'] in proof['source_ids']
        verified['queries'].append(q)
    for identity, digest in zip(proof['revision_ids'], proof['file_sha256'], strict=True):
        r = dict(db.execute('SELECT * FROM revisions WHERE id=?', (identity,)).fetchone())
        assert r['key_id'] == key_id(key) and r['file_sha'] == digest
        if expected_day is not None:
            assert r['day'] == expected_day
        blob, physical = read(r['path'])
        assert sha(blob) == digest
        table = pq.ParquetFile(pa.BufferReader(blob)).read()
        assert table.num_rows == r['row_count']
        quality = json.loads(r['quality'])
        times = table['timestamp'].to_pylist() if 'timestamp' in table.column_names else []
        grid = None
        if key['dataset'] in ('trade_bars', 'mark_price_bars', 'index_price_bars', 'open_interest'):
            step = 300 if key['dataset'] == 'open_interest' else 60
            start = datetime.fromisoformat(r['day']).replace(tzinfo=timezone.utc)
            assert times == [start + timedelta(seconds=n*step) for n in range(86400//step)]
            grid = True
        issues = [dict(x) for x in db.execute('SELECT * FROM issues WHERE key_id=? AND day=?', (r['key_id'], r['day']))]
        assert not any(x['blocking'] and not x['resolved'] for x in issues)
        verified['revisions'].append({'id':identity, 'day':r['day'], 'sha256':digest, 'path':r['path'],
                                     'physical_path':physical, 'rows':table.num_rows, 'quality':quality,
                                     'first_timestamp':times[0] if times else None,
                                     'last_timestamp':times[-1] if times else None,
                                     'exact_grid':grid, 'issues':issues})
    for identity in proof['source_ids']:
        r = dict(db.execute('SELECT * FROM artifacts WHERE id=?', (identity,)).fetchone())
        blob, physical = read(r['path'])
        assert sha(blob) == identity and len(blob) == r['bytes']
        verified['artifacts'].append({**r, 'physical_path':physical, 'verified_sha256':identity})
    return verified


def inspect():
    daily = json.loads((ROOT/'state/maintenance-daily-latest.json').read_text())
    cp = Path(daily['backup']['path'])
    result = {'as_of':datetime.now(timezone.utc).isoformat(), 'read_only':True}
    with ExitStack() as stack:
        fd = os.open(cp.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        lock = stack.enter_context((ROOT/'state/file-layout.lock').open('r'))
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        db = connection(ROOT/'state/catalog.sqlite')
        stack.callback(db.close)
        live = ledger(db)
        tasks = {t['id']:t for t in live['daily_tasks']}
        runs = sorted(live['daily_runs'], key=lambda r:r['started_at'])
        attempts = sorted(live['daily_attempts'], key=lambda a:a['started_at'])
        assert all(r['runner_ok'] for r in runs)
        assert all(a['state']=='complete' for a in attempts)
        actual = []
        for a in attempts:
            t = tasks[a['task_id']]
            assert a['started_at'] >= t['first_eligible_at'] and len(t['attempts']) == 1
            assert t['proof'] == a['proof']
            actual.append({'attempt_id':a['id'], 'task_id':t['id'], 'key':t['key'], 'day':t['day'],
                           'purpose':t['purpose'], 'started_at':a['started_at'], 'finished_at':a['finished_at'],
                           'requests':len(a['requests']), 'http_statuses':dict(Counter(str(x.get('status')) for x in a['requests'])),
                           'verified':verify_proof(ROOT, db, a['proof'], t['key'], None if t['purpose']=='observe' else t['day'])})
        before = max((r for r in runs if r['started_at'] < '2026-10-11T00:00:00'), key=lambda r:r['started_at'])
        after = min((r for r in runs if r['started_at'] >= '2026-10-11T00:00:00'), key=lambda r:r['started_at'])
        old_ids = {t['id'] for t in before['pending']}
        new_ids = {t['id'] for t in after['pending']}
        assert old_ids <= new_ids and not after['processed']
        result['date_boundary'] = {'before':before, 'after':after, 'previous_pending_retained':True,
                                   'added_tasks':[t for t in after['pending'] if t['id'] not in old_ids]}
        result['operating'] = {'counts':{t:len(v) for t,v in live.items()},
                               'states':dict(Counter(t['state'] for t in tasks.values())),
                               'run_outcomes':dict(Counter(r['run_outcome'] for r in runs)),
                               'processed_runs':[r for r in runs if r.get('processed')],
                               'verified_attempts':actual,
                               'request_count':sum(a['requests'] for a in actual),
                               'completed_via_same_attempt':[t['id'] for t in tasks.values() if t.get('covered_by')]}
        manifest_bytes = (cp/'backup.json').read_bytes()
        manifest = json.loads(manifest_bytes)
        cdb = connection(cp/'state/catalog.sqlite', immutable=True)
        stack.callback(cdb.close)
        archived = ledger(cdb)
        c_tasks = {t['id']:t for t in archived['daily_tasks']}
        cp_attempts = {a['id']:a for a in archived['daily_attempts']}
        verified_cp = []
        for a in attempts:
            if a['id'] not in cp_attempts:
                continue
            assert a == cp_attempts[a['id']]
            t = c_tasks[a['task_id']]
            assert t == tasks[t['id']]
            verified_cp.append({'attempt_id':a['id'], 'task_id':t['id'],
                                'verified':verify_proof(cp, cdb, a['proof'], t['key'], None if t['purpose']=='observe' else t['day'])})
        previous = json.loads((HERE.parent/'t13-daily-rerun-2026-10-10/verification.json').read_text())
        older = []
        for target in previous['resolved']:
            r, a = target['revision'], target['source']
            qids = [q[0] for q in cdb.execute('SELECT id FROM queries WHERE key_id=? AND source_id=?', (r['key_id'],a['id']))]
            proof = {'query_ids':qids, 'revision_ids':[r['id']], 'file_sha256':[r['file_sha']], 'source_ids':[a['id']]}
            older.append({'key':r['key_id'], 'verified':verify_proof(cp,cdb,proof,json.loads(r['key_json']),'2026-10-09')})
        evening_ids = {a['id'] for a in attempts if a['started_at'] >= '2026-10-11T09:00:00'}
        assert evening_ids.isdisjoint(cp_attempts)
        result['checkpoint'] = {'path':str(cp), 'backup_id':manifest['backup_id'], 'manifest_sha256':sha(manifest_bytes),
                                'checkpoint_started_at':manifest['checkpoint_started_at'],
                                'checkpoint_finished_at':manifest['checkpoint_finished_at'],
                                'completed_at':manifest['completed_at'], 'maintenance_finished_at':daily['finished_at'],
                                'ledger_counts':{t:len(v) for t,v in archived.items()},
                                'states':dict(Counter(t['state'] for t in c_tasks.values())),
                                'verified_attempts':verified_cp, 'previous_oct9_backfill':older,
                                'evening_attempts_absent':len(evening_ids)}
        packed = gzip.compress(json.dumps({'live':live,'checkpoint':archived},ensure_ascii=False).encode(),mtime=0)
        with (HERE/'ledger-records.json.gz').open('xb') as f:
            f.write(packed)
        result['ledger_records_sha256'] = sha(packed)
    result['passed'] = True
    save('inspection.json',result)
    print(json.dumps({'passed':True,'counts':result['operating']['counts'],'states':result['operating']['states'],
                      'requests':result['operating']['request_count'],'verified_attempts':len(actual),
                      'checkpoint_counts':result['checkpoint']['ledger_counts'],
                      'checkpoint_verified_attempts':len(result['checkpoint']['verified_attempts']),
                      'previous_backfill_verified':len(older),'evening_attempts_absent':len(evening_ids)},indent=2))


def host():
    props = 'Id,ActiveState,SubState,Result,MainPID,ControlPID,InvocationID,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp,UnitFileState,ExecStart,ExecStartPre,LastTriggerUSec,NextElapseUSecRealtime'
    commands = {
        'daily': ['systemctl','--user','show','marketdata-daily.service','marketdata-daily.timer','marketdata-clock-ready.service','--property='+props],
        'jobs': ['systemctl','--user','list-jobs','--no-pager'],
        'protected_services': ['systemctl','show','chrony.service','marketdata-collector@depth.service','marketdata-collector@aggtrade.service','--property=Id,ActiveState,MainPID,InvocationID'],
        'journal': ['journalctl','--user','-u','marketdata-daily.service','-u','marketdata-clock-ready.service','--since','2026-10-11 00:50:00','--no-pager','-o','json'],
    }
    value = {'as_of':datetime.now(timezone.utc).isoformat(),'read_only':True,'commands':{}}
    for name, args in commands.items():
        p = subprocess.run(args,capture_output=True,text=True,timeout=40)
        assert p.returncode==0,(name,p.stderr)
        if name=='journal':
            data=gzip.compress(p.stdout.encode(),mtime=0)
            with (HERE/'journal.jsonl.gz').open('xb') as f:f.write(data)
            value['commands'][name]={'argv':args,'code':0,'sha256':sha(data),'lines':len(p.stdout.splitlines())}
        else:
            value['commands'][name]={'argv':args,'code':0,'stdout':p.stdout}
    profile=tomllib.loads(Path('/home/yoonhc/.config/marketdata/restic-archive.toml').read_text())
    with (Path(profile['state_root'])/profile['expected_repository_id']/'archive.lock').open('r') as lock:
        fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        state=json.loads((Path(profile['state_root'])/profile['expected_repository_id']/profile['archive_scope']/'state.json').read_text())
        inspection=json.loads((HERE/'inspection.json').read_text())
        record=state['checkpoints'][inspection['checkpoint']['backup_id']]
        sid=record['stored']['snapshot_id']
        check=state['repository_check']
        assert check['ok'] and check['read_data'] and check['status']=='complete' and sid in check['snapshot_ids']
        args=[profile['restic_binary'],'-r',profile['restic_repository'],'--password-file',profile['restic_password_file'],
              '--no-cache','--no-lock','--json','snapshots',sid]
        p=subprocess.run(args,capture_output=True,text=True,timeout=40,check=True)
        snapshots=json.loads(p.stdout)
        assert len(snapshots)==1 and snapshots[0]['id']==sid
        assert 'backup_id='+inspection['checkpoint']['backup_id'] in snapshots[0]['tags']
        value['archive']={'checkpoint_record':record,'selected_snapshot':snapshots[0],'full_check':check,
                          'new_check_or_restore_requested':False}
    value['retirement_masks']={str(folder/name):os.readlink(folder/name) for folder in
        (Path('/etc/systemd/system'),Path('/home/yoonhc/.config/systemd/user'))
        for name in ('marketdata-checkpoint-retirement.service','marketdata-checkpoint-retirement.timer')}
    assert set(value['retirement_masks'].values())=={'/dev/null'}
    value['files_sha256']={str(p):sha(p.read_bytes()) for p in (
        Path('/etc/marketdata/daily-scheduler.toml'),Path('/etc/marketdata/daily-publication-probe.toml'),
        Path('/home/yoonhc/.config/systemd/user/marketdata-daily.service'),Path('/home/yoonhc/.config/systemd/user/marketdata-daily.timer'),
        Path('/etc/systemd/user/marketdata-daily.service.d/90-marketdata-clock.conf'))}
    save('host.json',value)
    print(json.dumps({'saved':True,'as_of':value['as_of'],'snapshot_id':sid,'full_check_completed':check['finished_at'],
                      'daily':value['commands']['daily']['stdout'],'jobs':value['commands']['jobs']['stdout']},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('inspect','host'))
    {'inspect':inspect,'host':host}[p.parse_args().action]()
