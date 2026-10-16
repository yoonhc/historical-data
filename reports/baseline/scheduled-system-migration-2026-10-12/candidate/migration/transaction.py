"""Exact-plan, crash-recoverable configuration transaction. No service/clock commands.

The injectable host must prove both managers drained and the target configuration
loaded. CLI is deliberately inspection-only until the separately approved root
handoff/preflight. The same transaction is exercised against real isolated files.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path

PURPOSE = 'scheduled-manager-only-v1'
JOBS = ('oi', 'oi-probe', 'oi-quality', 'daily', 'pack', 'backup', 'restic-archive', 'network-observer')
PROFILE = '/etc/marketdata/clock-bootstrap.json'
LEDGER = '/var/lib/marketdata-clock/boot-state.json'

class Hold(RuntimeError):
    pass

def encode(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode() + b'\n'

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    # Same encoding as the already installed schema-2 gate.
    return sha(json.dumps(value, sort_keys=True).encode())

def allowed_paths():
    result = {PROFILE, '/etc/marketdata/checkpoint-clock-retirement/clock-binding.json',
        '/etc/marketdata/scheduled-system/topology.json', '/etc/marketdata/scheduled-system/oi-live.toml',
        '/etc/systemd/system/marketdata-clock-bootstrap.service',
        '/etc/systemd/system/marketdata-clock-ready.service'}
    for job in JOBS:
        for ext in ('service', 'timer'):
            name = f'marketdata-{job}.{ext}'
            result.update({'/etc/systemd/system/' + name, '/home/yoonhc/.config/systemd/user/' + name})
        result.add('/home/yoonhc/.config/systemd/user/timers.target.wants/marketdata-' + job + '.timer')
    return result

def validate(plan, approval):
    if plan.get('purpose') != PURPOSE or plan.get('schema') != 1 or plan.get('policy_changes') != []:
        raise Hold('Wrong plan scope')
    if plan.get('plan_id') != sha(encode({k: v for k, v in plan.items() if k != 'plan_id'})):
        raise Hold('Plan hash differs')
    if (approval.get('purpose') != PURPOSE or approval.get('plan_id') != plan['plan_id']
            or approval.get('enabled') is not True or not approval.get('approval_reference')
            or set(approval.get('directions', [])) != {'apply', 'rollback'}):
        raise Hold('Explicit exact-plan approval required; existing grants do not apply')
    if plan.get('ledger_path') != LEDGER or plan.get('bootstrap_path') != PROFILE:
        raise Hold('Unexpected control paths')
    if set(plan['operations']) != allowed_paths():
        raise Hold('Incomplete or out-of-scope eight-job configuration transaction')
    for operation in plan['operations'].values():
        for node in operation.values():
            if node['kind'] == 'file' and sha(node['text'].encode()) != node['sha256']:
                raise Hold('Embedded configuration hash differs')
            if node['kind'] not in {'file', 'symlink', 'absent'}:
                raise Hold('Unknown filesystem operation')
    before, after = (json.loads(plan['operations'][PROFILE][x]['text']) for x in ('before', 'after'))
    if set(before) != set(after):
        raise Hold('Bootstrap schema changed')
    for key in before:
        if key not in {'user_units', 'guard_files'} and before[key] != after[key]:
            raise Hold('Bootstrap/runtime policy changed: ' + key)
    expected_users = dict(before['user_units'])
    for job in JOBS:
        for ext in ('service', 'timer'):
            expected_users[f'marketdata-{job}.{ext}'] = 'masked'
    if after['user_units'] != expected_users:
        raise Hold('Old user work is not completely masked')
    bootstrap_unit = '/etc/systemd/system/marketdata-clock-bootstrap.service'
    for path, digest in before['guard_files'].items():
        if path != bootstrap_unit and after['guard_files'].get(path) != digest:
            raise Hold('Existing guard removed or changed: ' + path)
    for path, operation in plan['operations'].items():
        if path.startswith('/etc/systemd/system/') or path.endswith('/topology.json'):
            if after['guard_files'].get(path) != operation['after']['sha256']:
                raise Hold('New system entrypoint is not pinned: ' + path)
    binding_path = '/etc/marketdata/checkpoint-clock-retirement/clock-binding.json'
    binding = json.loads(plan['operations'][binding_path]['after']['text'])
    if binding != {'bootstrap_profile': PROFILE,
                   'bootstrap_profile_sha256': plan['operations'][PROFILE]['after']['sha256']}:
        raise Hold('T05 binding does not match the new bootstrap profile')
    ledger = plan['ledger_before']
    if (ledger.get('schema') != 1 or ledger.get('phase') != 'closed'
            or ledger.get('boot_id') != plan['boot_id']
            or ledger.get('profile_sha256') != canonical(before)
            or not ledger.get('closed_observation', {}).get('ready')):
        raise Hold('A closed, prepared same-boot baseline is required')

class Files:
    def __init__(self, root):
        self.root = Path(root).resolve()
        # This candidate does not expose a live mutation entrypoint. Root approval
        # alone is not a substitute for the outstanding actual-manager preflight.
        if self.root == Path('/') or not (self.root / '.migration-fixture').is_file():
            raise Hold('Only an explicit isolated fixture is executable in this candidate')

    def path(self, name):
        p = Path(name)
        if not p.is_absolute() or '..' in p.parts:
            raise Hold('Unsafe path')
        result = self.root / p.relative_to('/')
        for parent in result.parents:
            if parent == self.root:
                break
            if parent.is_symlink():
                raise Hold('Symlink parent escapes the fixture')
        return result

    def node(self, name):
        p = self.path(name)
        if p.is_symlink():
            return {'kind': 'symlink', 'target': os.readlink(p)}
        if not p.exists():
            return {'kind': 'absent'}
        if not p.is_file():
            raise Hold('Expected regular file')
        raw = p.read_bytes()
        return {'kind': 'file', 'mode': stat.S_IMODE(p.stat().st_mode),
                'text': raw.decode(), 'sha256': sha(raw)}

    def write(self, name, node):
        p = self.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        if node['kind'] == 'absent':
            p.unlink(missing_ok=True)
        else:
            temp = p.with_name('.' + p.name + '.' + uuid.uuid4().hex)
            if node['kind'] == 'symlink':
                temp.symlink_to(node['target'])
            else:
                fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, node['mode'])
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(node['text'].encode())
                    stream.flush()
                    os.fchmod(stream.fileno(), node['mode'])
                    os.fsync(stream.fileno())
            os.replace(temp, p)
        fd = os.open(p.parent, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def json(self, name):
        return json.loads(self.path(name).read_text())

    def save(self, name, value, *, immutable=False):
        if immutable and self.path(name).exists():
            if self.json(name) != value:
                raise Hold('Immutable receipt differs')
            return
        raw = encode(value)
        self.write(name, {'kind': 'file', 'mode': 0o644, 'text': raw.decode(), 'sha256': sha(raw)})

    @contextlib.contextmanager
    def lock(self):
        path = self.path('/var/lib/marketdata-clock/coordinator.lock')
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Hold('Coordinator lock busy') from exc
            yield
        finally:
            os.close(fd)

class Transaction:
    def __init__(self, plan, approval, files, host, *, boot, fault=lambda point: None):
        validate(plan, approval)
        self.plan, self.approval, self.fs, self.host = plan, approval, files, host
        self.boot, self.fault = boot, fault
        self.directory = '/var/lib/marketdata-clock/scheduled-transitions/' + plan['plan_id']

    def name(self, name):
        return self.directory + '/' + name

    def checks(self):
        if self.boot() != self.plan['boot_id']:
            raise Hold('Boot changed; review recovery explicitly, do not reopen stepping')
        for path, expected in self.plan['protected'].items():
            if expected['kind'] == 'digest':
                actual = sha(self.fs.path(path).read_bytes())
                if actual != expected['sha256']:
                    raise Hold('Protected input changed: ' + path)
            elif self.fs.node(path) != expected:
                raise Hold('Protected input changed: ' + path)
        for key, expected in self.plan['retirement_masks'].items():
            manager, name = key.split(':', 1)
            base = '/etc/systemd/system/' if manager == 'system' else '/home/yoonhc/.config/systemd/user/'
            if self.fs.node(base + name) != expected or expected != {'kind': 'symlink', 'target': '/dev/null'}:
                raise Hold('Automatic retirement must remain masked')
        for path, sides in self.plan['operations'].items():
            if self.fs.node(path) not in sides.values():
                raise Hold('Unrecognised intermediate file: ' + path)
        self.host.drained()  # Both managers, pending jobs, all eight work/timer units.

    def pending(self, progress):
        return {'schema': 2, 'purpose': PURPOSE, 'plan_id': self.plan['plan_id'],
                'direction': progress['direction'], 'preserved': progress['base_ledger']}

    def stage(self, direction='apply'):
        if direction not in {'apply', 'rollback'}:
            raise Hold('Unknown direction')
        with self.fs.lock():
            self.checks()
            state = self.fs.json(LEDGER)
            progress_path = self.name('progress.json')
            progress = self.fs.json(progress_path) if self.fs.path(progress_path).exists() else None
            receipt = self.name(direction + '-receipt.json')
            if self.fs.path(receipt).exists() and state == self.fs.json(receipt)['ledger_after']:
                target = 'after' if direction == 'apply' else 'before'
                if any(self.fs.node(path) != op[target] for path, op in self.plan['operations'].items()):
                    raise Hold('Committed files drifted')
                return {'configuration': direction + '_committed', 'idempotent': True}
            if progress is None:
                if direction != 'apply' or state != self.plan['ledger_before']:
                    raise Hold('Baseline ledger differs')
                if any(self.fs.node(p) != op['before'] for p, op in self.plan['operations'].items()):
                    raise Hold('Baseline configuration differs')
                progress = {'direction': direction, 'phase': 'intent', 'base_ledger': state}
                self.fs.save(self.name('plan.json'), self.plan, immutable=True)
                self.fs.save(self.name('approval.json'), self.approval, immutable=True)
                self.fs.save(progress_path, progress)
                self.fault('intent')
            elif progress['direction'] != direction:
                if direction != 'rollback':
                    raise Hold('A reverted plan cannot be reapplied; review a new plan')
                valid_pending = state == self.pending(progress)
                applied = self.name('apply-receipt.json')
                valid_commit = self.fs.path(applied).exists() and state == self.fs.json(applied)['ledger_after']
                if not (valid_pending or valid_commit or state == progress['base_ledger']):
                    raise Hold('Cannot reconcile rollback state')
                base = state if valid_commit else progress['base_ledger']
                progress = {'direction': direction, 'phase': 'intent', 'base_ledger': base}
                self.fs.save(progress_path, progress)
                self.fault('rollback_intent')
            if self.fs.json(self.name('plan.json')) != self.plan or self.fs.json(self.name('approval.json')) != self.approval:
                raise Hold('Recorded authorization differs')
            old_apply_pending = self.pending({**progress, 'direction': 'apply'})
            if (state not in (progress['base_ledger'], self.pending(progress))
                    and not (direction == 'rollback' and state == old_apply_pending)):
                raise Hold('Ledger changed outside this transaction')
            self.fs.save(LEDGER, self.pending(progress))
            self.fault('pending')
            target = 'after' if direction == 'apply' else 'before'
            for index, (path, op) in enumerate(sorted(self.plan['operations'].items())):
                if self.fs.node(path) != op[target]:
                    self.fs.write(path, op[target])
                self.fault('file:' + str(index))
            progress['phase'] = 'staged'
            self.fs.save(progress_path, progress)
            self.fault('staged')
            return {'configuration': direction + '_staged', 'work_allowed': False}

    def commit(self):
        with self.fs.lock():
            self.checks()
            progress = self.fs.json(self.name('progress.json'))
            direction = progress['direction']
            target = 'after' if direction == 'apply' else 'before'
            if any(self.fs.node(p) != op[target] for p, op in self.plan['operations'].items()):
                raise Hold('Partial configuration cannot be committed')
            if self.fs.json(self.name('plan.json')) != self.plan or self.fs.json(self.name('approval.json')) != self.approval:
                raise Hold('Recorded authorization differs')
            receipt_path = self.name(direction + '-receipt.json')
            state = self.fs.json(LEDGER)
            if self.fs.path(receipt_path).exists() and state == self.fs.json(receipt_path)['ledger_after']:
                return {'configuration': direction + '_committed', 'idempotent': True,
                        'current_clock_ready': self.host.clock_ready()}
            if progress['phase'] != 'staged' or state != self.pending(progress):
                raise Hold('Not at activation boundary')
            effective = self.host.activation(direction)  # Exact reloaded units and masks.
            ready = self.host.clock_ready()  # Separate from approval/configuration validity.
            profile = json.loads(self.plan['operations'][PROFILE][target]['text'])
            final = {**progress['base_ledger'], 'profile_sha256': canonical(profile)}
            history = list(final.get('configuration_history', []))
            history.append({'plan_id': self.plan['plan_id'], 'direction': direction,
                            'approval_sha256': canonical(self.approval), 'receipt': receipt_path})
            final['configuration_history'] = history
            receipt = {'schema': 1, 'purpose': PURPOSE, 'plan_id': self.plan['plan_id'],
                       'direction': direction, 'ledger_after': final,
                       'approval_sha256': canonical(self.approval), 'effective': effective,
                       'clock_ready_at_commit': ready, 'step_reopened': False}
            if self.fs.path(receipt_path).exists():
                existing = self.fs.json(receipt_path)
                if existing['ledger_after'] != final or existing['approval_sha256'] != receipt['approval_sha256']:
                    raise Hold('Interrupted receipt differs')
            else:
                self.fs.save(receipt_path, receipt, immutable=True)
            self.fault('receipt')
            self.fs.save(LEDGER, final)
            self.fault('committed')
            return {'configuration': direction + '_committed', 'current_clock_ready': ready,
                    'step_reopened': False, 'receipt': receipt_path}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    print(json.dumps({'plan_id': plan['plan_id'], 'candidate_only': True,
        'operations': len(plan['operations']), 'live_mutation_entrypoint': False,
        'next': 'Review isolated results, then separately approve actual manager preflight and handoff'}))
