"""Verify the completed final archive and persistent hold before the Windows handoff."""
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
sys.path.insert(0, str(ROOT))
from scripts.archive_backups import Archive, Profile, checkpoint, overview, repository_lock
from marketdata.backup import open_catalog
from marketdata.checkpoint_locks import checkpoint_locks


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    result = json.loads((HERE / 'final-backup.json').read_text())
    manual = json.loads((HERE / 'final-archive.json').read_text())
    assert result['ok'] and manual['ok'] and not manual['context']['scheduled'] and not manual['counted_dates']
    profile = Profile.load(Path('/home/yoonhc/.config/marketdata/restic-archive.toml'))
    profile.guard()
    report = {'checked_at': datetime.now().astimezone().isoformat(), 'passed': False}
    with repository_lock(profile):
        archive = Archive(profile, HERE / 'final-restic-readback')
        stored = archive.state['checkpoints'][result['backup_id']]
        entry = stored['entry']
        cp = Path(entry['path'])
        with checkpoint_locks([cp], wait=False):
            assert checkpoint(cp, profile) == entry
            manifest = json.loads((cp / 'backup.json').read_text())
            record = next(r for r in manifest['files'] if r['path'] == 'state/catalog.sqlite')
            assert sha(cp / record['path']) == record['sha256']
            archive.restic.identify()
            snapshots = archive.restic.snapshots()
            ids = {s['id'] for s in snapshots}
            snapshot_id = stored['stored']['snapshot_id']
            snapshot = next(s for s in snapshots if s['id'] == snapshot_id)
            archive.match(snapshot, entry)
            check = archive.state['repository_check']
            assert check['ok'] and check['read_data'] and check['status'] == 'complete'
            assert check['receipt']['returncode'] == 0 and check['receipt']['arguments'] == ['check', '--read-data']
            assert set(check['snapshot_ids']) == ids
            assert check['finished_at'] <= manual['archive']['finished_at']
            assert stored['stored']['archive_finished_at'] <= check['started_at']
            updated = json.loads((HERE / 'updated-daily.json').read_text())
            inclusion = []
            with closing(open_catalog(cp / 'state/catalog.sqlite')) as db:
                for row in updated:
                    for revision in row.get('results', []):
                        if revision.get('date') != '2026-10-07':
                            continue
                        found = db.execute('SELECT id,day,row_count FROM revisions WHERE id=?', (revision['revision'],)).fetchone()
                        assert found and found['row_count'] == revision['rows']
                        inclusion.append({'key':row['key'], **dict(found)})
                streams = []
                for name in ('depth','aggtrade'):
                    final = json.loads((HERE / ('final-' + name + '-latest.json')).read_text())
                    row = db.execute('SELECT * FROM event_collections WHERE id=?', (final['id'],)).fetchone()
                    assert row is not None
                    recorded = json.loads(row['report'])
                    assert row['state'] == 'interrupted' and recorded['persisted_messages'] == final['persisted_messages']
                    assert recorded['last_received_at'] == final['last_received_at']
                    streams.append({'dataset':name,'id':final['id'],'state':row['state'],
                                    'persisted_messages':recorded['persisted_messages'],
                                    'last_received_at':recorded['last_received_at']})
            report.update(checkpoint=entry, stored=stored['stored'], snapshot_id=snapshot_id,
                          snapshot_count=len(ids), repository_check=check,
                          updated_revisions_in_checkpoint=inclusion, final_collections_in_checkpoint=streams)
    status = overview(profile)
    assert status['healthy'] and status['pending_count'] == 0
    assert not status['incomplete_folders']
    report['archive_overview'] = status
    before = json.loads((HERE / 'before.json').read_text())
    for path, expected in before['config_hashes'].items():
        assert sha(path) == expected['sha256'], path
    original = json.loads((ROOT / 'reports/baseline/system-runtime-root-preflight-2026-10-07/inputs.json').read_text())
    for group in ('pinned_files','operating_files'):
        assert all(sha(path) == digest for path,digest in original[group].items())
    query = subprocess.check_output(['/usr/bin/systemctl', '--user', 'show', *sorted(before['units']),
                                     '-p','Id','-p','LoadState','-p','UnitFileState','-p','ActiveState','-p','MainPID'],text=True)
    for block in query.strip().split('\n\n'):
        v = dict(line.split('=',1) for line in block.splitlines() if '=' in line)
        assert v['LoadState'] == 'masked' and v['UnitFileState'] == 'masked'
        assert v['ActiveState'] in ('inactive','failed') and v.get('MainPID','0') == '0'
        assert os.readlink(Path('/home/yoonhc/.config/systemd/user')/v['Id']) == '/dev/null'
    report.update(passed=True, all_twenty_persistently_masked=True, source_and_candidate_unchanged=True,
                  clock_and_profile_and_grant_hashes_unchanged=True, new_restore_drill=False)
    (HERE / 'final-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    handoff = {'stage':'ready_for_first_windows','at':datetime.now().astimezone().isoformat(),
               'release_id':'system-bootstrap-20261007-f85eb8321a19c1ee',
               'backup_id':entry['backup_id'],'snapshot_id':snapshot_id,
               'collection_hold_started_at':'2026-10-08T21:40:09+09:00',
               'windows_steps_completed':False,'rtc_changed':False,'chrony_installed':False,
               'next':'User Windows registry/restart/time verification, then Linux return observation',
               'automatic_checkpoint_retirement':'masked; remains held after collector transition'}
    with (HERE/'handoff.json').open('x') as stream:
        json.dump(handoff,stream,ensure_ascii=False,indent=2)
        stream.write('\n');stream.flush();os.fsync(stream.fileno())
    print(json.dumps(handoff,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
