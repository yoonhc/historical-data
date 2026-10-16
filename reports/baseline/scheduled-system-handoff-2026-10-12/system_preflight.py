"""Administrator preflight: temporary md-t15 units only, no operating mutation."""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_common as c
from handoff_inputs import load_checked, install_release

REUSE_REPORT = 'system-preflight-result-97f22b03bbf940c3.json'
REUSE_SHA256 = 'd1eee4235541062185ea19575c56da1410486be51003927466e9e6785732493b'
REUSE_TOOL = 'scheduled-handoff-20261012-781fc35b9032628c'

def lifecycle_unit(command, more='', owner='system'):
    c.require(owner in ('system', 'user'), 'Wrong trial manager')
    # User managers already run as UID 1000. Explicit User= can attempt an
    # unprivileged supplementary-group setup and fail with 216/GROUP.
    account = 'User=yoonhc\n' if owner == 'system' else ''
    return ('[Service]\nType=oneshot\n' + account +
            'ProtectClock=yes\nNoNewPrivileges=yes\nTimeoutStartSec=150\nTimeoutStopSec=10\n'
            'ExecStart=' + command + '\n' + more)

def completed_probe_evidence(before):
    """Reuse only the explicitly pinned successful parts of the second run."""
    import tarfile
    raw = (c.HERE / REUSE_REPORT).read_bytes()
    c.require(c.sha(raw) == REUSE_SHA256, 'Prior preflight evidence changed')
    prior = json.loads(raw)
    c.require(prior['tool_id'] == REUSE_TOOL and prior['after'] == before and
              prior['operating_unchanged'] and not prior['cleanup']['errors'] and
              prior['cleanup']['paths_absent'], 'Prior preflight does not cover current operating state')
    # Everything except this orchestration script is byte-identical. The old
    # script and seal remain available for review, not silently overwritten.
    with tarfile.open(c.HERE / 'tool-history' / (REUSE_TOOL + '.tar.gz')) as archive:
        seal = json.load(archive.extractfile('tool-seal.json'))
    for name, digest in seal['files'].items():
        if name != 'system_preflight.py':
            c.require(c.sha((c.HERE / name).read_bytes()) == digest, 'Shared preflight input changed: ' + name)
    value = prior['trials']
    keys = [*c.JOBS, 'system_enable_link', 'user_enable_link', 'timer_provenance', 'manual', 'persistent_catchup']
    for job in c.JOBS:
        r = value[job]['worker']
        c.require(r['uid'] == 1000 and r['coordinator_shared_read_lock'] and r['password_readable']
                  and value[job]['effective']['ProtectClock'] == 'yes', 'Missing prior access proof')
    for name in ('system_enable_link', 'user_enable_link'):
        c.require(value[name]['base_preserved'] and value[name]['link_removed'], 'Missing link proof')
    timer = value['timer_provenance']
    c.require(timer['context']['scheduled'] and not timer['wrong_unit']['scheduled']
              and not timer['wrong_timer']['scheduled'] and
              not value['manual']['worker']['context']['scheduled'] and
              value['persistent_catchup']['context']['scheduled'], 'Missing provenance proof')
    return {k: value[k] for k in keys}, {'report': str(c.HERE / REUSE_REPORT),
        'sha256': REUSE_SHA256, 'tool_id': REUSE_TOOL, 'groups': ['environments', 'links', 'provenance'],
        'repeated': False, 'failed_lifecycle_not_counted': True}

