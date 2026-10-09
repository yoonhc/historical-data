"""One approved, exact-inventory manual retirement. No recursive wildcard deletion.

An unfinished attempt is never silently retried. Its durable batch intents and
completions must first be reconciled against the unchanged approved inventory.
"""

from datetime import datetime, timezone
import gzip
import hashlib
import itertools
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
BASE = Path(__file__).resolve().parent
REVIEW = BASE.parent / 'trial-artifact-retention-refresh-2026-10-09'
PLAN_ID = '63a2be1e2f10e567ac6457cc4878f753f52691fe2219349c6f1e4e0c0b30c995'
AUDIT = ROOT / 'reports/trial-artifact-retirement-2026-10-09'
EXPECTED = (
    '/mnt/backup/marketdata-restic-pilot',
    '/mnt/backup/marketdata-restic-pilot-work',
    '/mnt/backup/marketdata-independent-recovery-20261003',
    '/mnt/backup/marketdata-restic-restore/0cc942c867b34ba1b561fc4f7e6d1dca',
)
FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
SIGNATURE = ('dev', 'ino', 'mode', 'uid', 'gid', 'size', 'mtime_ns', 'ctime_ns', 'nlink', 'blocks')


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def signature(info):
    return {key: getattr(info, 'st_' + key) for key in SIGNATURE}


def syncdir(path):
    fd = os.open(path, FLAGS)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def new_json(path, value):
    with path.open('xb') as f:
        f.write(encode(value) + b'\n')
        f.flush()
        os.fsync(f.fileno())
    syncdir(path.parent)


def command(args):
    return subprocess.run([str(x) for x in args], capture_output=True, text=True,
                          check=True, timeout=120).stdout


def plain(path):
    require(path.is_absolute() and '..' not in path.parts, f'Unsafe root: {path}')
    require(not any(p.is_symlink() for p in (path, *path.parents)), f'Symlink: {path}')


def records(path):
    with gzip.open(path, 'rt') as f:
        for line in f:
            row = json.loads(line)
            p = PurePosixPath(row['path'])
            require(not p.is_absolute() and '..' not in p.parts, 'Unsafe inventory path')
            require(row['path'] == p.as_posix(), 'Noncanonical inventory path')
            require(stat.S_ISREG(row['mode']) or stat.S_ISDIR(row['mode']), 'Unexpected type')
            yield row


def observed(root):
    plain(root)
    yield {'path': '.', **signature(root.stat())}
    def failed(exc):
        raise exc
    for current, dirs, names, fd in os.fwalk(root, follow_symlinks=False, onerror=failed):
        dirs.sort()
        names.sort()
        for name in dirs + names:
            row = {'path': (Path(current)/name).relative_to(root).as_posix(),
                   **signature(os.stat(name, dir_fd=fd, follow_symlinks=False))}
            require(stat.S_ISDIR(row['mode']) or stat.S_ISREG(row['mode']), 'Unexpected type')
            yield row


def check_tree(root, inventory):
    count = 0
    for actual, expected in itertools.zip_longest(observed(root), records(inventory)):
        require(actual == expected, f'Inventory changed in {root}: {(actual or expected)["path"]}')
        if stat.S_ISREG(actual['mode']):
            require(actual['nlink'] == 1, f'Hardlink appeared: {root / actual["path"]}')
        count += 1
    return count


class Journal:
    def __init__(self, path):
        self.file = path.open('xb')
        self.sequence = 0
        self.files = self.directories = 0

    def emit(self, kind, **data):
        self.file.write(encode({'at': now(), 'kind': kind, **data}) + b'\n')
        self.file.flush()
        os.fsync(self.file.fileno())

    def close(self):
        self.file.close()


class Access:
    def __init__(self, root, expected):
        self.root = root
        self.expected = expected
        self.fd = os.open(root, FLAGS)
        require(signature(os.fstat(self.fd)) == expected, f'Root changed: {root}')
        self.outer = os.open(root.parent, FLAGS)
        self.parents = {}

    def parent(self, relative):
        if relative == '.':
            return self.outer, self.root.name
        path = PurePosixPath(relative)
        name = path.name
        parent = path.parent.as_posix()
        if parent == '.':
            return self.fd, name
        if parent not in self.parents:
            fd = os.dup(self.fd)
            try:
                for part in path.parent.parts:
                    new = os.open(part, FLAGS, dir_fd=fd)
                    os.close(fd)
                    fd = new
                self.parents[parent] = fd
            except BaseException:
                os.close(fd)
                raise
        return self.parents[parent], name

    def flush(self):
        for fd in (self.fd, self.outer, *self.parents.values()):
            os.fsync(fd)
        for fd in self.parents.values():
            os.close(fd)
        self.parents.clear()

    def close(self):
        for fd in (*self.parents.values(), self.fd, self.outer):
            os.close(fd)
        self.parents.clear()


