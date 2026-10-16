"""Remove only the five completed T13 trial roots named in the current request.

Reuse the tested inode-checked deletion and durable journal functions, without
invoking their old main, approval, or checkpoint-retirement code. No wildcards,
service changes, backup, repository check, clock control, forget, or prune.
"""
import argparse
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FINAL = HERE.parent / 't13-operating-completion-2026-10-12'
LIBRARY = HERE.parent / 'trial-artifact-retirement-2026-10-09/apply.py'
CORE_SHA = '3d6549f866fec81a168d58d71a46b66f7ec070523891416f634b58d68f34c947'
TARGETS = tuple(map(Path, [
    '/tmp/t13-daily-operating-preview-20261010',
    '/tmp/t13-daily-operating-preview-20261011',
    '/tmp/t13-daily-operating-preview-v3-20261011',
    '/tmp/t13-daily-predeploy-preview-20261011',
    '/mnt/backup/marketdata-restic-restore/t13-completion-20261012-637d2877b36f4c99',
]))


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_bytes())


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def save(name, value):
    with (HERE / name).open('xb') as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False).encode() + b'\n')
        handle.flush()
        os.fsync(handle.fileno())


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def library():
    assert sha(LIBRARY) == CORE_SHA
    return module('tested_exact_deletion', LIBRARY)


def profile():
    return tomllib.loads(Path('/home/yoonhc/.config/marketdata/restic-archive.toml').read_text())


def use_review():
    helper = module('read_only_process_review', HERE.parent / 't04-restore-cleanup-2026-10-11/prepare.py')
    helper.TARGETS = TARGETS
    result = helper.use_review()
    assert not result['matches'], result['matches']
    return result


def protected(prof, lib):
    prior = read(FINAL / 'restore-plan.json')
    checkpoint = Path(prior['checkpoint'])
    archive_path = Path(prof['state_root']) / prof['expected_repository_id'] / prof['archive_scope'] / 'state.json'
    archive = read(archive_path)
    check = archive['repository_check']
    assert not archive.get('active_operation')
    assert check['ok'] and check['read_data'] and check['status'] == 'complete'
    assert prior['snapshot_id'] in check['snapshot_ids']
    args = [prof['restic_binary'], '-r', prof['restic_repository'], '--password-file',
            prof['restic_password_file'], '--no-cache', '--no-lock', '--json']
    repository = json.loads(lib.command(args + ['cat', 'config']))
    assert repository['id'] == prof['expected_repository_id']
    snapshots = json.loads(lib.command(args + ['snapshots']))
    assert next(s for s in snapshots if s['id'] == prior['snapshot_id']) == read(FINAL / 'snapshot.json')
    masks = {str(Path(base) / name): os.readlink(Path(base) / name)
        for base in ['/etc/systemd/system', '/home/yoonhc/.config/systemd/user']
        for name in ['marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer']}
    assert set(masks.values()) == {'/dev/null'}
    files = read(FINAL / 'host-after.json')['config_sha256']
    configs = {p: sha(p) for p in files}
    assert configs == files
    proofs = {str(p): sha(p) for p in sorted(FINAL.iterdir()) if p.is_file()}
    for name, expected in read(FINAL / 'report-manifest.json')['files_sha256'].items():
        assert proofs[str(FINAL / name)] == expected
    proof_files = [HERE.parent / 't13-daily-review-2026-10-10/manifest.json',
                   HERE.parent / 't13-daily-deploy-2026-10-11/summary.md',
                   HERE.parent / 't13-operating-review-2026-10-11/completion-targets.json']
    proofs.update({str(p): sha(p) for p in proof_files})
    original_manifest = read(FINAL / 'archive-evidence.json')['checkpoint_record']['entry']['manifest_sha256']
    assert sha(checkpoint / 'backup.json') == original_manifest
    assert sha(checkpoint / 'checkpoint-evidence.json') == read(FINAL / 't05-restored-evidence.json')['bundle_sha256']
    t05 = {str(p): sha(p) for p in sorted(Path('/var/lib/marketdata-checkpoint-retirement').rglob('*')) if p.is_file()}
    return {'repository_id': repository['id'], 'snapshots': snapshots, 'archive_state_sha256': sha(archive_path),
            'checkpoint': str(checkpoint), 'checkpoint_manifest_sha256': original_manifest,
            'checkpoint_evidence_sha256': sha(checkpoint / 'checkpoint-evidence.json'),
            'config_sha256': configs, 'report_sha256': proofs, 't05_state_sha256': t05,
            'masks': masks, 'services': lib.services(),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'operating_catalog_identity': {k: v for k, v in lib.signature((ROOT / 'state/catalog.sqlite').stat()).items()
                                           if k in ['dev', 'ino', 'uid', 'gid', 'mode']}}


