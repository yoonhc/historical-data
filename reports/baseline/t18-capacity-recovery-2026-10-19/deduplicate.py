"""Apply only the reviewed closed-output hardlink plan; never control services."""

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data/reports/restic-archive-runs')
LOCK = Path('/home/yoonhc/projects/algoTrade/historical_data/archive-state/b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558/archive.lock')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def plain(path):
    path = Path(path)
    require(not any(p.is_symlink() for p in (path, *path.parents)), f'Symlink: {path}')
    require(stat.S_ISREG(path.lstat().st_mode), f'Not regular: {path}')
    return path


def attributes(path):
    return {name: os.getxattr(path, name).hex() for name in os.listxattr(path)}


def metadata(item, *, original=True):
    path = plain(item['path'])
    info = path.stat()
    actual = {'dev': info.st_dev, 'uid': info.st_uid, 'gid': info.st_gid,
              'mode': stat.S_IMODE(info.st_mode), 'bytes': info.st_size,
              'xattrs': attributes(path)}
    for key, value in actual.items():
        require(item[key] == value, f'Metadata changed ({key}): {path}')
    if original:
        for key, value in {'ino': info.st_ino, 'nlink': info.st_nlink,
                           'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}.items():
            require(item[key] == value, f'Original changed ({key}): {path}')
    return info


def temporary(target, plan_id):
    path = Path(target['path'])
    return path.with_name('.' + path.name + '.t18-' + plan_id[:20] + '.link')


def inspect_group(group, plan_id):
    source = group['source']
    source_info = metadata(source, original=False)
    require(source_info.st_ino == source['ino'], 'Retained source inode changed')
    require(source_info.st_mtime_ns == source['mtime_ns'], 'Retained source mtime changed')
    body = Path(source['path']).read_bytes()
    require(digest(body) == group['sha256'], 'Retained source content changed')
    links, completed = 1, 0
    for target in group['targets']:
        info = metadata(target, original=False)
        same = info.st_ino == source_info.st_ino and info.st_dev == source_info.st_dev
        if same:
            links += 1
            completed += 1
        else:
            metadata(target)
        require(Path(target['path']).read_bytes() == body, 'Target content differs')
        temp = temporary(target, plan_id)
        if temp.exists() or temp.is_symlink():
            ti = plain(temp).stat()
            require((ti.st_dev, ti.st_ino) == (source_info.st_dev, source_info.st_ino),
                    'Unrecognized temporary path')
            links += 1
    require(Path(source['path']).stat().st_nlink == links, 'Unplanned hardlinks exist')
    return completed


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_target(group, target, plan_id, emit):
    source = Path(group['source']['path'])
    path = Path(target['path'])
    source_info = source.stat()
    current = metadata(target, original=False)
    temp = temporary(target, plan_id)
    if (current.st_dev, current.st_ino) == (source_info.st_dev, source_info.st_ino):
        # A committed replacement is never expanded or repeated on resume.
        if temp.exists():
            require(plain(temp).stat().st_ino == source_info.st_ino, 'Wrong temporary inode')
            temp.unlink()
            sync_directory(path.parent)
        return False
    metadata(target)
    require(path.read_bytes() == source.read_bytes(), 'Content changed before replacement')
    emit({'phase': 'before_replace', 'path': str(path), 'original_inode': current.st_ino,
          'source': str(source)})
    if not temp.exists():
        os.link(source, temp, follow_symlinks=False)
        sync_directory(path.parent)
    else:
        ti = plain(temp).stat()
        require((ti.st_dev, ti.st_ino) == (source_info.st_dev, source_info.st_ino), 'Wrong temporary')
    # Recheck the original pathname after preparing the link.
    metadata(target)
    os.replace(temp, path)
    sync_directory(path.parent)
    require(path.stat().st_ino == source_info.st_ino, 'Replacement not visible')
    emit({'phase': 'after_replace', 'path': str(path), 'source_inode': source_info.st_ino})
    return True


def unit(name):
    raw = subprocess.check_output(['/usr/bin/systemctl', 'show', name, '--no-pager',
                                   '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID',
                                   '-p', 'ControlPID', '-p', 'Job', '-p', 'UnitFileState'], text=True)
    return dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)