def access_trial_unit(plan, job, prefix, release, state):
    """Preserve final service settings; substitute ONLY the probe dependency/body.

    Empty Requires=/After= assignments do not remove earlier dependencies.
    Use an explicit nonce readiness unit, never the uninstalled operating unit.
    The production gate and workload remain unchanged in configuration-plan.
    """
    final_name = 'marketdata-' + job + '.service'
    text = plan['inner']['operations'][c.SYSTEM_ROOT + final_name]['after']['text']
    c.require('Requires=' + c.WAITER in text and 'After=' + c.WAITER in text,
              'Final service dependency differs')
    text = text.replace(c.WAITER, prefix + '-ready.service')
    actual = plan['inner']['topology']['units'][final_name]['ExecStart'][0]
    actual = actual.replace('/opt/marketdata/releases/' + plan['inner']['release_id'], str(release))
    python = actual.split()[0]
    text += ('\n[Service]\nExecStartPre=\nExecStart=\nExecStart=' + actual + ' --help\n'
             f'ExecStart={python} -B -I {release}/trial_worker.py access --state {state} --name {job}\n')
    for line in text.splitlines():
        if line.startswith(('Requires=', 'Wants=', 'After=', 'Before=', 'BindsTo=', 'PartOf=')):
            c.require(not any(n.startswith('marketdata-') for n in line.split('=', 1)[1].split()),
                      'Operating dependency leaked into trial')
    return text

def trial_diagnostics(created, manager):
    """Capture only registered trial units, before stopping/cleaning them."""
    diagnostics = {}
    for owner, name, _, _ in created:
        item = {}
        diagnostics[owner + ':' + name] = item
        try:
            item['properties'] = manager.show(owner, name)
            item['jobs'] = [j for j in manager.jobs(owner) if j['unit'] == name]
            item['result'] = manager.command(owner, 'show', name, '--no-pager',
                '--property=Result,ExecMainCode,ExecMainStatus', check=False).stdout
            argv = ['/usr/bin/journalctl', '--boot', '--no-pager', '--output=short-monotonic',
                    '--lines=50', '--unit=' + name] if owner == 'system' else c.user_command([
                    '/usr/bin/journalctl', '--user', '--boot', '--no-pager',
                    '--output=short-monotonic', '--lines=50', '--unit=' + name])
            r = subprocess.run(argv, capture_output=True, text=True, timeout=10)
            item['journal'] = {'code': r.returncode, 'stdout': r.stdout[-24000:], 'stderr': r.stderr[-2000:]}
        except Exception as exc:
            item['diagnostic_error'] = type(exc).__name__ + ': ' + str(exc)
    return diagnostics

def operating(plan, m):
    paths = set(plan['inner']['operations']) | set(plan['inner']['protected']) | set(plan['extra_protected']) | set(plan['enable_links'])
    paths.add('/var/lib/marketdata-clock/boot-state.json')
    paths.update((c.USER_ROOT if manager == 'user' else c.SYSTEM_ROOT) + name
                 for manager, name in (key.split(':', 1) for key in plan['inner']['retirement_masks']))
    nodes = {}
    for path in sorted(paths):
        p = Path(path)
        meta = c.metadata(p)
        if meta['kind'] == 'symlink':
            meta['target'] = os.readlink(p)
        elif meta['kind'] == 'file':
            meta['sha256'] = c.sha(p.read_bytes())
        nodes[path] = meta
    identities = {name: {key: m.show('system', name).get(key) for key in ('MainPID', 'InvocationID', 'ActiveState')}
                  for name in c.IDENTITIES}
    inventory = {manager: m.command(manager, 'list-unit-files', 'marketdata-*', '--no-legend', '--no-pager').stdout
                 for manager in ('user', 'system')}
    return {'boot_id': c.boot(), 'nodes': nodes, 'identities': identities, 'inventory': inventory}

def save_report(path, value):
    # Output is user-owned, O_EXCL and NOFOLLOW in a non-root process.
    script = 'import os,sys;fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644);f=os.fdopen(fd,"wb");f.write(sys.stdin.buffer.read());f.flush();os.fsync(f.fileno());f.close()'
    subprocess.run(c.user_command(['/usr/bin/python3', '-B', '-I', '-c', script, str(path)]),
                   input=c.encode(value), check=True, timeout=30)

