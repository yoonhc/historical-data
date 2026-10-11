"""Read-only review of exactly two completed T04 restore trial trees.

No deletion, backup, repository check, service operation or approval creation.
Only review files under the project reports directories are written.
"""
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import tomllib
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
HERE = Path(__file__).resolve().parent
RAW = ROOT / 'reports/t04-restore-cleanup-2026-10-11'
PRIOR = HERE.parent / 't04-operating-clock-restore-2026-10-10'
WORK = Path('/mnt/backup/marketdata-restic-restore/t04-clock-evidence-20261010-f71c5b20490248b4b229a66f3378ab10')
TARGETS = (WORK / 'extracted', WORK / 'store')
LIBRARY = HERE.parent / 'trial-artifact-retirement-2026-10-09/apply.py'


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def save(path, value):
    with path.open('xb') as f:
        f.write(json.dumps(value, indent=2, ensure_ascii=False).encode() + b'\n')
        f.flush()
        os.fsync(f.fileno())


def command(argv):
    p = subprocess.run(list(map(str, argv)), capture_output=True, text=True, check=True, timeout=60)
    return p.stdout


def use_review():
    matches, denied, examined = [], [], 0
    roots = [str(x) for x in TARGETS]
    def inside(value):
        return any(value == r or value.startswith(r + '/') for r in roots)
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        uid, comm = None, None
        try:
            uid = proc.stat().st_uid
            comm = (proc / 'comm').read_text().strip()
            argv = (proc / 'cmdline').read_bytes().decode(errors='replace').split('\0')
            if any(inside(x) for x in argv):
                matches.append({'pid': int(proc.name), 'comm': comm, 'via': 'argv'})
            if uid != os.getuid():
                continue
            examined += 1
            references = [os.readlink(x) for x in [proc / 'cwd', *list((proc / 'fd').iterdir())]]
            references += [line.split(maxsplit=5)[5] for line in (proc / 'maps').read_text().splitlines()
                           if len(line.split(maxsplit=5)) == 6]
            for value in references:
                if inside(value.removesuffix(' (deleted)')):
                    matches.append({'pid': int(proc.name), 'comm': comm, 'via': 'cwd/fd/maps', 'path': value})
        except PermissionError:
            denied.append({'pid': int(proc.name), 'uid': uid, 'comm': comm})
        except (FileNotFoundError, ProcessLookupError):
            continue
    return {'same_uid_examined': examined, 'matches': matches, 'permission_denied': denied,
            'scope': 'Visible argv for all UIDs; cwd/fd/maps for current UID. This is not a guarantee against all noncooperating processes.'}