def delete_tree(root, inventory, expected, journal, batch_size=128):
    """Check each inode just before unlink; rmdir refuses any unlisted child."""
    access = Access(root, expected)
    directory_rows = []

    def operations():
        for row in records(inventory):
            if stat.S_ISDIR(row['mode']):
                directory_rows.append(row)
            else:
                yield row
        # Every descendant is recorded after its ancestor in the approved fwalk.
        yield from reversed(directory_rows)

    try:
        source = iter(operations())
        while batch := list(itertools.islice(source, batch_size)):
            index = journal.sequence
            journal.sequence += 1
            journal.emit('batch_intent', index=index, root=str(root), count=len(batch),
                         actions_sha256=hashlib.sha256(encode(batch)).hexdigest())
            for row in batch:
                fd, name = access.parent(row['path'])
                fresh = signature(os.stat(name, dir_fd=fd, follow_symlinks=False))
                is_directory = stat.S_ISDIR(row['mode'])
                keys = ('dev', 'ino', 'mode', 'uid', 'gid') if is_directory else SIGNATURE
                require(all(fresh[k] == row[k] for k in keys), f'Changed before delete: {root / row["path"]}')
                if is_directory:
                    os.rmdir(name, dir_fd=fd)
                    journal.directories += 1
                else:
                    require(fresh['nlink'] == 1, 'File gained an external hardlink')
                    os.unlink(name, dir_fd=fd)
                    journal.files += 1
            access.flush()
            journal.emit('batch_complete', index=index, root=str(root), count=len(batch),
                         files_deleted=journal.files, directories_deleted=journal.directories)
        require(not root.exists() and not root.is_symlink(), f'Root remains: {root}')
    finally:
        access.close()


def usage(roots):
    matches, denied, examined = [], [], 0
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        uid, comm = None, None
        try:
            uid = proc.stat().st_uid
            comm = (proc/'comm').read_text().strip()
            args = (proc/'cmdline').read_bytes().split(b'\0')
            argv_matches = [r for r in roots if any(a.decode(errors='replace') == r or
                            a.decode(errors='replace').startswith(r+'/') for a in args)]
            if argv_matches:
                matches.append({'pid': int(proc.name), 'comm': comm, 'via': 'argv', 'roots': argv_matches})
            if uid != os.getuid():
                continue
            examined += 1
            links = [proc/'cwd', *list((proc/'fd').iterdir())]
            targets = [os.readlink(p).removesuffix(' (deleted)') for p in links]
            targets += [line.split(maxsplit=5)[5].removesuffix(' (deleted)')
                        for line in (proc/'maps').read_text().splitlines() if len(line.split(maxsplit=5)) == 6]
            for target in targets:
                if any(target == p or target.startswith(p+'/') for p in roots):
                    matches.append({'pid': int(proc.name), 'comm': comm, 'via': 'cwd/fd/maps', 'path': target})
        except PermissionError:
            denied.append({'pid': int(proc.name), 'uid': uid, 'comm': comm})
        except (FileNotFoundError, ProcessLookupError):
            continue
    # These were individually identified before application; no trial/data reader.
    unknown = [x for x in denied if x['uid'] == os.getuid() and
               (x['pid'], x['comm']) not in {(1201, 'systemd'), (1216, '(sd-pam)'), (207240, 'sd_espeak-ng-mb')}]
    require(not matches and not unknown, f'Usage uncertainty: {matches}, {unknown}')
    return {'at': now(), 'same_uid_examined': examined, 'matches': matches,
            'permission_denied': denied, 'scope': 'Visible argv for all UIDs; cwd/fd/maps for current UID. Cooperative locks and inactive profile additionally required.'}


def services():
    return command(['systemctl', 'show', 'marketdata-collector@depth.service',
                    'marketdata-collector@aggtrade.service', 'chrony.service',
                    '-p', 'Id', '-p', 'MainPID', '-p', 'ActiveState', '-p', 'InvocationID'])