def perform(plan, scenario='all'):
    c.require(os.geteuid() == 0, 'Administrator authentication required')
    c.require(c.boot() == plan['boot_id'], 'Boot changed; preflight input review required')
    nonce = uuid.uuid4().hex[:16]
    prefix = 'md-t15-' + nonce
    release, state = Path('/opt') / prefix, Path('/var/lib') / prefix
    created, stamps, events, outputs, enable_links = [], [], [], {}, []
    manager = c.Managers(record=events.append)
    from handoff import Host
    Host(plan, c.SafeFiles('/', (), {}), manager).immutable()
    before = operating(plan, manager)
    for path, operation in plan['inner']['operations'].items():
        p = Path(path)
        n = operation['before']
        if n['kind'] == 'absent':
            c.require(not p.exists() and not p.is_symlink(), 'Before configuration changed: ' + path)
        elif n['kind'] == 'symlink':
            c.require(p.is_symlink() and os.readlink(p) == n['target'], 'Before link changed: ' + path)
        else:
            c.require(c.sha(p.read_bytes()) == n['sha256'], 'Before configuration changed: ' + path)
    for path, digest in plan['extra_protected'].items():
        c.require(c.sha(Path(path).read_bytes()) == digest, 'Protected input changed: ' + path)
    result = {'passed': False, 'tool_id': plan['tool_id'], 'boot_id': c.boot(), 'nonce': nonce,
              'operating_changes_requested': False, 'before': before, 'events': events,
              'ownership_before': {p: c.metadata(p) for p in plan['inner']['operations']},
              'cleanup': {'errors': []}, 'trials': outputs}
    def write(path, raw, uid=0):
        c.require(not path.exists() and not path.is_symlink(), 'Trial path already exists')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'wb') as f:
            f.write(raw); f.flush(); os.fchown(f.fileno(), uid, uid); os.fsync(f.fileno())
    def unit(name, text, owner='system'):
        c.require(name.startswith(prefix + '-'), 'Wrong trial namespace')
        path = (Path('/run/systemd/system') if owner == 'system' else Path(c.USER_ROOT)) / name
        write(path, text.encode(), 0 if owner == 'system' else 1000)
        created.append((owner, name, path, text.encode()))
        manager.allowed.add((owner, name))
        return name
    def wait(test, label, seconds=30):
        until = time.monotonic() + seconds
        while not test():
            c.require(time.monotonic() < until, 'Trial timeout: ' + label)
            time.sleep(0.1)
    def worker(mode, name, python=None, extra=''):
        return f'{python or release / ".venv/bin/python"} -B -I {release}/trial_worker.py {mode} --state {state} --name {name} {extra}'
    def basic(command, more='', owner='system'):
        return lifecycle_unit(command, more, owner)
    def start_wait(owner, name, output):
        manager.command(owner, 'start', '--no-block', name)
        wait(lambda: (state / (output + '.json')).exists(), output)
        wait(lambda: c.classify(manager.show(owner, name), manager.jobs(owner)) == 'idle', name)
        props = manager.show(owner, name)
        data = json.loads((state / (output + '.json')).read_bytes())
        outputs[output] = {'worker': data, 'effective': props}
        return data
    try:
        print('installing sealed trial environment', flush=True)
        install_release(release)
        # Extra worker is explicitly test-only, separate from the fixed source.
        write(release / 'trial_worker.py', (c.HERE / 'trial_worker.py').read_bytes())
        state.mkdir(mode=0o700)
        os.chown(state, 1000, 1000)
        write(state / '.owner.json', c.encode({'nonce': nonce}), 1000)
        result['scenario'] = scenario
        if scenario == 'lifecycle':
            reused, result['reused_preflight'] = completed_probe_evidence(before)
            outputs.update(reused)
            print('reusing verified access, links and timer evidence', flush=True)
        else:
            # Each final service's protections/environment/limits, with ONLY its
            # business command/gate replaced by a harmless nonce probe dependency.
            print('checking eight final service environments', flush=True)
            t05 = Path('/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7')
            unit(prefix + '-ready.service', basic('/usr/bin/true', 'RemainAfterExit=yes\n'))
            for job in c.JOBS:
                text = access_trial_unit(plan, job, prefix, release, state)
                # No data requests: argparse --help exits before Store/HTTP.
                name = unit(prefix + '-' + job + '.service', text)
                manager.command('system', 'daemon-reload')
                data = start_wait('system', name, job)
                c.require(data['uid'] == 1000 and data['coordinator_shared_read_lock'] and data['password_readable'], 'Access probe failed')
                c.require(outputs[job]['effective']['ProtectClock'] == 'yes', 'Missing final ProtectClock')
                for field in ('Requires', 'Wants', 'After', 'Before', 'BindsTo', 'PartOf'):
                    c.require(not any(n.startswith('marketdata-') for n in outputs[job]['effective'].get(field, '').split()),
                              'Operating dependency in effective trial unit')
            # Exact enable links, mask replacement and restoration in both managers.
            # Never use disable, which can remove unrelated base links.
            for owner in ('system', 'user'):
                name = unit(prefix + '-' + owner + '-links.timer',
                    '[Timer]\nOnCalendar=2099-01-01 00:00:00 UTC\nUnit=' + prefix + '-oi.service\n[Install]\nWantedBy=timers.target\n', owner)
                base = next(x[2] for x in created if x[0] == owner and x[1] == name)
                root = c.SYSTEM_ROOT if owner == 'system' else c.USER_ROOT
                link = Path(root) / 'timers.target.wants' / name
                c.require(link.parent.is_dir() and not link.exists() and not link.is_symlink(), 'Trial enable path unavailable')
                fs = c.SafeFiles('/', [str(link)], {str(link): (0, 0) if owner == 'system' else (1000, 1000)})
                enable_links.append(link)
                fs.write(str(link), {'kind': 'symlink', 'target': str(base)})
                manager.command(owner, 'daemon-reload')
                linked = manager.show(owner, name)
                c.require(linked['UnitFileState'] in ('enabled', 'enabled-runtime'), 'Enable link not recognised')
                fs.write(str(link), {'kind': 'absent'})
                manager.command(owner, 'daemon-reload')
                c.require(base.is_file() and not link.exists() and not link.is_symlink(), 'Base link lost during enable rollback')
                outputs[owner + '_enable_link'] = {'enabled': linked, 'base_preserved': True, 'link_removed': True}
            print('checking real system timer provenance', flush=True)
            context_service, context_timer = prefix + '-context.service', prefix + '-context.timer'
            # Recreate just this trial service between invocations so outputs are immutable.
            def context(mode, timer_name=context_timer):
                text = basic(worker('context', mode, t05 / '.venv/bin/python',
                    f'--release {release} --service {context_service} --timer {timer_name}'))
                found = next((x for x in created if x[1] == context_service), None)
                if found:
                    found[2].unlink()
                    created.remove(found)
                unit(context_service, text)
            context('timer')
            unit(context_timer, '[Timer]\nOnActiveSec=1s\nAccuracySec=1ms\nUnit=' + context_service + '\n')
            manager.command('system', 'daemon-reload')
            manager.command('system', 'start', '--no-block', context_timer)
            wait(lambda: (state / 'timer.json').exists(), 'timer context')
            wait(lambda: c.classify(manager.show('system', context_service), manager.jobs('system')) == 'idle', 'context idle')
            timer = json.loads((state / 'timer.json').read_bytes())
            c.require(timer['context']['scheduled'] and not timer['wrong_unit']['scheduled'] and not timer['wrong_timer']['scheduled'], 'System timer attribution failed')
            outputs['timer_provenance'] = timer
            manager.command('system', 'stop', context_timer)
            context('manual')
            manager.command('system', 'daemon-reload')
            manual = start_wait('system', context_service, 'manual')
            c.require(not manual['context']['scheduled'], 'Manual start counted as timer')
            # Explicit missed-calendar Persistent activation, no operating stamps.
            persistent_name = prefix + '-persistent.timer'
            context('persistent', persistent_name)
            yesterday = datetime.now(UTC) - timedelta(days=1)
            unit(persistent_name, '[Timer]\nOnCalendar=' + yesterday.strftime('%Y-%m-%d 00:00:00 UTC') +
                 '\nPersistent=true\nAccuracySec=1ms\nUnit=' + context_service + '\n')
            stamp = Path('/var/lib/systemd/timers') / ('stamp-' + persistent_name)
            write(stamp, b'')
            os.utime(stamp, (yesterday.timestamp() - 86400, yesterday.timestamp() - 86400))
            stamps.append(stamp)
            manager.command('system', 'daemon-reload')
            manager.command('system', 'start', '--no-block', persistent_name)
            wait(lambda: (state / 'persistent.json').exists(), 'persistent catchup')
            outputs['persistent_catchup'] = json.loads((state / 'persistent.json').read_bytes())
            c.require(outputs['persistent_catchup']['context']['scheduled'], 'Persistent timer provenance failed')
            outputs['persistent_catchup']['timer'] = manager.show('system', persistent_name)
            manager.command('system', 'stop', persistent_name)
        print('checking both-manager drain, masks and common lock', flush=True)
        for owner in ('system', 'user'):
            suffix = owner
            busy = unit(prefix + '-' + suffix + '-busy.service', basic(worker('hold', suffix + '-busy'), owner=owner), owner)
            waiter = unit(prefix + '-' + suffix + '-wait.service', basic('/usr/bin/sleep 100', owner=owner), owner)
            pending = unit(prefix + '-' + suffix + '-queued.service',
                '[Unit]\nRequires=' + waiter + '\nAfter=' + waiter + '\n' + basic(worker('touch', suffix + '-queued'), owner=owner), owner)
            retry = unit(prefix + '-' + suffix + '-retry.service', basic(worker('fail', suffix + '-retry'), 'Restart=on-failure\nRestartSec=100s\n', owner), owner)
            other = 'user' if owner == 'system' else 'system'
            lock = unit(prefix + '-' + suffix + '-lock.service', basic(worker('lock-try', suffix + '-lock'), owner=other), other)
            manager.command(owner, 'daemon-reload'); manager.command(other, 'daemon-reload')
            manager.command(owner, 'start', '--no-block', busy, pending, retry)
            wait(lambda: (state / (suffix + '-busy-started.json')).exists(), 'busy start')
            wait(lambda: manager.show(owner, retry).get('SubState') == 'auto-restart', 'restart pending')
            states = manager.states(owner, (busy, pending, retry))
            c.require(states[busy]['classification'] == 'running' and states[busy]['properties']['ActiveState'] == 'activating', 'Oneshot classification failed')
            c.require(states[pending]['classification'] == 'pending' and states[retry]['classification'] == 'pending', 'Pending classification failed')
            data = start_wait(other, lock, suffix + '-lock')
            c.require(data['lock_conflict'], 'Shared cross-manager lock did not conflict')
            paths = {x[1]: x for x in created if x[0] == owner}
            fs = c.SafeFiles('/', [str(paths[n][2]) for n in (busy, pending, retry)],
                {str(paths[n][2]): ((1000, 1000) if owner == 'user' else (0, 0)) for n in (busy, pending, retry)})
            for name in (busy, pending, retry):
                fs.write(str(paths[name][2]), {'kind': 'symlink', 'target': '/dev/null'})
            manager.command(owner, 'daemon-reload')
            c.require(manager.show(owner, busy)['MainPID'] == states[busy]['properties']['MainPID'], 'Mask killed running work')
            for name in (pending, retry):
                current = manager.states(owner, (name,))[name]
                c.require(current['classification'] in ('pending', 'idle'), 'Pending turned into active body')
                if current['classification'] == 'pending':
                    manager.command(owner, 'stop', '--no-block', name)
            write(state / (suffix + '-busy-release'), b'', 1000)
            wait(lambda: (state / (suffix + '-busy.json')).exists(), 'normal work completion')
            wait(lambda: all(x['classification'] == 'idle' for x in manager.states(owner, (busy, pending, retry)).values()), 'drain complete')
            manager.command(owner, 'stop', '--no-block', waiter)  # never before work drains
            c.require(not (state / (suffix + '-queued.json')).exists(), 'Cancelled body executed')
            outputs[owner + '_drain'] = {'before': states, 'normal_completion': json.loads((state / (suffix + '-busy.json')).read_bytes()), 'queued_body_absent': True}
            for name in (busy, pending, retry):
                item = paths[name]
                fs.write(str(item[2]), {'kind': 'file', 'text': item[3].decode(), 'sha256': c.sha(item[3]), 'mode': 0o644})
            manager.command(owner, 'daemon-reload')
            c.require(all(manager.show(owner, n)['LoadState'] == 'loaded' for n in (busy, pending, retry)), 'Trial unit rollback failed')
        result['passed'] = True
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
        result['failure_diagnostics'] = trial_diagnostics(created, manager)
    finally:
        # Only registered nonce units are touched; no marketdata-* command.
        for owner, name, path, raw in reversed(created):
            try:
                manager.command(owner, 'stop', name, check=False)
                p = manager.show(owner, name)
                c.require(p.get('MainPID', '0') == '0' and p.get('ControlPID', '0') == '0', 'Trial process remains')
                manager.command(owner, 'reset-failed', name, check=False)
                path.unlink(missing_ok=True)
            except Exception as exc:
                result['cleanup']['errors'].append(name + ': ' + str(exc))
        for owner in ('user', 'system'):
            try:
                manager.command(owner, 'daemon-reload')
            except Exception as exc:
                result['cleanup']['errors'].append(str(exc))
        for path in stamps:
            path.unlink(missing_ok=True)
        for path in enable_links:
            path.unlink(missing_ok=True)
        if state.is_dir():
            result['worker_outputs'] = {p.name: json.loads(p.read_bytes()) for p in state.glob('*.json')}
        if not result['cleanup']['errors']:
            for path, parent in ((release, Path('/opt')), (state, Path('/var/lib'))):
                if path.exists():
                    c.require(path.parent == parent and path.name == prefix and not path.is_symlink(), 'Unsafe trial cleanup')
                    shutil.rmtree(path)
        result['cleanup']['unit_load_states'] = {owner + ':' + name: manager.show(owner, name).get('LoadState') for owner, name, _, _ in created}
        if any(v != 'not-found' for v in result['cleanup']['unit_load_states'].values()):
            result['cleanup']['errors'].append('Trial unit still loaded')
        result['cleanup']['paths_absent'] = not release.exists() and not state.exists() and all(not p.exists() and not p.is_symlink() for _, _, p, _ in created)
        result['after'] = operating(plan, manager)
        result['operating_unchanged'] = result['before'] == result['after']
        result['passed'] = result['passed'] and result['operating_unchanged'] and not result['cleanup']['errors'] and result['cleanup']['paths_absent']
        result['finished_at'] = datetime.now(UTC).isoformat()
        output = c.HERE / ('system-preflight-result-' + nonce + '.json')
        save_report(output, result)
        print(json.dumps({'passed': result['passed'], 'report': str(output),
              'operating_unchanged': result['operating_unchanged'], 'cleanup': result['cleanup']}, indent=2))
    return result['passed']

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', action='store_true')
    p.add_argument('--scenario', choices=('all', 'lifecycle'), default='all')
    args = p.parse_args()
    plan = load_checked(c.HERE / 'host-plan.json')
    if args.run:
        raise SystemExit(0 if perform(plan, args.scenario) else 1)
    print(json.dumps({'checked': True, 'tool_id': plan['tool_id'], 'operating_mutations': False, 'run_requires_sudo': True}))
