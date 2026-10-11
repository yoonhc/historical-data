"""Exact T04 trial-root cleanup, including its parent directory.

prepare writes a review only. apply --plan-id requires approval for that exact
root; it reuses the previously tested inode-checked deletion/journal functions.
No old T06 main/approval or automatic checkpoint retirement is invoked.
"""
import argparse
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
import stat
import sys
import tomllib
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prepare as review

WORK, ROOT, RAW = review.WORK, review.ROOT, review.RAW
CORE_HASH = '3d6549f866fec81a168d58d71a46b66f7ec070523891416f634b58d68f34c947'


def library():
    review.require(review.digest(review.LIBRARY) == CORE_HASH, 'Tested deletion library changed')
    spec = importlib.util.spec_from_file_location('exact_cleanup_library', review.LIBRARY)
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    return lib


def read_plan(name):
    value = json.loads((HERE / name).read_text())
    expected = value.pop('plan_id')
    review.require(hashlib.sha256(review.encode(value)).hexdigest() == expected, 'Plan content changed')
    value['plan_id'] = expected
    return value


def combined_inventory(lib, parent, trees, destination):
    """Match os.fwalk ordering: root, both child roots, then each subtree."""
    lib.plain(parent)
    expected_names = {Path(x['path']).name for x in trees}
    review.require(set(x.name for x in parent.iterdir()) == expected_names, 'Unexpected parent contents')
    root = {'path': '.', **lib.signature(parent.stat())}
    with gzip.open(destination, 'xt', encoding='utf-8') as out:
        out.write(json.dumps(root, sort_keys=True) + '\n')
        for item in sorted(trees, key=lambda x: x['path']):
            prefix = Path(item['path']).name
            row = next(lib.records(Path(item['inventory'])))
            review.require(row['path'] == '.', 'Missing child root inventory')
            out.write(json.dumps({**row, 'path': prefix}, sort_keys=True) + '\n')
        for item in sorted(trees, key=lambda x: x['path']):
            prefix = Path(item['path']).name
            rows = lib.records(Path(item['inventory']))
            next(rows)
            for row in rows:
                out.write(json.dumps({**row, 'path': prefix + '/' + row['path']}, sort_keys=True) + '\n')
    return {k: v for k, v in root.items() if k != 'path'}


def profile():
    return tomllib.loads(Path('/home/yoonhc/.config/marketdata/restic-archive.toml').read_text())


def locks(stack, p, checkpoint):
    handle = stack.enter_context((Path(p['state_root']) / p['expected_repository_id'] / 'archive.lock').open('r'))
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # Preserve the permanent restore parent lock domain; remove only WORK.
    for parent, exclusive in sorted(((WORK.parent, True), (WORK, True), (checkpoint.parent, False)), key=lambda x: str(x[0])):
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        stack.callback(os.close, fd)
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)


def mount(p):
    value = json.loads(review.command(['findmnt', '-J', '-R', '/mnt/backup', '-o', 'TARGET,SOURCE,FSTYPE,UUID,OPTIONS']))
    fs = value['filesystems'][0]
    review.require(fs['uuid'] == p['backup_filesystem_uuid'] and 'rw' in fs['options'].split(','), 'Mount changed')
    review.require(not fs.get('children'), 'Submount requires separate review')
    return value


def use_check(previous):
    review.TARGETS = (WORK,)
    current = review.use_review()
    review.require(not current['matches'], 'Trial root is in use')
    known = {(x['pid'], x['uid'], x['comm']) for x in previous['usage']['permission_denied']}
    review.require(all((x['pid'], x['uid'], x['comm']) in known for x in current['permission_denied']),
                   'New inaccessible process requires review')
    return current