def protected(plan, profile_removed=False):
    probe = json.loads((REVIEW/'probe.json').read_bytes())
    host = json.loads((REVIEW/'host-addendum.json').read_bytes())
    profile = Path(plan['profile_retirement']['source'])
    for group in (probe['configuration_sha256'], host['configuration_hashes']):
        for p, expected in group.items():
            if Path(p) == profile and profile_removed:
                require(not profile.exists(), 'Retired profile reappeared')
            else:
                require(sha(Path(p)) == expected, f'Protected configuration changed: {p}')
    for name in host['automatic_retirement_links']:
        require(str((Path.home()/'.config/systemd/user'/name).readlink()) == '/dev/null', 'Automatic retirement unmasked')
    for item in plan['protected']:
        require(Path(item['path']).exists(), f'Protected path missing: {item["path"]}')
    for item in plan['preserved_proofs']['historical_proofs']:
        require(sha(ROOT/item['evidence']) == item['evidence_sha256'], 'Historical proof changed')
    for item in plan['preserved_proofs']['copied_manifests_and_restore_receipts']:
        require(sha(ROOT/item['copy']) == item['compressed_sha256'], 'Preserved manifest changed')
    for item in plan['replacement_sources']:
        if item['retained_source']['kind'] == 'pre_enrollment_checkpoint':
            require(sha(Path(item['retained_source']['path'])/'backup.json') == item['manifest_sha256'], 'Retained checkpoint changed')
    state = json.loads(Path(probe['state_path']).read_bytes())
    require(not state.get('active_operation') and state['last_operation']['ok'], 'Archive active or unresolved failure')
    require(state['repository_check'] == plan['existing_production_full_check'], 'Full-check evidence changed; review current state')
    for item in plan['full_check_receipt_files']:
        require(sha(Path(item['path'])) == item['sha256'], 'Full-check receipt changed')
    repo = Path('/mnt/backup/marketdata-restic')
    binary = ROOT/'.cache/restic/0.19.1/restic'
    args = [binary, '-r', repo, '--password-file', Path.home()/'.config/marketdata/secrets/restic-password', '--no-cache', '--json']
    config = json.loads(command([*args, 'cat', 'config']))
    snapshots = json.loads(command([*args, 'snapshots']))
    require(config == probe['repositories'][str(repo)]['config'], 'Production repository identity changed')
    require(snapshots == probe['repositories'][str(repo)]['snapshots'], 'Production snapshots changed')
    return {'at': now(), 'repository_id': config['id'], 'snapshot_ids': sorted(x['id'] for x in snapshots),
            'protected_configurations_and_receipts_unchanged': True, 'automatic_retirement': 'masked',
            'services': services()}


def kit_inventory(root):
    out = []
    for p in sorted(root.rglob('*')):
        require(not p.is_symlink(), 'Kit symlink needs review')
        row = {'path': p.relative_to(root).as_posix(), **signature(p.stat())}
        if p.is_file() and 'secrets' not in p.relative_to(root).parts:
            row['sha256'] = sha(p)
        out.append(row)
    return out


