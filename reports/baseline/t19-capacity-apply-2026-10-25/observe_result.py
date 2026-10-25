"""Small post-application audit. No payload reread or operating mutation."""
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    operation_raw = (HERE / 'operation.json').read_bytes()
    envelope = json.loads(operation_raw)
    oid, operation = envelope['operation_id'], envelope['operation']
    assert digest(operation_raw) == '98f9cb59adc39581bfa3d3d6c32379e686b4580b270574a21c4c56c1d141b3e4'
    complete_raw = (HERE / 'audit/complete.json').read_bytes()
    complete = json.loads(complete_raw)
    attempt = json.loads((HERE / 'audit' / (complete['attempt_id'] + '.json')).read_bytes())
    assert complete['operation_id'] == oid and complete['passed']
    assert attempt['state'] == 'complete' and attempt['passed']
    assert complete['verified_targets'] == complete['replaced_this_attempt'] == 1022
    selection_raw = Path(operation['selection_path']).read_bytes()
    assert digest(selection_raw) == operation['selection_sha256']
    plan = json.loads(selection_raw)['proposal']
    authority_raw = (HERE / 'audit/authority.json').read_bytes()
    assert digest(authority_raw) == complete['authority_sha256']
    events_path = HERE / 'audit' / (complete['attempt_id'] + '.jsonl')
    events_raw = events_path.read_bytes()
    events = [json.loads(line) for line in events_raw.splitlines()]
    expected = {i['path'] for g in plan['groups'] for i in g['targets']}
    assert len(expected) == 1022
    pending = None
    for event in events:
        if event['phase'] == 'before_replace':
            assert pending is None
            pending = event['path']
        else:
            assert event['phase'] == 'after_replace' and event['path'] == pending
            pending = None
    assert pending is None
    for phase in ('before_replace', 'after_replace'):
        assert Counter(e['path'] for e in events if e['phase'] == phase) == Counter(dict.fromkeys(expected, 1))
    original_allocated = 0
    actual_unique = {}
    groups = []
    for group in plan['groups']:
        source = group['source']
        si = Path(source['path']).stat()
        assert (si.st_dev, si.st_ino) == (source['dev'], source['ino'])
        assert si.st_nlink == len(group['targets']) + 1 == 512
        paths = [source, *group['targets']]
        for item in paths:
            p = Path(item['path'])
            assert not p.is_symlink()
            s = p.stat()
            assert stat.S_ISREG(s.st_mode)
            assert (s.st_dev, s.st_ino) == (si.st_dev, si.st_ino)
            assert (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode), s.st_size) == (
                item['uid'], item['gid'], item['mode'], item['bytes'])
            assert {k: os.getxattr(p, k).hex() for k in os.listxattr(p)} == item['xattrs']
            assert s.st_mtime_ns == source['mtime_ns']
            temp = p.with_name('.' + p.name + '.t18-' + oid[:20] + '.link')
            assert not os.path.lexists(temp)
            original_allocated += item['allocated_bytes']
            actual_unique[(s.st_dev, s.st_ino)] = s.st_blocks * 512
        groups.append({'snapshot_id': group['snapshot_id'], 'backup_id': group['backup_id'],
                       'manifest_sha256': group['sha256'], 'paths': len(paths),
                       'retained_source_inode': si.st_ino, 'links': si.st_nlink,
                       'unique_allocated_bytes': si.st_blocks * 512})
    actual_allocated = sum(actual_unique.values())
    assert original_allocated - actual_allocated == operation['expected_reclaim_bytes']
    with Path(operation['archive_lock']).open('rb') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            archive_lock_free = False  # A later natural task may legitimately hold it.
        else:
            archive_lock_free = True
            fcntl.flock(lock, fcntl.LOCK_UN)
    v = os.statvfs(PROJECT)
    current_free = v.f_bavail * v.f_frsize
    result = {
        'at': datetime.now(timezone.utc).isoformat(), 'passed': True, 'operation_id': oid,
        'attempt_id': complete['attempt_id'], 'authority_sha256': digest(authority_raw),
        'completion_sha256': digest(complete_raw), 'journal_sha256': digest(events_raw),
        'journal_before_events': 1022, 'journal_after_events': 1022,
        'paths_preserved': 1024, 'replaced_paths': 1022, 'retained_inodes': len(actual_unique),
        'groups': groups, 'temporary_links_present': 0,
        'selected_set_original_allocated_bytes': original_allocated,
        'selected_set_current_unique_allocated_bytes': actual_allocated,
        'selected_set_allocation_reduction_bytes': original_allocated - actual_allocated,
        'run_free_before_bytes': complete['available_before_bytes'],
        'run_free_after_bytes': complete['available_after_bytes'],
        'run_observed_free_delta_bytes': complete['available_after_bytes'] - complete['available_before_bytes'],
        'current_available_bytes': current_free, 'capacity_includes_concurrent_work': True,
        'archive_lock_free_at_probe': archive_lock_free,
        'payload_reread_bytes': 0, 'operating_writes': 0,
        'receipt_content_validation': {'before': complete['receipts_verified'],
                                       'after': complete['receipts_verified_after'],
                                       'source': 'completed application; not repeated by this observer'},
        'collector_state': {},
    }
    for stream in ('depth', 'aggtrade'):
        c = json.loads((PROJECT / 'state/runtime-clock/v2' / stream / 'latest.json').read_bytes())
        result['collector_state'][stream] = {k: c.get(k) for k in
            ('state', 'child_pid', 'supervisor_pid', 'systemd_invocation_id', 'clock_usable', 'decision')}
    with (HERE / 'post-application-audit.json').open('xb') as f:
        f.write(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2).encode() + b'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