def protected(lib, previous, p):
    for name, digest in previous['preserved_evidence_sha256'].items():
        review.require(review.digest(name) == digest, 'Preserved evidence changed: ' + name)
    checkpoint = previous['retained_checkpoint']
    review.require(review.digest(Path(checkpoint['path']) / 'backup.json') == checkpoint['manifest_sha256'], 'Checkpoint changed')
    args = [p['restic_binary'], '-r', p['restic_repository'], '--password-file',
            p['restic_password_file'], '--no-cache', '--no-lock', '--json']
    config = json.loads(review.command([*args, 'cat', 'config']))
    review.require(config['id'] == previous['repository_id'], 'Repository changed')
    snapshots = json.loads(review.command([*args, 'snapshots']))
    selected = [x for x in snapshots if x['id'] == previous['snapshot_id']]
    review.require(selected == [json.loads((HERE / 'snapshot-current.json').read_text())], 'Retained snapshot changed')
    archive_path = Path(p['state_root']) / p['expected_repository_id'] / p['archive_scope'] / 'state.json'
    archive = json.loads(archive_path.read_text())
    check = archive.get('repository_check') or {}
    review.require(not archive.get('active_operation') and check.get('status') == 'complete'
                   and check.get('ok') and check.get('read_data') and previous['snapshot_id'] in check.get('snapshot_ids', []),
                   'Archive active or required full check absent')
    masks = {str(d / name): os.readlink(d / name) for d in (
        Path('/home/yoonhc/.config/systemd/user'), Path('/etc/systemd/system'))
        for name in ('marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer')}
    review.require(set(masks.values()) == {'/dev/null'}, 'Retirement mask changed')
    states = Path('/var/lib/marketdata-checkpoint-retirement')
    state_hashes = {str(x): review.digest(x) for x in sorted(states.rglob('*')) if x.is_file()}
    config_hashes = {}
    for directory in (Path('/etc/marketdata'), Path('/home/yoonhc/.config/marketdata')):
        for path in directory.rglob('*'):
            if path.is_symlink() or not path.is_file() or 'secrets' in path.relative_to(directory).parts:
                continue
            if path.suffix in ('.json', '.toml', '.conf', '.service', '.timer', '.env'):
                config_hashes[str(path)] = review.digest(path)
    for path in (Path('/etc/chrony/marketdata.conf'), Path('/etc/adjtime')):
        config_hashes[str(path)] = review.digest(path)
    return {'snapshot_metadata': snapshots, 'archive_state_sha256': review.digest(archive_path),
            'checkpoint_manifest_sha256': checkpoint['manifest_sha256'], 'masks': masks,
            't05_state_sha256': state_hashes, 'configuration_sha256': config_hashes,
            'services': lib.services(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'recovery_kit_identity': {k: v for k, v in lib.signature(Path('/mnt/backup/marketdata-recovery-kit-20261003').stat()).items()
                                      if k in ('dev', 'ino', 'mode', 'uid', 'gid')}}


def prepare_parent(lib, previous, p):
    inventory = RAW / 'whole-t04-inventory.jsonl.gz'
    review.require(not (HERE / 'parent-plan.json').exists() and not inventory.exists(), 'Parent plan already exists')
    with ExitStack() as stack:
        locks(stack, p, Path(previous['retained_checkpoint']['path']))
        actual_mount = mount(p)
        for item in previous['trees']:
            review.require(review.digest(item['inventory']) == item['inventory_sha256'], 'Child inventory changed')
        root = combined_inventory(lib, WORK, previous['trees'], inventory)
        count = lib.check_tree(WORK, inventory)
        value = {'prepared_at': datetime.now(timezone.utc).isoformat(), 'scope': str(WORK),
                 'scope_clarification': 'T04 시험 폴더 전체(부모 포함)',
                 'apply_requires_explicit_execution_approval': True, 'deleted_files': 0,
                 'previous_plan_id': previous['plan_id'], 'previous_plan_sha256': review.digest(HERE / 'plan.json'),
                 'root': root, 'permanent_restore_parent_preserved': str(WORK.parent),
                 'inventory': str(inventory), 'inventory_sha256': review.digest(inventory), 'entries': count,
                 'file_count': previous['file_count'], 'directory_count': previous['directory_count'] + 1,
                 'estimated_reclaimable_allocated_bytes': previous['estimated_reclaimable_allocated_bytes'] + root['blocks'] * 512,
                 'mount': actual_mount, 'usage': use_check(previous), 'protected': protected(lib, previous, p),
                 'executor_sha256': review.digest(Path(__file__)), 'review_script_sha256': review.digest(HERE / 'prepare.py'),
                 'library_sha256': CORE_HASH, 'validation_sha256': review.digest(HERE / 'parent-adapter-tests.json')}
        value['plan_id'] = hashlib.sha256(review.encode(value)).hexdigest()
        review.save(HERE / 'parent-plan.json', value)
    print(json.dumps({k: value[k] for k in ('plan_id', 'scope', 'file_count', 'directory_count',
                                           'estimated_reclaimable_allocated_bytes', 'deleted_files')}, indent=2))


def apply_parent(lib, previous, p, plan_id):
    plan = read_plan('parent-plan.json')
    review.require(plan_id == plan['plan_id'] and plan['scope'] == str(WORK), 'Approval scope differs')
    review.require(review.digest(HERE / 'plan.json') == plan['previous_plan_sha256'], 'Previous evidence changed')
    for name, digest in ((Path(__file__), plan['executor_sha256']), (HERE / 'prepare.py', plan['review_script_sha256']),
                         (Path(plan['inventory']), plan['inventory_sha256']), (HERE / 'parent-adapter-tests.json', plan['validation_sha256'])):
        review.require(review.digest(name) == digest, 'Fixed input changed: ' + str(name))
    review.require(json.loads((HERE / 'parent-adapter-tests.json').read_text())['passed'], 'Validation missing')
    journal_path = RAW / 'parent-deletion-journal.jsonl'
    review.require(not journal_path.exists() and not (HERE / 'parent-result.json').exists(), 'Prior attempt exists; reconcile before retry')
    result = {'passed': False, 'plan_id': plan_id, 'scope': str(WORK),
              'started_at': datetime.now(timezone.utc).isoformat(), 'executor_sha256': plan['executor_sha256'],
              'approval_basis': 'User clarified whole T04 root; explicit approved apply command for this plan.',
              'no_backup_requested': True, 'no_clock_control': True, 'automatic_retirement': 'masked', 'phase': 'preflight'}
    journal = None
    try:
        with ExitStack() as stack:
            locks(stack, p, Path(previous['retained_checkpoint']['path']))
            result['mount'] = mount(p)
            result['usage_before'] = use_check(previous)
            result['protected_before'] = protected(lib, previous, p)
            review.require(result['protected_before'] == plan['protected'], 'Protected state changed since review')
            review.require(lib.check_tree(WORK, Path(plan['inventory'])) == plan['entries'], 'Inventory count differs')
            journal = lib.Journal(journal_path)
            lib.syncdir(RAW)
            result['space_before'] = lib.space()
            result['phase'] = 'delete_exact_inventory'
            journal.emit('approved_root', plan_id=plan_id, root=str(WORK), inventory_sha256=plan['inventory_sha256'])
            lib.delete_tree(WORK, Path(plan['inventory']), plan['root'], journal)
            review.require(journal.files == plan['file_count'] and journal.directories == plan['directory_count'], 'Deletion count differs')
            result['phase'] = 'verify_preservation'
            result['protected_after'] = protected(lib, previous, p)
            review.require(result['protected_after'] == result['protected_before'], 'Protected state changed; inspect')
            result['space_after'] = lib.space()
            result['observed_available_change_bytes'] = result['space_after']['available_bytes'] - result['space_before']['available_bytes']
            result['exact_reclaimed_bytes'] = None
            result.update(passed=True, phase='complete', files_deleted=journal.files, directories_deleted=journal.directories,
                          target_absent=not WORK.exists(), permanent_restore_parent_preserved=WORK.parent.is_dir())
    except BaseException as exc:
        result.update(error=repr(exc), files_deleted=journal.files if journal else 0,
                      directories_deleted=journal.directories if journal else 0,
                      retry='Do not automatically retry; reconcile the retained plan and journal first.')
        raise
    finally:
        if journal:
            journal.close()
            result['journal_sha256'] = review.digest(journal_path)
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        review.save(HERE / 'parent-result.json', result)
        print(json.dumps({k: v for k, v in result.items() if k not in ('protected_before', 'protected_after', 'usage_before')}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'apply'))
    parser.add_argument('--plan-id')
    args = parser.parse_args()
    review.require(os.geteuid() == 1000, 'Run as yoonhc on host')
    lib, previous, p = library(), read_plan('plan.json'), profile()
    if args.action == 'prepare':
        prepare_parent(lib, previous, p)
    else:
        review.require(args.plan_id, 'Explicit approved plan ID required')
        apply_parent(lib, previous, p, args.plan_id)


if __name__ == '__main__':
    main()