def main():
    require(sys.argv[1:] == ['--run'], 'Explicit --run required')
    plan = json.loads((REVIEW/'plan.json').read_bytes())
    require(plan.pop('plan_id') == PLAN_ID and hashlib.sha256(encode(plan)).hexdigest() == PLAN_ID, 'Plan differs')
    plan['plan_id'] = PLAN_ID
    require(tuple(x['path'] for x in plan['candidate_roots']) == EXPECTED, 'Scope differs')
    approval = json.loads((BASE/'approval.json').read_bytes())
    require(approval['plan_id'] == PLAN_ID and approval['approved'] is True, 'Approval missing')
    require(approval['executor_sha256'] == sha(Path(__file__)), 'Executor differs')
    for name, expected in plan['input_sha256'].items():
        require(sha(REVIEW/name) == expected, f'Plan input changed: {name}')
    require(not AUDIT.exists(), 'Prior attempt exists; reconcile its journal before any retry')
    AUDIT.mkdir(mode=0o700)
    journal = Journal(AUDIT/'progress.jsonl')
    result = {'plan_id': PLAN_ID, 'started_at': now(), 'passed': False,
              'approval_sha256': sha(BASE/'approval.json'), 'executor_sha256': sha(Path(__file__)),
              'attempt': str(AUDIT), 'scope': list(EXPECTED), 'phase': 'preflight',
              'no_service_control': True, 'no_forget_prune': True, 'no_clock_control': True}
    try:
        mount = json.loads(command(['findmnt', '-J', '-R', '/mnt/backup', '-o', 'TARGET,SOURCE,FSTYPE,UUID,OPTIONS']))
        fs = mount['filesystems'][0]
        require(fs['uuid'] == plan['filesystem_uuid'] and not fs.get('children') and 'rw' in fs['options'].split(','), 'Mount differs or not writable')
        result['mount'] = mount
        result['usage_before'] = usage(EXPECTED)
        result['protected_before'] = protected(plan)
        kit = Path(plan['kit_guidance']['planned_destination']).parent
        kit_before = kit_inventory(kit)
        new_json(AUDIT/'kit-before.json', kit_before)
        profile = Path(plan['profile_retirement']['source'])
        require(signature(profile.stat()) == plan['profile_retirement']['source_identity'], 'Trial profile identity differs')
        require(sha(profile) == plan['profile_retirement']['source_sha256'], 'Trial profile content differs')
        require(profile.read_bytes() == (ROOT/plan['profile_retirement']['preserved_copy']).read_bytes(), 'Profile copy differs')
        require(not Path(plan['kit_guidance']['planned_destination']).exists(), 'Kit note already exists')
        from marketdata.checkpoint_locks import checkpoint_locks
        prior_inventory = json.loads((REVIEW/'inventory.json').read_bytes())
        roots = [Path(p) for p in EXPECTED]
        roots += [Path(x['path']) for x in prior_inventory['original_twelve_trees']]
        with checkpoint_locks(roots, exclusive=True, wait=False):
            for target in plan['candidate_roots']:
                require(sha(ROOT/target['inventory']) == target['gzip_sha256'], 'Inventory copy changed')
                n = check_tree(Path(target['path']), ROOT/target['inventory'])
                journal.emit('inventory_verified', root=target['path'], entries=n)
                print(json.dumps({'phase': 'verified', 'root': target['path'], 'entries': n}), flush=True)
            for repo in (Path(EXPECTED[0]), Path(EXPECTED[2])/'repository'):
                require(not list((repo/'locks').iterdir()), 'Trial repository in use')
            result['usage_under_locks'] = usage(EXPECTED)
            result['space_before'] = space()
            result['phase'] = 'profile_retirement'
            journal.emit('profile_retirement_intent', path=str(profile), sha256=sha(profile))
            require(signature(profile.stat()) == plan['profile_retirement']['source_identity'], 'Trial profile changed')
            profile.unlink()
            syncdir(profile.parent)
            journal.emit('profile_retired', path=str(profile))
            result['profile_retired'] = True
            result['phase'] = 'delete_exact_inventories'
            for target in plan['candidate_roots']:
                usage(EXPECTED)
                journal.emit('root_started', root=target['path'])
                delete_tree(Path(target['path']), ROOT/target['inventory'], target['root'], journal)
                journal.emit('root_complete', root=target['path'])
                print(json.dumps({'phase': 'deleted', 'root': target['path'], 'files': target['files']}), flush=True)
        result['phase'] = 'post_verification'
        result['protected_after'] = protected(plan, profile_removed=True)
        require(result['protected_before']['services'] == result['protected_after']['services'], 'Service identity/state changed during operation; investigate')
        require(kit_inventory(kit) == kit_before, 'Existing recovery kit changed')
        require(journal.files == plan['candidate_file_count'] and journal.directories == plan['candidate_directory_count'], 'Deletion count mismatch')
        result['space_after'] = space()
        result['observed_available_change_bytes'] = result['space_after']['available_bytes'] - result['space_before']['available_bytes']
        result['exact_reclaimed_bytes'] = None
        result['space_includes_concurrent_activity'] = True
        result['estimated_reclaimable_allocated_bytes'] = plan['estimated_reclaimable_allocated_bytes']
        result['phase'] = 'kit_guidance'
        note = Path(plan['kit_guidance']['prepared_source'])
        require(sha(note) == plan['kit_guidance']['sha256'], 'Kit note differs')
        destination = Path(plan['kit_guidance']['planned_destination'])
        with destination.open('xb') as f:
            f.write(note.read_bytes())
            f.flush()
            os.fsync(f.fileno())
        syncdir(kit)
        result['kit_note'] = {'path': str(destination), 'sha256': sha(destination)}
        result['phase'] = 'complete'
        result['passed'] = True
        result['files_deleted'] = journal.files
        result['directories_deleted'] = journal.directories
        result['finished_at'] = now()
        result['progress_sha256'] = sha(AUDIT/'progress.jsonl')
        receipt_path = kit/'TRIAL-CLEANUP-20261009.json'
        require(not receipt_path.exists(), 'Kit receipt already exists')
        new_json(receipt_path, result)
        result['kit_receipt'] = {'path': str(receipt_path), 'sha256': sha(receipt_path)}
        new_json(BASE/'result.json', result)
        print(json.dumps({'passed': True, 'files_deleted': journal.files,
                          'observed_available_change_bytes': result['observed_available_change_bytes'],
                          'report': str(BASE/'result.json')}), flush=True)
    except BaseException as exc:
        result.update({'passed': False, 'error': repr(exc), 'finished_at': now(),
                       'files_deleted_in_this_process': journal.files,
                       'directories_deleted_in_this_process': journal.directories,
                       'retry': 'Do not automatically retry; reconcile durable intents/completions and remaining exact inventory.'})
        new_json(BASE/'failure.json', result)
        raise
    finally:
        journal.close()


def space():
    v = os.statvfs('/mnt/backup')
    return {'at': now(), 'available_bytes': v.f_bavail*v.f_frsize,
            'free_bytes': v.f_bfree*v.f_frsize, 'total_bytes': v.f_blocks*v.f_frsize}


if __name__ == '__main__':
    main()
