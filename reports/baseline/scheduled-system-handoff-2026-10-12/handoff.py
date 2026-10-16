"""Approved same-boot handoff, separate from the frozen configuration engine.

The default command is inspection. Live execution additionally requires a
passed exact-tool root preflight and an explicit root-owned outer approval.
No clock control, collector control, deletion or policy changes are exposed.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_common as c
sys.path.insert(0, str(c.BASE / 'candidate/migration'))
import pinned
import systemd_read as reader
import transaction as tx

PURPOSE = 'scheduled-manager-host-handoff-v1'
STATE = '/var/lib/marketdata-clock/scheduled-handoffs'
MASK = {'kind': 'symlink', 'target': '/dev/null'}
ABSENT = {'kind': 'absent'}
PHASES = ('new', 'installed', 'intent', 'held', 'drained', 'staged', 'committed', 'activated', 'complete')

def validate_outer(plan, approval):
    c.require(plan['purpose'] == PURPOSE and plan['policy_changes'] == [], 'Wrong outer scope')
    c.require(plan['plan_id'] == c.sha(c.encode({k: v for k, v in plan.items() if k != 'plan_id'})), 'Outer plan hash differs')
    c.require(approval.get('enabled') is True and approval.get('purpose') == PURPOSE
        and approval.get('plan_id') == plan['plan_id'] and approval.get('approval_reference')
        and set(approval.get('directions', [])) == {'apply', 'rollback'}, 'Explicit outer approval required')
    c.require(plan['inner']['release_id'] == 'scheduled-manager-20261012-255d64b78ffa8328', 'Candidate changed')
    c.require(plan['inner']['plan_id'] == json.loads((c.HERE / 'configuration-plan.json').read_bytes())['plan_id'], 'Inner plan changed')
    for name in c.TIMERS:
        op = plan['enable_links'][c.SYSTEM_ROOT + 'timers.target.wants/' + name]
        c.require(op == {'before': ABSENT, 'after': {'kind': 'symlink', 'target': c.SYSTEM_ROOT + name}}, 'Enable link differs')
    c.require(len(plan['enable_links']) == 8, 'Unexpected enable links')

class Host:
    def __init__(self, plan, files, managers):
        self.plan, self.fs, self.m = plan, files, managers

    def immutable(self):
        c.require(c.boot() == self.plan['boot_id'], 'Boot changed: explicit recovery review required')
        pinned.check_parents()
        for path, digest in self.plan['extra_protected'].items():
            c.require(c.sha(Path(path).read_bytes()) == digest, 'Protected guard changed: ' + path)
        inner = self.plan['inner']
        for path, node in inner['protected'].items():
            if node['kind'] == 'digest':
                c.require(c.sha(self.fs.path(path).read_bytes()) == node['sha256'], 'Protected configuration changed: ' + path)
            else:
                c.require(self.fs.node(path) == node, 'Protected configuration changed: ' + path)
        for key, node in inner['retirement_masks'].items():
            manager, name = key.split(':', 1)
            root = c.USER_ROOT if manager == 'user' else c.SYSTEM_ROOT
            c.require(self.fs.node(root + name) == MASK == node, 'Retirement mask changed')

    def drained(self):
        self.immutable()
        for manager in ('user', 'system'):
            states = self.m.states(manager, (*c.SERVICES, *c.TIMERS))
            c.require(all(v['classification'] == 'idle' for v in states.values()), 'Jobs have not drained in ' + manager)
        # T05 registration/preview/apply must not overlap the binding update.
        for manager in ('user', 'system'):
            raw = self.m.command(manager, 'list-units', 'marketdata-checkpoint-retirement*',
                                 '--all', '--no-legend', '--no-pager', '--plain').stdout
            for line in raw.splitlines():
                name = line.split()[0]
                c.require(c.classify(self.m.show(manager, name), self.m.jobs(manager)) == 'idle', 'T05 is still running')

    def activation(self, direction):
        result = {}
        if direction == 'apply':
            for name, spec in self.plan['inner']['topology']['units'].items():
                p = self.m.show('system', name)
                reader.verify(p, spec, quiescent=spec['kind'] == 'work')
                if name.endswith('.service'):
                    c.require(p['ProtectClock'] == 'yes', 'ProtectClock differs')
                result[name] = p
            for name in (*c.SERVICES, *c.TIMERS):
                c.require(self.m.show('user', name)['LoadState'] == 'masked', 'Old unit not masked')
        else:
            for name, expected in self.plan['effective_user_before'].items():
                p = self.m.show('user', name)
                for key, value in expected.items():
                    current = reader.commands(p.get(key, '')) if key in reader.COMMANDS else p.get(key)
                    c.require(current == value, 'Restored effective unit differs: ' + name + ':' + key)
                result[name] = p
            for name in (*c.SERVICES, *c.TIMERS, c.WAITER):
                c.require(self.m.show('system', name)['LoadState'] == 'not-found', 'New system unit remains loaded')
        # Bootstrap's exact file content and owner are pinned by the transaction.
        bootstrap = self.m.show('system', 'marketdata-clock-bootstrap.service')
        c.require(bootstrap['NeedDaemonReload'] == 'no' and not bootstrap['DropInPaths'], 'Bootstrap activation differs')
        for path, operation in self.plan['inner']['operations'].items():
            side = 'after' if direction == 'apply' else 'before'
            desired = operation[side]
            if desired['kind'] != 'absent':
                owner = self.plan['ownership'][path][side]
                c.require(c.metadata(self.fs.path(path)) == owner, 'Installed ownership differs: ' + path)
        return result

    def clock_ready(self):
        gate = pinned.load_gate()
        runtime = gate.rt.load_profile(Path('/etc/marketdata/runtime.json'))
        # Read-only current observation. Ledger is deliberately pending here.
        return bool(gate.rt.probe(runtime)['ready'])

    def baseline(self):
        for manager, expected in self.plan['inventory_before'].items():
            actual = self.m.command(manager, 'list-unit-files', 'marketdata-*', '--no-legend', '--no-pager').stdout
            c.require(actual == expected, 'Manager inventory changed before handoff: ' + manager)
        for path, item in self.plan['ownership'].items():
            c.require(c.metadata(self.fs.path(path)) == item['before'], 'Before ownership changed: ' + path)
        for name in (*c.SERVICES, *c.TIMERS, c.WAITER):
            p = self.m.show('system', name)
            c.require(p['LoadState'] == 'not-found' and c.classify(p, self.m.jobs('system')) == 'idle', 'Target manager is not empty')
        for name, expected in self.plan['effective_user_before'].items():
            p = self.m.show('user', name)
            for key, value in expected.items():
                current = reader.commands(p.get(key, '')) if key in reader.COMMANDS else p.get(key)
                c.require(current == value, 'Original effective unit changed: ' + name + ':' + key)

class Handoff:
    def __init__(self, plan, approval, fs, managers, host, *, installer=lambda: None,
                 boot=c.boot, pause=time.sleep, monotonic=time.monotonic, fault=lambda point: None):
        validate_outer(plan, approval)
        self.plan, self.approval, self.fs, self.m, self.host = plan, approval, fs, managers, host
        self.installer, self.boot, self.pause, self.monotonic, self.fault = installer, boot, pause, monotonic, fault
        self.directory = STATE + '/' + plan['plan_id']
        inner_approval = {'purpose': tx.PURPOSE, 'enabled': True, 'plan_id': plan['inner']['plan_id'],
            'approval_reference': self.directory + '/approval.json', 'directions': ['apply', 'rollback']}
        self.t = tx.Transaction(plan['inner'], inner_approval, fs, host, boot=boot)

    def path(self, name):
        return self.directory + '/' + name

    def save(self):
        self.fs.save(self.path(self.direction + '.json'), self.state)

    def phase(self, name):
        self.state['phase'] = name
        self.save()
        self.fault('phase:' + name)

    def need(self, name):
        return PHASES.index(self.state['phase']) < PHASES.index(name)

    def remember_cancel(self, manager, name, state):
        key = manager + ':' + name
        if key not in self.state['cancelled']:
            self.state['cancelled'][key] = state
            self.save()  # durable original job/command/PID/invocation before cancel
        self.fault('cancel-intent:' + key)

    def hold_source(self, manager):
        root = c.USER_ROOT if manager == 'user' else c.SYSTEM_ROOT
        self.state.setdefault('source_at_hold', {}).setdefault(manager, self.m.states(manager, (*c.SERVICES, *c.TIMERS)))
        self.save()
        for name in c.TIMERS:
            self.m.command(manager, 'stop', '--no-block', name)
        # Persistently mask both services and timers; do not stop executing work.
        for name in (*c.SERVICES, *c.TIMERS):
            self.fs.write(root + name, MASK)
            self.fault('barrier:' + manager + ':' + name)
        self.m.command(manager, 'daemon-reload')
        for name in (*c.SERVICES, *c.TIMERS):
            c.require(self.m.show(manager, name)['LoadState'] == 'masked', 'Start barrier not effective')

    def drain(self, manager, timeout):
        deadline = self.monotonic() + timeout
        while True:
            states = self.m.states(manager, (*c.SERVICES, *c.TIMERS))
            pending = False
            for name, state in states.items():
                kind = state['classification']
                if kind == 'idle':
                    continue
                pending = True
                if kind == 'pending':
                    self.remember_cancel(manager, name, state)
                    # Barrier makes a no-PID cancellation safe across a re-read.
                    current = self.m.states(manager, (name,))[name]
                    c.require(current['properties']['LoadState'] == 'masked', 'Barrier vanished')
                    if current['classification'] == 'pending':
                        self.m.command(manager, 'stop', '--no-block', name)
                # running and unknown: wait; never turn timeout into SIGKILL.
            if not pending:
                break
            c.require(self.monotonic() < deadline, 'Normal drain pending; rerun same approved plan to continue')
            self.pause(1)
        # Only after ALL business units/jobs drained may a Requires waiter stop.
        self.m.command(manager, 'stop', '--no-block', c.WAITER)
        while c.classify(self.m.show(manager, c.WAITER), self.m.jobs(manager)) != 'idle':
            c.require(self.monotonic() < deadline, 'Waiter cancellation pending')
            self.pause(1)

    def inner_intent(self):
        if self.fs.node(self.t.name('progress.json'))['kind'] != 'absent':
            return
        with self.fs.lock():
            self.host.baseline()
            c.require(self.fs.json(tx.LEDGER) == self.plan['inner']['ledger_before'], 'Initial ledger changed')
            c.require(all(self.fs.node(p) == op['before'] for p, op in self.plan['inner']['operations'].items()), 'Initial files changed')
            self.fs.save(self.t.name('plan.json'), self.plan['inner'], immutable=True)
            self.fs.save(self.t.name('approval.json'), self.t.approval, immutable=True)
            self.fs.save(self.t.name('progress.json'), {'direction': 'apply', 'phase': 'intent',
                         'base_ledger': self.fs.json(tx.LEDGER)})
        # Clock ledger remains closed while running T05/backup is draining.

    def rollback_pending(self):
        with self.fs.lock():
            p = self.fs.json(self.t.name('progress.json'))
            state = self.fs.json(tx.LEDGER)
            if p['direction'] != 'rollback':
                receipt_path = self.t.name('apply-receipt.json')
                applied = self.fs.node(receipt_path)['kind'] != 'absent' and state == self.fs.json(receipt_path)['ledger_after']
                c.require(applied or state in (p['base_ledger'], self.t.pending(p)), 'Unrecognised rollback ledger')
                p = {'direction': 'rollback', 'phase': 'intent', 'base_ledger': state if applied else p['base_ledger']}
                self.fs.save(self.t.name('progress.json'), p)
            c.require(state in (p['base_ledger'], self.t.pending(p), self.t.pending({**p, 'direction': 'apply'})), 'Rollback ledger mismatch')
            self.fs.save(tx.LEDGER, self.t.pending(p))
            # Remove temporary system masks only with pending ledger already
            # durable. The frozen engine now sees only its approved before/after.
            for name in (*c.SERVICES, *c.TIMERS):
                path = c.SYSTEM_ROOT + name
                if self.fs.node(path) == MASK:
                    self.fs.write(path, self.plan['inner']['operations'][path]['after'])

    def request_start(self, manager, name):
        key = manager + ':' + name
        requests = self.state.setdefault('start_requests', {})
        p = self.m.show(manager, name)
        if key in requests:
            entry = requests[key]
            if entry.get('accepted'):
                return
            # Crash at a command boundary: do not blindly replay an expired job.
            changed = p.get('InvocationID') and p.get('InvocationID') != entry['before'].get('InvocationID')
            activated = p.get('ActiveState') == 'active' or p.get('ActiveEnterTimestampMonotonic', '0') != entry['before'].get('ActiveEnterTimestampMonotonic', '0')
            c.require(changed or activated, 'Indeterminate start request: review ' + key + ' before reissuing')
            entry.update(accepted=True, reconciled=True, after=p)
            self.save()
            return
        requests[key] = {'before': p, 'accepted': False}
        self.save()
        self.fault('start-intent:' + key)
        self.m.command(manager, 'start', '--no-block', name)
        self.fault('start-return:' + key)
        requests[key].update(accepted=True, after=self.m.show(manager, name))
        self.save()

    def run(self, direction='apply', *, drain_seconds=600):
        c.require(direction in ('apply', 'rollback'), 'Invalid direction')
        c.require(self.boot() == self.plan['boot_id'], 'Boot changed; review required')
        self.host.immutable()
        for path, op in self.plan['inner']['operations'].items():
            allowed = list(op.values())
            if path in {c.SYSTEM_ROOT + n for n in (*c.SERVICES, *c.TIMERS)}:
                allowed.append(MASK)  # explicit transient rollback barrier
            c.require(self.fs.node(path) in allowed, 'Unrecognised configuration at resume: ' + path)
        for path, op in self.plan['enable_links'].items():
            c.require(self.fs.node(path) in op.values(), 'Unknown enable link: ' + path)
        if self.fs.node(self.t.name('progress.json'))['kind'] != 'absent':
            c.require(self.fs.json(self.t.name('plan.json')) == self.plan['inner'] and
                      self.fs.json(self.t.name('approval.json')) == self.t.approval, 'Recorded inner authority differs')
        if direction == 'apply':
            c.require(self.fs.node(self.path('rollback.json'))['kind'] == 'absent', 'Rolled-back plan cannot be reapplied')
        self.direction = direction
        if self.fs.node(self.path(direction + '.json'))['kind'] == 'absent':
            if direction == 'apply':
                c.require(self.fs.node(self.path('rollback.json'))['kind'] == 'absent', 'Rolled-back plan cannot be reapplied')
            else:
                c.require(self.fs.node(self.t.name('progress.json'))['kind'] != 'absent', 'Nothing to roll back')
            self.state = {'direction': direction, 'phase': 'new', 'cancelled': {}, 'start_requests': {}}
            self.fs.save(self.path('plan.json'), self.plan, immutable=True)
            self.fs.save(self.path('approval.json'), self.approval, immutable=True)
            self.save()
        else:
            self.state = self.fs.json(self.path(direction + '.json'))
            c.require(self.fs.json(self.path('plan.json')) == self.plan and self.fs.json(self.path('approval.json')) == self.approval, 'Recorded host approval differs')
        source, target = ('user', 'system') if direction == 'apply' else ('system', 'user')
        if not self.need('committed'):
            receipt = self.fs.json(self.t.name(direction + '-receipt.json'))
            c.require(self.fs.json(tx.LEDGER) == receipt['ledger_after'], 'Committed ledger drifted')
            side = 'after' if direction == 'apply' else 'before'
            c.require(all(self.fs.node(p) == op[side] for p, op in self.plan['inner']['operations'].items()), 'Committed files drifted')
        if self.need('installed'):
            if direction == 'apply':
                self.installer()
            self.phase('installed')
        if self.need('intent'):
            if direction == 'apply':
                c.require(self.host.clock_ready(), 'Strict readiness must pass before holding the old jobs')
                self.inner_intent()
            self.phase('intent')
        if self.need('held'):
            if direction == 'rollback':
                self.hold_source('user')  # partial apply may still have old user jobs
            self.hold_source(source)
            if direction == 'rollback':
                for path, operation in self.plan['enable_links'].items():
                    self.fs.write(path, operation['before'])
            self.phase('held')
        if self.need('drained'):
            if direction == 'rollback':
                self.drain('user', drain_seconds)
            self.drain(source, drain_seconds)
            self.phase('drained')
        if self.need('staged'):
            if direction == 'rollback':
                self.rollback_pending()
            self.t.stage(direction)
            for manager in ('user', 'system'):
                self.m.command(manager, 'daemon-reload')
            self.phase('staged')
        if self.need('committed'):
            self.state['commit'] = self.t.commit()
            self.phase('committed')
        if self.need('activated'):
            if direction == 'apply':
                for path, operation in self.plan['enable_links'].items():
                    self.fs.write(path, operation['after'])
            # Activation may catch up Persistent and OnActive. Existing task
            # ledgers and archive inventory decide work; no manual full daily.
            for name in c.TIMERS:
                self.request_start(target, name)
            for key in sorted(self.state['cancelled']):
                name = key.split(':', 1)[1]
                if name in c.SERVICES:
                    self.request_start(target, name)
            self.phase('activated')
        self.host.immutable()
        self.state['result'] = {'configuration': direction + '_committed',
            'operating_observation_pending': True, 'clock_control': False,
            'checkpoint_deletion': False, 'automatic_retirement': 'masked',
            'strict_policy_unchanged': True}
        self.phase('complete')
        return self.state

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=c.HERE / 'host-plan.json')
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--action', choices=('inspect', 'apply', 'rollback'), default='inspect')
    parser.add_argument('--drain-seconds', type=int, default=600)
    args = parser.parse_args()
    from handoff_inputs import load_checked, live_execution
    plan = load_checked(args.plan)
    if args.action == 'inspect':
        print(json.dumps({'plan_id': plan['plan_id'], 'release_id': plan['inner']['release_id'],
            'preflight_passed': bool(plan.get('preflight')), 'operating_approval': False,
            'configuration_operations': 46, 'additional_enable_links': 8,
            'barrier': 'persistent mask on source manager; normal work drain',
            'policy_changes': []}, indent=2))
        return
    live_execution(plan, args)

if __name__ == '__main__':
    main()