def guard(operation):
    timer = unit('marketdata-restic-archive.timer')
    service = unit('marketdata-restic-archive.service')
    require(timer.get('ActiveState') == 'inactive' and not timer.get('Job'), 'Timer not stopped')
    require(timer.get('UnitFileState') == 'enabled', 'Timer enable/mask changed')
    require(service.get('ActiveState') == 'inactive' and not service.get('Job')
            and service.get('MainPID') == '0' and service.get('ControlPID') == '0', 'Archiver not idle')
    for path, expected in operation['protected_sha256'].items():
        require(digest(plain(path).read_bytes()) == expected, f'Protected input changed: {path}')
    for path, expected in operation['protected_links'].items():
        require(Path(path).is_symlink() and os.readlink(path) == expected, 'Protected link changed')
    return {'timer': timer, 'service': service}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', choices=('check', 'apply'), required=True)
    parser.add_argument('--approve-operation')
    args = parser.parse_args()
    envelope = json.loads((HERE / 'dedup-operation.json').read_bytes())
    operation = envelope['operation']
    require(digest(canonical(operation)) == envelope['operation_id'], 'Operation digest differs')
    for name, expected in operation['bundle_sha256'].items():
        require(digest(plain(HERE / name).read_bytes()) == expected, 'Tool/plan bundle changed')
    wrapped = json.loads((HERE / 'dedup-plan.json').read_bytes())
    plan, plan_id = wrapped['plan'], wrapped['plan_id']
    require(digest(canonical(plan)) == plan_id == operation['plan_id'], 'Plan digest differs')
    require(Path(plan['root']) == ROOT, 'Unexpected root')
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == plan['boot_id'], 'Boot changed')
    require(os.getuid() == 1000, 'Run as yoonhc (UID 1000), without sudo')
    if args.action == 'apply':
        require(args.approve_operation == envelope['operation_id'], 'Exact operation approval required')
    else:
        require(args.approve_operation is None, 'Check does not accept approval')
    for group in plan['groups']:
        for item in [group['source'], *group['targets']]:
            path = Path(item['path'])
            require(path.is_relative_to(ROOT) and len(path.relative_to(ROOT).parts) == 2
                    and path.suffix == '.stdout', 'Unexpected target path')
    report_dir = HERE / 'dedup-audit'
    report_dir.mkdir(exist_ok=True)
    attempt = uuid.uuid4().hex
    report = {'operation_id': envelope['operation_id'], 'plan_id': plan_id,
              'action': args.action, 'started_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'passed': False, 'replaced_this_run': 0, 'service_control': False, 'ledger_writes': False}
    def emit(value):
        with (report_dir / (attempt + '.jsonl')).open('ab') as out:
            out.write(canonical(value) + b'\n')
            out.flush()
            os.fsync(out.fileno())
    try:
        report['before'] = guard(operation)
        before = os.statvfs(ROOT)
        report['available_before'] = before.f_bavail * before.f_frsize
        with plain(LOCK).open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            guard(operation)
            receipts = {}
            for group in plan['groups']:
                for item in [group['source'], *group['targets']]:
                    for prefix in ('command_receipt', 'run_receipt'):
                        receipts[item[prefix]] = item[prefix + '_sha256']
            for path, expected in receipts.items():
                require(digest(plain(path).read_bytes()) == expected, 'Receipt changed')
            report['already_replaced'] = sum(inspect_group(g, plan_id) for g in plan['groups'])
            if args.action == 'apply':
                for group in plan['groups']:
                    for target in group['targets']:
                        report['replaced_this_run'] += replace_target(group, target, plan_id, emit)
                total = sum(inspect_group(g, plan_id) for g in plan['groups'])
                require(total == plan['counts']['targets'], 'Incomplete replacement')
                for path, expected in receipts.items():
                    require(digest(plain(path).read_bytes()) == expected, 'Receipt changed after operation')
                report['verified_targets'] = total
            report['after'] = guard(operation)
        after = os.statvfs(ROOT)
        report.update(passed=True, available_after=after.f_bavail * after.f_frsize,
                      expected_reclaim_bytes=plan['capacity']['expected_reclaim_bytes'])
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        report['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        (report_dir / (attempt + '.json')).write_bytes(canonical(report) + b'\n')
        print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
