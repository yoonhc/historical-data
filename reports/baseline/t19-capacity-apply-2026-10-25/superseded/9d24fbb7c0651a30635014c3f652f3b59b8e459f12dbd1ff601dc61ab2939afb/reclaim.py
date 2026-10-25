"""One approved historical stdout batch; active timer, nonblocking archive lock.

No systemctl mutation, restic command, request/ledger write, or payload copy.
The previous pinned replacement algorithm remains an explicit dependency.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import uuid

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / 't18-capacity-recovery-2026-10-19/deduplicate.py'
OLD_SHA = '4e68dd62804b3cc8b270decac8354dc0cc3b6c51b068ae1f659d503268b86263'
SELECTION = HERE.parent / 'archive-capacity-planning-2026-10-25/verified-selection.json'
SELECTION_SHA = '975299ec183583d3345bf5b35a5619285d4a74604ca95f4083a3a69c8f0d12a5'
PROPOSAL_ID = 'a15f778666f6f377765466bd27076f4474977c5629ca1228b9cdbd7d5cf85e93'
PROJECT = HERE.parents[2]
ROOT = PROJECT / 'reports/restic-archive-runs'
REPOSITORY = 'b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558'
LOCK = PROJECT / 'archive-state' / REPOSITORY / 'archive.lock'
STATE = LOCK.parent / 'historical-data-original-v1/state.json'
REQUEST = PROJECT / 'state/scheduled-clock-policy/restic-archive/request.json'
TIMER = 'marketdata-restic-archive.timer'
SERVICE = 'marketdata-restic-archive.service'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def require(value, message):
    if not value:
        raise RuntimeError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def load_old():
    require(digest(OLD.read_bytes()) == OLD_SHA, 'Previous algorithm changed')
    spec = importlib.util.spec_from_file_location('t19_pinned_replacement', OLD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


d = load_old()


class Deferred(RuntimeError):
    pass


@contextmanager
def exclusive(path):
    """Open the existing lock without creating/truncating/replacing its inode."""
    path = d.plain(path)
    with path.open('rb') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Deferred('lock_busy: ' + str(path)) from exc
        try:
            require(os.fstat(handle.fileno()).st_ino == path.stat().st_ino, 'Lock inode changed')
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class ReadBudget:
    def __init__(self, rate=64 * 1024 ** 2):
        self.rate = rate
        self.bytes = 0
        self.started = time.monotonic()

    def charge(self, count):
        self.bytes += count
        delay = self.bytes / self.rate - (time.monotonic() - self.started)
        if delay > 0:
            time.sleep(delay)

    def read(self, path):
        raw = Path(path).read_bytes()
        self.charge(len(raw))
        return raw


def validate_paths(plan, root=ROOT):
    seen = set()
    inodes = set()
    for group in plan['groups']:
        for item in [group['source'], *group['targets']]:
            path = Path(item['path'])
            require(path.is_absolute() and '..' not in path.parts, 'Invalid absolute path')
            require(path.is_relative_to(root), 'Path outside selected root')
            parts = path.relative_to(root).parts
            require(len(parts) == 2 and re.fullmatch('[0-9a-f]{32}', parts[0])
                    and re.fullmatch('[0-9a-f]{32}\\.stdout', parts[1]), 'Unexpected output path')
            require(str(path) not in seen, 'Duplicate path')
            require((item['dev'], item['ino']) not in inodes and item['nlink'] == 1,
                    'Original selection must contain independent single-link files')
            seen.add(str(path))
            inodes.add((item['dev'], item['ino']))
            require(item['sha256'] == group['sha256'], 'Manifest identity differs')
            require(item['command_receipt'] == str(path.with_suffix('.json'))
                    and item['run_receipt'] == str(path.parent / 'run.json'), 'Unexpected receipt path')


def verify_receipts(plan):
    refs = {}
    for group in plan['groups']:
        for item in [group['source'], *group['targets']]:
            for prefix in ('command_receipt', 'run_receipt'):
                path, expected = item[prefix], item[prefix + '_sha256']
                require(path not in refs or refs[path] == expected, 'Receipt expectations conflict')
                refs[path] = expected
    for path, expected in refs.items():
        require(digest(d.plain(path).read_bytes()) == expected, 'Receipt changed: ' + path)
    return len(refs)


def inspect(plan, operation_id, *, reader=None):
    """A shared inode is hashed once; every independent target is compared.

    Metadata-only check is explicit. Apply always passes a real byte reader.
    """
    completed = 0
    for group in plan['groups']:
        source = group['source']
        si = d.metadata(source, original=False)
        require(si.st_ino == source['ino'] and si.st_mtime_ns == source['mtime_ns'],
                'Retained source changed')
        body = reader(source['path']) if reader else None
        if reader:
            require(digest(body) == group['sha256'], 'Retained source content changed')
        links = 1
        for target in group['targets']:
            ti = d.metadata(target, original=False)
            same = (ti.st_dev, ti.st_ino) == (si.st_dev, si.st_ino)
            if same:
                completed += 1
                links += 1
            else:
                d.metadata(target)
                require(ti.st_blocks * 512 == target['allocated_bytes'], 'Target allocation changed')
                if reader:
                    require(reader(target['path']) == body, 'Target content differs')
                    d.metadata(target)
            temp = d.temporary(target, operation_id)
            if os.path.lexists(temp):
                ts = d.plain(temp).stat()
                require((ts.st_dev, ts.st_ino) == (si.st_dev, si.st_ino), 'Unrecognized temporary path')
                links += 1
        require(d.metadata(source, original=False).st_nlink == links, 'Unplanned hardlinks exist')
    return completed


def replace(group, target, operation_id, emit, budget):
    changed = d.replace_target(group, target, operation_id, emit)
    if changed:
        # Pinned helper compares both bodies immediately before replacement.
        budget.charge(target['bytes'] * 2)
    else:
        # Resume after replace-but-before-directory-fsync must repeat the fsync,
        # including when no temporary link remains. Do not infer power durability.
        d.sync_directory(Path(target['path']).parent)
    return changed


def replace_all(plan, operation_id, emit, budget, guard):
    count = 0
    for group in plan['groups']:
        for index, target in enumerate(group['targets']):
            if index % 32 == 0:
                guard()
            count += replace(group, target, operation_id, emit, budget)
    return count


def units():
    props = 'Id,LoadState,ActiveState,SubState,MainPID,ControlPID,Job,UnitFileState,NeedDaemonReload,FragmentPath,DropInPaths,InvocationID'
    raw = subprocess.check_output(['/usr/bin/systemctl', 'show', TIMER, SERVICE,
                                   '--no-pager', '--property=' + props], text=True)
    result = {}
    for block in raw.strip().split('\n\n'):
        value = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        require(value.get('Id') in (TIMER, SERVICE), 'Unexpected manager output')
        result[value['Id']] = value
    require(set(result) == {TIMER, SERVICE}, 'Incomplete manager output')
    return result


def manager_guard(value, *, idle):
    timer, service = value[TIMER], value[SERVICE]
    for name, row in value.items():
        require(row.get('LoadState') == 'loaded' and row.get('NeedDaemonReload') == 'no'
                and row.get('DropInPaths') == ''
                and row.get('FragmentPath') == '/etc/systemd/system/' + name,
                'Manager configuration differs: ' + name)
    require(timer.get('ActiveState') == 'active' and timer.get('UnitFileState') == 'enabled'
            and not timer.get('Job'), 'Expected unchanged active/enabled timer')
    if idle and not (service.get('ActiveState') == 'inactive'
                     and service.get('MainPID') == '0' and service.get('ControlPID') == '0'
                     and not service.get('Job')):
        raise Deferred('archiver_not_idle')


def file_identity(path):
    path = d.plain(path)
    info = path.stat()
    return {'sha256': digest(path.read_bytes()), 'uid': info.st_uid, 'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode)}


def static_guard(operation):
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == operation['boot_id'],
            'Boot changed')
    for path, expected in operation['protected_files'].items():
        require(file_identity(path) == expected, 'Protected input changed: ' + path)
    for path, target in operation['protected_links'].items():
        require(Path(path).is_symlink() and os.readlink(path) == target, 'Protected link changed')


def available():
    v = os.statvfs(ROOT)
    return v.f_bavail * v.f_frsize


def space_guard(operation):
    free = available()
    if free < operation['collector_reserve_bytes'] + operation['audit_budget_bytes']:
        raise Deferred('home_space_below_reserve_and_audit_budget')
    return free


def request_summary():
    # A natural lock_busy attempt may update this request while the lock is held.
    # Observe it; never rewrite or freeze normal request progress.
    raw = d.plain(REQUEST).read_bytes()
    value = json.loads(raw)
    return {k: value.get(k) for k in ('request_id', 'identity', 'state', 'exit_code', 'pins')} | {
        'sha256': digest(raw), 'attempt_id': value.get('attempt', {}).get('id')}


class Audit:
    def __init__(self, root, budget, attempt):
        self.root, self.budget, self.attempt = root, budget, attempt
        if not root.exists():
            root.mkdir(mode=0o700)
            d.sync_directory(root.parent)
        require(not root.is_symlink(), 'Audit directory is a symlink')

    def room(self, needed, reserve=65536):
        used = sum(p.stat().st_blocks * 512 for p in self.root.iterdir() if p.is_file())
        require(used + needed + reserve <= self.budget, 'Audit budget exhausted; review required')

    def write_once(self, name, value):
        raw = canonical(value) + b'\n'
        self.room(max(len(raw), 4096), reserve=0)
        with (self.root / name).open('xb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        d.sync_directory(self.root)

    def emit(self, value):
        raw = canonical({'at': now(), **value}) + b'\n'
        self.room(len(raw) + 4096)
        with (self.root / (self.attempt + '.jsonl')).open('ab') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        d.sync_directory(self.root)

    def authority(self, operation_id, action):
        path = self.root / 'authority.json'
        if path.exists():
            value = json.loads(d.plain(path).read_bytes())
            require(value['operation_id'] == operation_id and value['uid'] == os.getuid()
                    and value['accepted_at'], 'Authority mismatch')
        else:
            require(action == 'apply', 'No accepted authority; use approved apply first')
            self.write_once('authority.json', {'operation_id': operation_id, 'accepted_at': now(),
                                             'uid': os.getuid(), 'scope': 'exact historical stdout replacements'})
        # Covers a prior creation followed by directory synchronization failure.
        d.sync_directory(self.root)
        return digest(path.read_bytes())


def load_operation():
    raw = (HERE / 'operation.json').read_bytes()
    envelope = json.loads(raw)
    op, oid = envelope['operation'], envelope['operation_id']
    require(digest(canonical(op)) == oid, 'Operation digest differs')
    for name, expected in op['bundle_sha256'].items():
        require(digest(d.plain(HERE / name).read_bytes()) == expected, 'Tool bundle changed: ' + name)
    require(digest(SELECTION.read_bytes()) == SELECTION_SHA, 'Exact selection changed')
    selected = json.loads(SELECTION.read_bytes())
    plan = selected['proposal']
    require(digest(canonical(plan)) == selected['proposal_id'] == PROPOSAL_ID == op['proposal_id'],
            'Selection identity differs')
    require(plan['root'] == str(ROOT) and plan['counts']['replacement_paths'] == 1022,
            'Wrong scope')
    validate_paths(plan)
    return op, oid, plan, digest(raw)


def execute(operation, oid, plan, action, audit, report):
    static_guard(operation)
    report['manager_before'] = units()
    manager_guard(report['manager_before'], idle=True)
    report['available_before_bytes'] = space_guard(operation)
    # Order: operation audit lock -> archive lock. Never coordinator/request lock.
    with exclusive(LOCK):
        static_guard(operation)
        manager_guard(units(), idle=True)
        state_before = digest(d.plain(STATE).read_bytes())
        require(not json.loads(STATE.read_bytes()).get('active_operation'), 'Unfinished archive operation')
        report['request_before'] = request_summary()
        report['receipts_verified'] = verify_receipts(plan)
        budget = ReadBudget(operation['read_budget_bytes_per_second'])
        report['already_replaced'] = inspect(plan, oid, reader=None if action == 'check' else budget.read)
        if action != 'check':
            def guard():
                space_guard(operation)
                audit.room(32768)
                static_guard(operation)
            report['replaced_this_attempt'] = replace_all(plan, oid, audit.emit, budget, guard)
            report['verified_targets'] = inspect(plan, oid, reader=budget.read)
            require(report['verified_targets'] == plan['counts']['replacement_paths'], 'Incomplete replacement')
            require(all(not os.path.lexists(d.temporary(t, oid)) for g in plan['groups'] for t in g['targets']),
                    'Temporary links remain')
            report['receipts_verified_after'] = verify_receipts(plan)
        else:
            report['payload_rehashed'] = False
        static_guard(operation)
        require(digest(STATE.read_bytes()) == state_before, 'Archive state changed while locked')
        report['archive_state_unchanged_while_locked'] = True
        report['manager_before_unlock'] = units()
        # A new natural invocation may now be running or have ended with 75.
        # Its record is preserved; this tool must neither restart it nor fail
        # simply because it was dispatched after we acquired the lock.
        manager_guard(report['manager_before_unlock'], idle=False)
        report['request_before_unlock'] = request_summary()
        report['payload_bytes_read'] = budget.bytes
    report['available_after_bytes'] = available()
    report['expected_total_reclaim_bytes'] = plan['capacity']['expected_reclaim_bytes']
    report['passed'] = True
    report['state'] = 'checked_metadata_only' if action == 'check' else 'complete'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('check', 'apply', 'resume'), required=True)
    parser.add_argument('--approve-operation')
    args = parser.parse_args()
    operation, oid, plan, operation_sha = load_operation()
    require(os.getuid() == 1000, 'Run as UID 1000, without sudo')
    require(args.approve_operation == (None if args.action == 'check' else oid), 'Exact approval required')
    os.umask(0o077)
    os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
    subprocess.run(['/usr/bin/ionice', '-c', '3', '-p', str(os.getpid())], check=True)
    attempt = uuid.uuid4().hex
    audit = Audit(HERE / 'audit', operation['audit_budget_bytes'], attempt)
    own_lock = audit.root / 'operation.lock'
    if not own_lock.exists():
        try:
            with own_lock.open('xb'):
                pass
            d.sync_directory(audit.root)
        except FileExistsError:
            pass
    report = {'operation_id': oid, 'operation_json_sha256': operation_sha, 'proposal_id': PROPOSAL_ID,
              'attempt_id': attempt, 'action': args.action, 'started_at': now(), 'passed': False,
              'state': 'checking', 'replaced_this_attempt': 0, 'service_control': False,
              'ledger_writes': False, 'payload_copies': 0}
    old_handler = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    code = 1
    try:
        with exclusive(own_lock):
            if args.action != 'check':
                report['authority_sha256'] = audit.authority(oid, args.action)
            complete = audit.root / 'complete.json'
            if complete.exists():
                prior = json.loads(d.plain(complete).read_bytes())
                require(prior['operation_id'] == oid and prior['passed'], 'Invalid completion record')
                report.update(passed=True, state='already_complete_no_mutation', completion_record=str(complete))
            else:
                execute(operation, oid, plan, args.action, audit, report)
                if args.action != 'check':
                    audit.write_once('complete.json', {**report, 'finished_at': now()})
            code = 0
    except Deferred as exc:
        report.update(state='deferred', error=str(exc), replaced_this_attempt=None,
                      recovery='Reinspect exact paths; a mid-operation hold can leave completed links.')
        code = 75
    except BaseException as exc:
        report.update(passed=False, state='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                      error=f'{type(exc).__name__}: {exc}',
                      replaced_this_attempt=None,
                      recovery='Preserve paths/evidence; inspect and resume the same operation only.')
    finally:
        signal.signal(signal.SIGTERM, old_handler)
        report['finished_at'] = now()
        try:
            audit.write_once(attempt + '.json', report)
        except BaseException as exc:
            report.update(passed=False, state='audit_failed', audit_error=f'{type(exc).__name__}: {exc}')
            code = 1
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
