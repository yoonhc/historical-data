"""Install the approved immutable releases, then invoke the frozen transaction.

Only the two collector instances are stopped/started. No clock/package control,
deletion, bootstrap enrollment, or timer changes. A failed run keeps its journal
and requires review before another invocation. --validate has no operating writes.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tarfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUTS = HERE / 'install-inputs.json'
PLAN_ID = '22706e571f3a46a8745803b18bca2efacc3318c3e05b2309df6f3c7c37c621e6'
RELEASE_ID = 'clock-evidence-policy-20261009-65296619a374c325'
TOOL_ID = 'collector-transition-20261009-1c7c4120369fd893'
RELEASE = Path('/opt/marketdata/releases') / RELEASE_ID
TOOL = Path('/opt/marketdata/tools') / TOOL_ID
OPS = Path('/var/lib/marketdata-clock/runtime-approvals') / PLAN_ID
PROFILE = '/etc/marketdata/clock-bootstrap.json'
UNITS = ['marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service']
USER_PREFIX = ['/usr/sbin/runuser', '-u', 'yoonhc', '--', '/usr/bin/env',
               'XDG_RUNTIME_DIR=/run/user/1000', 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus']
EVENTS = []
PHASE = 'validation'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp():
    return {'utc': datetime.now(timezone.utc).isoformat(),
            'boottime_ns': time.clock_gettime_ns(time.CLOCK_BOOTTIME)}


def event(kind, **fields):
    row = {'sample': stamp(), 'phase': PHASE, 'kind': kind, **fields}
    EVENTS.append(row)
    if OPS.exists():
        with (OPS / 'operations.jsonl').open('a') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())


def command(argv, *, timeout=30, allowed=(0,), cwd=None, input_text=None):
    argv = [str(x) for x in argv]
    event('command_intent', argv=argv)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('PYTHON', 'RESTIC_', 'UV_'))}
    env.update(PATH='/usr/sbin:/usr/bin:/sbin:/bin', LC_ALL='C',
               PYTHONDONTWRITEBYTECODE='1', SYSTEMD_COLORS='0', UV_PYTHON_DOWNLOADS='never')
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                       cwd=cwd, env=env, input=input_text)
    event('command_result', argv=argv, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
    if p.returncode not in allowed:
        raise RuntimeError(f'Command failed ({p.returncode}): {argv}\n{p.stderr[-2500:]}')
    return p


def new_file(path, content, mode=0o644):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    path.chmod(mode)


def properties(text):
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def show(unit):
    return properties(command(['/usr/bin/systemctl', 'show', unit, '--all', '--no-pager',
        '--property=ActiveState,SubState,MainPID,ControlPID,InvocationID,Result,ExecMainCode,ExecMainStatus,FragmentPath,DropInPaths,ExecStart']).stdout)


def module(path):
    spec = importlib.util.spec_from_file_location('approved_transition', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def validate():
    inputs = json.loads(INPUTS.read_text())
    for name, expected in inputs['files'].items():
        if sha(name) != expected:
            raise RuntimeError('Reviewed deployment input changed: ' + name)
    plan = json.loads(Path(inputs['plan']).read_text())
    approval = json.loads(Path(inputs['approval']).read_text())
    assert plan['plan_id'] == PLAN_ID and approval['plan_id'] == PLAN_ID
    assert approval['approved'] is True
    assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == plan['boot_id']
    assert json.loads(Path('/var/lib/marketdata-clock/boot-state.json').read_text()) == plan['ledger_before']
    for role, f in plan['files'].items():
        assert sha(f['path']) == f['before_sha256'], role
    bootstrap = json.loads(plan['files']['bootstrap']['before'])
    for name, expected in bootstrap['guard_files'].items():
        assert sha(name) == expected, name
    for name, mode in bootstrap['user_units'].items():
        if mode == 'masked':
            p = Path('/home/yoonhc/.config/systemd/user') / name
            assert p.is_symlink() and p.readlink() == Path('/dev/null'), name
    for path in (RELEASE, TOOL, OPS):
        assert not path.exists() and not path.is_symlink(), f'Existing installation requires review: {path}'
    tx = Path(plan['state_dir']) / 'runtime-transitions'
    assert not tx.exists() or not any(tx.iterdir()), 'Existing transaction requires review'
    return inputs, plan, approval


def extract(archive, target, expected, candidate_sha):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    target.mkdir(mode=0o755)
    seen = set()
    with tarfile.open(archive, 'r:gz') as tf:
        for m in tf:
            assert m.isfile() and not Path(m.name).is_absolute() and '..' not in Path(m.name).parts
            assert m.name not in seen and m.name in {*expected, 'candidate.json'}
            blob = tf.extractfile(m).read()
            assert hashlib.sha256(blob).hexdigest() == (candidate_sha if m.name == 'candidate.json' else expected[m.name])
            dest = target / m.name
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            new_file(dest, blob)
            seen.add(m.name)
    assert seen == {*expected, 'candidate.json'}


def snapshot(plan):
    b = json.loads(plan['files']['bootstrap']['before'])
    paths = set(b['guard_files']) | {f['path'] for f in plan['files'].values()}
    paths |= {'/etc/chrony/marketdata.conf', '/etc/adjtime', '/var/lib/marketdata-clock/boot-state.json'}
    masks = {n: os.readlink(Path('/home/yoonhc/.config/systemd/user') / n)
             for n, mode in b['user_units'].items() if mode == 'masked'}
    return {'sample': stamp(), 'files': {p: sha(p) for p in sorted(paths)}, 'masks': masks,
            'chrony': show('chrony.service'), 'collectors': {u: show(u) for u in UNITS}}


def jobs_idle(bootstrap):
    names = [n for n, mode in bootstrap['user_units'].items() if mode in ('guarded', 'masked') and n.endswith('.service')]
    out = command(USER_PREFIX + ['/usr/bin/systemctl', '--user', 'show', *names, '--all',
        '--property=Id,ActiveState,MainPID,ControlPID'], timeout=15).stdout
    rows = [properties(x) for x in out.strip().split('\n\n')]
    assert {r.get('Id') for r in rows} == set(names)
    return [r for r in rows if r.get('ActiveState') not in ('inactive', 'failed')
            or r.get('MainPID') != '0' or r.get('ControlPID') != '0']


def install(inputs, plan):
    global PHASE
    PHASE = 'install_fixed_releases'
    print(PHASE, flush=True)
    collector = json.loads(Path(inputs['collector_metadata']).read_text())
    helper = json.loads(Path(inputs['tool_metadata']).read_text())
    extract(inputs['collector_archive'], RELEASE, collector['source_files_sha256'],
            plan['release_pins']['after']['candidate_json_sha256'])
    extract(inputs['tool_archive'], TOOL, helper['files'], inputs['tool_candidate_sha256'])
    new_file(OPS / 'uv', Path(inputs['uv']).read_bytes(), 0o755)
    # Private cache, copy mode: root never updates the developer cache or links
    # writable cache contents into the sealed release.
    shutil.copytree(inputs['uv_cache'], OPS / 'uv-cache', symlinks=True)
    command([OPS / 'uv', 'sync', '--frozen', '--no-editable', '--offline',
             '--link-mode', 'copy', '--python', '/usr/bin/python3', '--cache-dir', OPS / 'uv-cache',
             '--reinstall-package', 'marketdata', '--reinstall-package', 'marketdata-freqtrade'],
            cwd=RELEASE, timeout=300)
    command([RELEASE / '.venv/bin/python', '-B', '-I', RELEASE / 'integrations/runtime/seal_runtime.py'])
    sealed = json.loads((RELEASE / 'runtime-manifest.json').read_text())
    tested = json.loads(Path(inputs['tested_manifest']).read_text())
    for key in ('python', 'release_id', 'editable', 'runtime_files_sha256', 'distributions'):
        assert sealed[key] == tested[key], 'Installation differs from tested environment: ' + key
    mod = module(TOOL / 'work/collector_transition.py')
    mod.validate_plan(plan)
    runtime = json.loads(plan['files']['runtime']['after'])
    mod.check_release(runtime, 'after')
    # Verify the final UID/import/mount/DNS before interrupting current collectors.
    new_file(OPS / 'runtime-after.json', plan['files']['runtime']['after'].encode())
    code = "import importlib.util,json,sys; from pathlib import Path; r=Path(sys.argv[1]); s=importlib.util.spec_from_file_location('rt',r/'integrations/runtime/marketdata_runtime.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps({'release_id':m.environment_check(m.load_profile(sys.argv[2]))}))"
    check = json.loads(command(USER_PREFIX[:4] + [str(RELEASE / '.venv/bin/python'), '-B', '-I', '-c', code,
                                                str(RELEASE), str(OPS / 'runtime-after.json')]).stdout)
    assert check['release_id'] == RELEASE_ID
    (OPS / 'units').mkdir()
    new_file(OPS / 'units/marketdata-collector@.service', plan['files']['collector_unit']['after'].encode())
    command(['/usr/bin/systemd-analyze', 'verify', str(OPS / 'units/marketdata-collector@.service')])
    return mod


def apply(inputs, plan, mod):
    global PHASE
    bootstrap, runtime, _, _ = mod.validate_plan(plan)
    PHASE = 'wait_before_stopping_collectors'
    print(PHASE, flush=True)
    deadline = time.monotonic() + 300
    while True:
        current = mod.rt.probe(runtime)
        busy = jobs_idle(bootstrap)
        event('pre_stop_readiness', clock=current, busy=busy)
        if current['ready'] and not busy:
            mod.gate.validated_gate(PROFILE)
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Current preparation/jobs still not ready; collectors were NOT stopped')
        time.sleep(5)
    PHASE = 'normal_collector_stop'
    print(PHASE, flush=True)
    command(['/usr/bin/systemctl', 'stop', *UNITS], timeout=180)
    stopped = {u: show(u) for u in UNITS}
    assert all(s['MainPID'] == '0' and s['ControlPID'] == '0' and s['ActiveState'] in ('inactive', 'failed') for s in stopped.values())
    assert all(s['Result'] not in ('timeout', 'watchdog', 'signal', 'core-dump') for s in stopped.values()), stopped
    event('collectors_stopped', units=stopped)
    tool = ['/usr/bin/python3', '-B', '-I', str(TOOL / 'work/collector_transition.py')]
    args = ['--plan', str(OPS / 'plan.json'), '--approval', str(OPS / 'approval.json')]
    PHASE = 'stage_configuration'
    # A scheduled job may have started between the pre-stop observation and
    # taking the transaction lock. Retry only the unchanged tool's busy hold;
    # do not stop that job or reinterpret other transition failures.
    stage_deadline = time.monotonic() + 90
    while True:
        staged = command(tool + ['stage', *args], timeout=60, allowed=(0, 75))
        if staged.returncode == 0:
            break
        hold = json.loads(staged.stderr).get('hold', '')
        if not any(x in hold for x in ('Job is not quiescent:', 'Another bootstrap owns the lock')):
            raise RuntimeError('Transition stage refused: ' + hold)
        if time.monotonic() >= stage_deadline:
            raise RuntimeError('Scheduled work still busy; transition hold preserved')
        time.sleep(2)
    PHASE = 'activate_configuration'
    command(['/usr/bin/systemctl', 'daemon-reload'])
    committed = json.loads(command(tool + ['commit', *args], timeout=60).stdout)
    assert committed['configuration'] == 'apply_committed' and committed['step_reopened'] is False
    event('configuration_committed', result=committed)
    PHASE = 'start_new_collectors'
    # Configuration commit is not a claim of current readiness. The real service
    # ExecStartPre gates decide again and retry if preparation changed meanwhile.
    command(['/usr/bin/systemctl', 'start', '--no-block', *UNITS])
    return committed


def main():
    global PHASE
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--validate', action='store_true')
    g.add_argument('--run', action='store_true')
    a = ap.parse_args()
    inputs, plan, approval = validate()
    if a.validate:
        print(json.dumps({'inputs_valid': True, 'plan_id': PLAN_ID, 'release_id': RELEASE_ID,
                          'operating_writes': False}))
        return
    assert os.geteuid() == 0, 'Administrator authentication is required'
    mod = module(Path(inputs['reviewed_helper']))
    mod.validate_plan(plan)
    for f in plan['files'].values():
        mod.gate.trusted(f['path'])
        assert Path(f['path']).stat().st_uid == 0 and stat.S_IMODE(Path(f['path']).stat().st_mode) == f['mode']
    before = snapshot(plan)
    assert before['chrony']['InvocationID'] == inputs['expected_provider_invocation'], 'Provider changed before deployment'
    OPS.parent.mkdir(exist_ok=True, mode=0o755)
    mod.gate.trusted(OPS.parent, directory=True)
    OPS.mkdir(mode=0o755)
    new_file(OPS / 'installer.py', Path(__file__).read_bytes())
    new_file(OPS / 'inputs.json', INPUTS.read_bytes())
    new_file(OPS / 'plan.json', Path(inputs['plan']).read_bytes())
    new_file(OPS / 'approval.json', Path(inputs['approval']).read_bytes())
    new_file(OPS / 'before.json', (json.dumps(before, indent=2) + '\n').encode())
    result = {'release_id': RELEASE_ID, 'tool_id': TOOL_ID, 'plan_id': PLAN_ID,
              'passed': False, 'started_at': stamp(), 'automatic_checkpoint_retirement': 'masked',
              'scope': 'two collectors and approved configuration transaction only'}
    try:
        installed = install(inputs, plan)
        result['commit'] = apply(inputs, plan, installed)
        after = snapshot(plan)
        result['chrony_unchanged'] = all(after['chrony'][k] == before['chrony'][k] for k in ('MainPID', 'InvocationID'))
        assert result['chrony_unchanged']
        changed = {f['path'] for f in plan['files'].values()} | {'/var/lib/marketdata-clock/boot-state.json'}
        assert all(h == after['files'][p] for p, h in before['files'].items() if p not in changed)
        assert before['masks'] == after['masks']
        assert all(x == '/dev/null' for x in after['masks'].values())
        final_ledger = json.loads(Path('/var/lib/marketdata-clock/boot-state.json').read_text())
        assert all(final_ledger[k] == v for k, v in plan['ledger_before'].items()
                   if k not in ('profile_sha256', 'configuration_history'))
        result['original_boot_and_step_evidence_preserved'] = True
        result['other_configuration_and_masks_unchanged'] = True
        PHASE = 'applied_start_requested_observation_pending'
        result['passed'] = True
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        result['traceback'] = traceback.format_exc()
        event('failed', error=result['error'])
    finally:
        result.update(phase=PHASE, finished_at=stamp(), events=EVENTS,
                      after=snapshot(plan), reboot_performed=False, clock_control_performed=False)
        new_file(OPS / 'result.json', (json.dumps(result, indent=2) + '\n').encode())
        print(json.dumps({k: v for k, v in result.items() if k not in ('events', 'after', 'traceback')}, indent=2), flush=True)
        print('Root evidence: ' + str(OPS / 'result.json'), flush=True)
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