def main():
    require(os.geteuid() == 1000, 'Run as yoonhc on the host')
    require(not RAW.exists() and not (HERE / 'plan.json').exists(), 'Review already exists; preserve it')
    prior = json.loads((PRIOR / 'execution.json').read_text())
    require(prior['passed'] and prior['phase'] == 'complete', 'T04 verification incomplete')
    require([prior['extracted'], prior['restored']] == [str(x) for x in TARGETS], 'Trial paths differ')
    profile = tomllib.loads(Path('/home/yoonhc/.config/marketdata/restic-archive.toml').read_text())
    checkpoint = Path(prior['backup']['path'])
    require(digest(checkpoint / 'backup.json') == prior['backup']['manifest_sha256'], 'Retained manifest changed')
    spec = importlib.util.spec_from_file_location('prior_cleanup_library', LIBRARY)
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    lib.plain(WORK)
    require(set(x.name for x in WORK.iterdir()) == {'extracted', 'store'}, 'Unexpected work-root contents')
    mount = json.loads(command(['findmnt', '-J', '-R', '/mnt/backup', '-o', 'TARGET,SOURCE,FSTYPE,UUID,OPTIONS']))
    fs = mount['filesystems'][0]
    require(fs['uuid'] == profile['backup_filesystem_uuid'] and 'rw' in fs['options'].split(','), 'Host mount differs')
    def descendants(value):
        for child in value.get('children', []):
            yield child
            yield from descendants(child)
    require(not any(x['target'] == str(WORK) or x['target'].startswith(str(WORK) + '/') for x in descendants(fs)),
            'Nested mount in trial scope')
    RAW.mkdir(mode=0o700)
    report = {'prepared_at': datetime.now(timezone.utc).isoformat(), 'passed': False,
              'approval_required': True, 'deletion_authority': False, 'deleted_files': 0,
              'mount': mount, 'filesystem_uuid': fs['uuid'], 'scope': [str(x) for x in TARGETS],
              'parent_preserved': str(WORK), 'inventory_library_sha256': digest(LIBRARY)}
    with ExitStack() as stack:
        repo_lock = Path(profile['state_root']) / profile['expected_repository_id'] / 'archive.lock'
        handle = stack.enter_context(repo_lock.open('r'))
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        for parent, exclusive in ((WORK, True), (checkpoint.parent, False)):
            fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            stack.callback(os.close, fd)
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        args = [profile['restic_binary'], '-r', profile['restic_repository'], '--password-file',
                profile['restic_password_file'], '--no-cache', '--no-lock', '--json']
        config = json.loads(command([*args, 'cat', 'config']))
        require(config['id'] == profile['expected_repository_id'], 'Repository identity differs')
        snapshots = json.loads(command([*args, 'snapshots', prior['snapshot_id']]))
        require(len(snapshots) == 1 and snapshots[0]['id'] == prior['snapshot_id'], 'Snapshot missing')
        require(snapshots[0] == json.loads((PRIOR / 'snapshot-inventory.json').read_text())['selected'],
                'Original snapshot metadata changed')
        save(HERE / 'snapshot-current.json', snapshots[0])
        archive = json.loads((Path(profile['state_root']) / profile['expected_repository_id'] / profile['archive_scope'] / 'state.json').read_text())
        check = archive.get('repository_check') or {}
        require(check.get('ok') and check.get('read_data') and prior['snapshot_id'] in check.get('snapshot_ids', []),
                'Successful retained-snapshot full check absent')
        report['repository_id'] = config['id']
        report['snapshot_id'] = prior['snapshot_id']
        report['existing_full_check'] = check
        report['retained_checkpoint'] = {'path': str(checkpoint), 'backup_id': prior['backup']['backup_id'],
                                          'manifest_sha256': digest(checkpoint / 'backup.json')}
        require(digest(TARGETS[0] / 'backup.json') == prior['backup']['manifest_sha256'], 'Extracted manifest changed')
        for name, source in (('backup.json.gz', TARGETS[0] / 'backup.json'),
                             ('restore.json.gz', TARGETS[1] / 'restore.json')):
            raw = source.read_bytes()
            with (RAW / name).open('xb') as f:
                f.write(gzip.compress(raw, mtime=0))
        retained = {}
        for group in ('checkpoint_queries', 'restored_queries'):
            for stream, result in prior[group].items():
                path = Path(result['retained_stdout_gzip'])
                require(digest(path) == result['retained_stdout_sha256'], 'Preserved query changed')
                query = json.loads(gzip.decompress(path.read_bytes()))
                require(hashlib.sha256(encode(query)).hexdigest() == result['query_sha256'], 'Query result differs')
                retained[str(path)] = digest(path)
        for path in sorted(PRIOR.rglob('*')):
            if path.is_file():
                retained[str(path)] = digest(path)
        for path in RAW.iterdir():
            if path.is_file():
                retained[str(path)] = digest(path)
        report['preserved_evidence_sha256'] = retained
        trees = []
        inodes = set()
        for target in TARGETS:
            inv = RAW / (target.name + '-inventory.jsonl.gz')
            counts = {'files': 0, 'directories': 0, 'logical_file_bytes': 0, 'allocated_bytes': 0, 'hardlinked_files': 0}
            root_identity = None
            with gzip.open(inv, 'xt', encoding='utf-8') as f:
                for row in lib.observed(target):
                    if root_identity is None:
                        root_identity = {k: v for k, v in row.items() if k != 'path'}
                    require(row['dev'] == WORK.stat().st_dev, 'Crossed device')
                    key = (row['dev'], row['ino'])
                    require(key not in inodes, 'Shared inode in candidate trees')
                    inodes.add(key)
                    counts['allocated_bytes'] += row['blocks'] * 512
                    if stat.S_ISREG(row['mode']):
                        counts['files'] += 1
                        counts['logical_file_bytes'] += row['size']
                        counts['hardlinked_files'] += int(row['nlink'] != 1)
                    else:
                        counts['directories'] += 1
                    f.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
            require(counts['hardlinked_files'] == 0, 'External hardlinks require separate review')
            trees.append({'path': str(target), 'root': root_identity, 'inventory': str(inv),
                          'inventory_sha256': digest(inv), **counts})
        report['trees'] = trees
        report['usage'] = use_review()
        require(not report['usage']['matches'], 'Trial files still in use')
        v = os.statvfs('/mnt/backup')
        report['available_bytes'] = v.f_bavail * v.f_frsize
        report['estimated_reclaimable_allocated_bytes'] = sum(x['allocated_bytes'] for x in trees)
        report['logical_file_bytes'] = sum(x['logical_file_bytes'] for x in trees)
        report['file_count'] = sum(x['files'] for x in trees)
        report['directory_count'] = sum(x['directories'] for x in trees)
        report['exact_reclaimed_bytes'] = None
        report['exclusions'] = ['production data/catalog', 'all checkpoints', 'restic repository/snapshots',
                                'recovery kit', 'T05 registry and masks', 'T13 previews', 'parent/sibling paths',
                                'report evidence', 'service/clock changes']
        report['passed'] = True
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report['plan_id'] = hashlib.sha256(encode(report)).hexdigest()
        save(HERE / 'plan.json', report)
    print(json.dumps({k: report[k] for k in ('passed', 'plan_id', 'scope', 'file_count', 'directory_count',
                                             'logical_file_bytes', 'estimated_reclaimable_allocated_bytes',
                                             'available_bytes', 'usage', 'deletion_authority', 'deleted_files')}, indent=2))


if __name__ == '__main__':
    main()