def locks(stack, prof):
    handle = stack.enter_context((Path(prof['state_root']) / prof['expected_repository_id'] / 'archive.lock').open('r'))
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # The common restore parent survives. Never acquire or remove all of /tmp.
    paths = [TARGETS[-1].parent, *TARGETS]
    for path in paths:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        stack.callback(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for root in TARGETS[:4]:
        for name in ['daily-scheduler.lock', 'writer.lock']:
            handle = stack.enter_context((root / 'store/state' / name).open('r'))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def space():
    result = {}
    for p in [Path('/tmp'), TARGETS[-1].parent]:
        fs = os.statvfs(p)
        result[str(p)] = {'dev': p.stat().st_dev, 'available_bytes': fs.f_bavail * fs.f_frsize}
    return result


def mount_review(lib, prof):
    output = json.loads(lib.command(['findmnt', '-J', '-R', '/mnt/backup', '-o', 'TARGET,SOURCE,FSTYPE,UUID,OPTIONS']))
    backup = output['filesystems'][0]
    assert backup['uuid'] == prof['backup_filesystem_uuid'] and 'rw' in backup['options'].split(',')
    def descendants(value):
        for child in value.get('children', []):
            yield child
            yield from descendants(child)
    assert not any(c['target'] == str(TARGETS[-1]) or c['target'].startswith(str(TARGETS[-1]) + '/') for c in descendants(backup))
    # Include bind-mount boundaries, even where st_dev stays the same.
    mounts = lib.command(['findmnt', '-rn', '-o', 'TARGET']).splitlines()
    assert not any(m == str(root) or m.startswith(str(root) + '/') for root in TARGETS for m in mounts)
    return output


def prepare():
    assert os.geteuid() == 1000, 'Host yoonhc identity required'
    assert read(FINAL / 'verification.json')['passed']
    assert read(FINAL / 't05-restored-evidence.json')['passed']
    assert not (HERE / 'plan.json').exists()
    lib, prof = library(), profile()
    with ExitStack() as stack:
        locks(stack, prof)
        mount = mount_review(lib, prof)
        usage = use_review()
        preservation = protected(prof, lib)
        rows, seen = [], set()
        for index, root in enumerate(TARGETS):
            inv = HERE / f'inventory-{index}.jsonl.gz'
            records = list(lib.observed(root))
            counts = {'files': 0, 'directories': 0, 'logical_file_bytes': 0, 'allocated_bytes': 0}
            for row in records:
                assert row['dev'] == records[0]['dev'] and row['uid'] == 1000
                assert (row['dev'], row['ino']) not in seen
                seen.add((row['dev'], row['ino']))
                counts['allocated_bytes'] += row['blocks'] * 512
                if stat.S_ISREG(row['mode']):
                    assert row['nlink'] == 1
                    counts['files'] += 1
                    counts['logical_file_bytes'] += row['size']
                else:
                    counts['directories'] += 1
            with inv.open('xb') as out:
                out.write(gzip.compress(b''.join(encode(row) + b'\n' for row in records), mtime=0))
            assert lib.check_tree(root, inv) == len(records)
            rows.append({'path': str(root), 'root': {k: v for k, v in records[0].items() if k != 'path'},
                         'inventory': str(inv), 'inventory_sha256': sha(inv), 'entries': len(records), **counts})
            preserve = ['preview.json'] if index < 4 else ['backup.json', 'checkpoint-evidence.json']
            for name in preserve:
                raw = (root / name).read_bytes()
                if index < 4:
                    value = json.loads(raw)
                    assert value['preview_only'] and value['http_requests'] == 0
                    assert value['source_root'] == str(ROOT) and value['destination'] == str(root)
                with (HERE / f'preserved-{index}-{name}.gz').open('xb') as out:
                    out.write(gzip.compress(raw, mtime=0))
        plan = {'prepared_at': now(), 'scope': [str(x) for x in TARGETS], 'trees': rows,
                'authorization_basis': 'Current user request to remove unnecessary T13 trial copies including their folders.',
                'preserved_parent': str(TARGETS[-1].parent), 'mount': mount, 'usage': usage,
                'protected': preservation, 'executor_sha256': sha(__file__), 'library_sha256': CORE_SHA,
                'copies_sha256': {p.name: sha(p) for p in HERE.glob('preserved-*.gz')}, 'space': space()}
        plan['plan_id'] = hashlib.sha256(encode(plan)).hexdigest()
        save('plan.json', plan)
    print(json.dumps({'plan_id': plan['plan_id'], 'trees': [{k: v for k, v in t.items() if k in
          ['path', 'files', 'directories', 'logical_file_bytes', 'allocated_bytes']} for t in rows],
          'usage': usage, 'deletion_performed': False}, indent=2))


def apply(plan_id):
    assert os.geteuid() == 1000, 'Host yoonhc identity required'
    lib, prof, plan = library(), profile(), read(HERE / 'plan.json')
    assert plan_id == plan['plan_id'] == hashlib.sha256(encode({k: v for k, v in plan.items() if k != 'plan_id'})).hexdigest()
    assert plan['scope'] == [str(x) for x in TARGETS] and sha(__file__) == plan['executor_sha256']
    assert not (HERE / 'result.json').exists() and not (HERE / 'deletion-journal.jsonl').exists()
    result = {'passed': False, 'started_at': now(), 'plan_id': plan_id, 'scope': plan['scope'], 'phase': 'preflight'}
    journal = None
    try:
        with ExitStack() as stack:
            locks(stack, prof)
            assert mount_review(lib, prof) == plan['mount']
            usage = use_review()
            known = {(x['pid'], x['uid'], x['comm']) for x in plan['usage']['permission_denied']}
            assert all((x['pid'], x['uid'], x['comm']) in known for x in usage['permission_denied'])
            result['usage'] = usage
            result['protected_before'] = protected(prof, lib)
            assert result['protected_before'] == plan['protected']
            for name, digest in plan['copies_sha256'].items():
                assert sha(HERE / name) == digest
            for item in plan['trees']:
                assert sha(item['inventory']) == item['inventory_sha256']
                assert lib.check_tree(Path(item['path']), Path(item['inventory'])) == item['entries']
            result['space_before'] = space()
            result['phase'] = 'delete_exact_inventory'
            journal = lib.Journal(HERE / 'deletion-journal.jsonl')
            lib.syncdir(HERE)
            journal.emit('authorized_scope', plan_id=plan_id, paths=plan['scope'], authorization=plan['authorization_basis'])
            for item in plan['trees']:
                lib.delete_tree(Path(item['path']), Path(item['inventory']), item['root'], journal)
            assert journal.files == sum(t['files'] for t in plan['trees'])
            assert journal.directories == sum(t['directories'] for t in plan['trees'])
            result['phase'] = 'preservation_check'
            result['protected_after'] = protected(prof, lib)
            assert result['protected_after'] == result['protected_before']
            result['space_after'] = space()
            result['targets_absent'] = all(not p.exists() and not p.is_symlink() for p in TARGETS)
            assert result['targets_absent'] and TARGETS[-1].parent.is_dir()
            result.update(passed=True, phase='complete', files_deleted=journal.files, directories_deleted=journal.directories,
                          permanent_restore_parent_preserved=True, operating_state_preserved=True)
    except BaseException as exc:
        result.update(error=repr(exc), files_deleted=journal.files if journal else 0,
                      directories_deleted=journal.directories if journal else 0,
                      retry='Do not rerun automatically; reconcile this result and journal first.')
        raise
    finally:
        if journal:
            journal.close()
            result['journal_sha256'] = sha(HERE / 'deletion-journal.jsonl')
        result['finished_at'] = now()
        save('result.json', result)
        print(json.dumps({k: v for k, v in result.items() if k not in ['protected_before', 'protected_after', 'usage']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply'])
    parser.add_argument('--plan-id')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    else:
        apply(args.plan_id)
