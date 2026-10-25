"""Authorize and start exactly one installed T05 plan; never delete files here.

The installed UID-1000 system service performs all data checks and retirement.
The coordinator is held SH; no maintenance/archive/checkpoint lock is held while
waiting for that service. An idle service permits restoration of manual.json.
An interrupted service is never restarted or granted resume authority here.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAN_ID = '31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08'
STATE = Path('/var/lib/marketdata-checkpoint-retirement')
CONFIG = Path('/etc/marketdata/checkpoint-clock-retirement')
UNIT = 'marketdata-checkpoint-retirement-apply@' + PLAN_ID + '.service'
ROOT_REPORTS = Path('/var/lib/marketdata-clock/t05-manual-applications') / PLAN_ID
UNIT_FILE = Path('/etc/systemd/system/marketdata-checkpoint-retirement-apply@.service')
FAILED_RUN = '4a61c943702c41bcb4b5e854ac098440'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_inputs():
    inputs = json.loads((HERE / 'inputs.json').read_bytes())
    require(inputs['plan_id'] == PLAN_ID, 'Unexpected plan')
    require(sha(Path(__file__).read_bytes()) == inputs['tool_sha256'], 'Tool changed')
    for item in inputs['files'].values():
        require(sha(Path(item['path']).read_bytes()) == item['sha256'], 'Input changed: ' + item['path'])
    spec = importlib.util.spec_from_file_location('reviewed_scope', inputs['files']['helper']['path'])
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return inputs, helper


def command(argv):
    done = subprocess.run(list(map(str, argv)), capture_output=True, text=True,
                          env=ENV, cwd='/', timeout=45)
    require(done.returncode == 0, 'Command failed: ' + done.stderr[-1000:] + done.stdout[-1000:])
    return done.stdout


def unit_state():
    raw = command(['/usr/bin/systemctl', '--system', 'show', UNIT,
                   '--property=Id,LoadState,ActiveState,SubState,MainPID,ControlPID,Job,InvocationID,'
                   'FragmentPath,DropInPaths,NeedDaemonReload,User,Group,ExecStart,Result,ExecMainStatus'])
    result = {}
    for line in raw.splitlines():
        key, value = line.split('=', 1)
        require(key not in result, 'Duplicate scalar unit field')
        result[key] = value
    return result


def idle(row):
    return (row['ActiveState'] in ('inactive', 'failed') and row['MainPID'] == '0'
            and row['ControlPID'] == '0' and row['Job'] in ('', '0'))


def runs():
    return {p.parent.name: p for p in (STATE / 'runs').glob('*/run.json')}


def registry():
    return {str(p): sha(p.read_bytes()) for p in sorted((STATE / 'evidence').rglob('*.json'))}


@contextmanager
def runner_idle():
    fd = os.open(STATE / 'runner/runner.lock', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def manual_proposal(helper):
    argv = [helper.RELEASE / '.venv/bin/python', '-B', '-I',
            helper.RELEASE / 'scripts/run_clock_retirement.py', '--settings',
            CONFIG / 'settings.toml', 'propose-manual', '--plan-id', PLAN_ID]
    if os.geteuid() == 0:
        argv = ['/usr/sbin/runuser', '-u', 'yoonhc', '--', *argv]
    return json.loads(command(argv))


def validate_grant(grant, expected):
    require(grant == expected, 'Current exact-plan proposal changed')
    require(grant['enabled'] is False and grant['approved_at'] is None
            and grant['approval_reference'] is None, 'Proposal already enabled')
    require(grant['grant_kind'] == 'exact_plan' and grant['action'] == 'apply-plan'
            and grant['plan_id'] == PLAN_ID and grant['resume_interrupted'] is False,
            'Not a first exact-plan application')


def reviewed_predelete_failure(value):
    require(value.get('run_id') == FAILED_RUN and value.get('plan_id') == PLAN_ID
            and value.get('action') == 'apply-plan' and value.get('state') == 'deferred'
            and value.get('exit_code') == 75 and value.get('reason') == 'ClockHold'
            and value.get('detail') == 'Untrusted clock/approval path: ' + str(UNIT_FILE)
            and value.get('deletion_attempted') is False
            and value.get('selection_assessed') is False
            and value.get('deleted_this_run') == 0 and value.get('deleted_checkpoints') == 0,
            'Prior attempt is not the reviewed pre-deletion unit-link refusal')


def installed_unit_check(helper):
    """Run the actual installed guard as UID 1000 before enabling any grant."""
    code = ('import sys; from pathlib import Path; '
            'p=Path(' + repr(str(helper.RELEASE)) + '); '
            'sys.path[:0]=[str(p),str(p/"packages/marketdata/src")]; '
            'from scripts import clock_retirement as r; '
            'r.installed_service(r.Settings.load(' + repr(str(CONFIG/'settings.toml')) + '),'
            + repr(UNIT) + ',"apply-plan"); print("installed_unit_guard_passed")')
    argv = [helper.RELEASE/'.venv/bin/python', '-B', '-I', '-c', code]
    if os.geteuid() == 0:
        argv = ['/usr/sbin/runuser', '-u', 'yoonhc', '--', *argv]
    require(command(argv).strip() == 'installed_unit_guard_passed', 'Installed unit guard failed')


def preflight(inputs, helper):
    scope_plan = json.loads(Path(inputs['files']['scope_plan']['path']).read_bytes())
    helper.validate_plan(scope_plan)
    live = helper.verify_live(scope_plan)
    require(live['position'] == 'after', 'Disabled recurring scope differs')
    baseline = json.loads(Path(inputs['files']['preview_after']['path']).read_bytes())
    require(helper.BOOT.read_text().strip() == baseline['boot_id'], 'Boot changed')
    for name in helper.PROCESS_UNITS:
        for key in ('MainPID', 'InvocationID', 'ActiveState'):
            require(live['units'][name][key] == baseline['units'][name][key], 'Protected process changed')
    plan_path = STATE / 'retention/plans' / (PLAN_ID + '.json')
    require(sha(plan_path.read_bytes()) == inputs['files']['preview_plan']['sha256'], 'Runtime plan changed')
    plan = json.loads(plan_path.read_bytes())
    require(plan['plan_id'] == PLAN_ID and len(plan['candidates']) == 10
            and len(plan['keep']) == 3 and len(plan['excluded']) == 3, 'Selection differs')
    require(not (STATE / 'retention/journals' / (PLAN_ID + '.json')).exists(),
            'Existing journal requires separate review; no automatic retry')
    for path in runs().values():
        old = json.loads(path.read_bytes())
        if old.get('action') == 'apply-plan' and old.get('plan_id') == PLAN_ID:
            reviewed_predelete_failure(old)
            require(sha(path.read_bytes()) == inputs['files']['failed_run']['sha256'],
                    'Reviewed failed run changed')
    expected = json.loads(Path(inputs['files']['proposal']['path']).read_bytes())
    validate_grant(manual_proposal(helper), expected)
    row = unit_state()
    require(row['Id'] == UNIT and row['LoadState'] == 'loaded' and idle(row), 'Apply unit is not idle')
    fragment = CONFIG / 'units/marketdata-checkpoint-retirement-apply@.service'
    require(Path(row['FragmentPath']) == UNIT_FILE
            and not UNIT_FILE.is_symlink() and UNIT_FILE.read_bytes() == fragment.read_bytes()
            and row['DropInPaths'] == '' and row['NeedDaemonReload'] == 'no'
            and row['User'] == 'yoonhc' and row['Group'] == 'yoonhc'
            and '--plan-id ' + PLAN_ID in row['ExecStart'], 'Effective apply unit differs')
    installed_unit_check(helper)
    current_registry = registry()
    require(all(current_registry.get(p) == h for p, h in baseline['registry_files'].items()),
            'Existing completion/order evidence changed')
    return {'checked_at': datetime.now(timezone.utc).isoformat(), 'unit': row,
            'protected_processes': live['units'], 'registry': current_registry,
            'runs': sorted(runs()), 'plan': plan, 'proposal': expected,
            'manual_before': {'text': helper.MANUAL.read_text(),
                              'fingerprint': helper.fingerprint(helper.MANUAL)},
            'protected': {**baseline['protected'], str(UNIT_FILE):helper.fingerprint(UNIT_FILE)},
            'recurring': helper.fingerprint(helper.TARGET)}


def enabled_grant(proposal, approved_at, reference):
    validate_grant(proposal, proposal)
    require(reference and approved_at, 'Missing explicit approval record')
    return {**proposal, 'enabled': True, 'approved_at': approved_at, 'approval_reference': reference}


def wait_result(before_runs, record):
    deadline = time.monotonic() + 7500
    previous = None
    while True:
        row = unit_state()
        added = set(runs()) - set(before_runs)
        found = []
        for rid in added:
            value = json.loads(runs()[rid].read_bytes())
            if value.get('action') == 'apply-plan' and value.get('plan_id') == PLAN_ID:
                found.append(value)
        require(len(found) <= 1, 'Multiple application runs; do not repeat')
        value = found[0] if found else None
        marker = (row['ActiveState'], value.get('phase') if value else None)
        if marker != previous:
            print(json.dumps({'phase': 'waiting_for_installed_service', 'unit_state': marker[0],
                              'run_phase': marker[1], 'run_id': value.get('run_id') if value else None}), flush=True)
            previous = marker
        record['last_unit'] = row
        if idle(row):
            require(value is not None, 'Service ended without a run report')
            require(value['state'] != 'running', 'Service ended with an unfinished report')
            return value
        require(time.monotonic() < deadline, 'Wait limit reached; service not stopped or retried')
        time.sleep(3)


def restore_when_idle(helper, original, enabled, state_reader=unit_state):
    require(idle(state_reader()), 'Service still active; preserve exact grant and inspect')
    current = helper.fingerprint(helper.MANUAL)
    if current == original['fingerprint']:
        return 'already_disabled'
    require(current == enabled['fingerprint'], 'Manual grant changed; do not overwrite')
    helper.replace_checked(helper.MANUAL, enabled['fingerprint'], original,
                           lambda: require(idle(state_reader()), 'Service became active'))
    return 'original_disabled_restored'


def operate(inputs, helper, before):
    require(os.geteuid() == 0, 'Root required for manual grant and system service start')
    attempt = ROOT_REPORTS / uuid.uuid4().hex
    attempt.mkdir(parents=True, mode=0o755)
    helper.trusted_directory(attempt)
    for label, item in inputs['files'].items():
        helper.write_new(attempt / (label + Path(item['path']).suffix), Path(item['path']).read_bytes())
    helper.write_new(attempt / 'manual_apply.py', Path(__file__).read_bytes())
    helper.write_new(attempt / 'inputs.json', (HERE / 'inputs.json').read_bytes())
    helper.write_new(attempt / 'before.json', helper.blob(before))
    approval = enabled_grant(before['proposal'], datetime.now(timezone.utc).isoformat(),
                             str(attempt / 'authorization.md'))
    raw = helper.blob(approval)
    enabled = {'text': raw.decode(), 'fingerprint': {**before['manual_before']['fingerprint'], 'sha256': sha(raw)}}
    helper.write_new(attempt / 'manual-enabled.json', raw)
    result = {'plan_id': PLAN_ID, 'started_at': datetime.now(timezone.utc).isoformat(),
              'passed': False, 'service_start_requested': False, 'automatic_retirement': 'masked',
              'clock_control': False, 'recurring_changed': False, 'phase': 'before_manual_grant',
              'report': str(attempt / 'result.json')}
    installed = False
    try:
        with runner_idle():
            preflight(inputs, helper)
            helper.replace_checked(helper.MANUAL, before['manual_before']['fingerprint'], enabled,
                                   lambda: preflight(inputs, helper))
            installed = True
        result['phase'] = 'manual_grant_installed'
        print('starting exact-plan installed system service', flush=True)
        result['service_start_requested'] = True
        command(['/usr/bin/systemctl', '--system', '--no-block', 'start', UNIT])
        run = wait_result(before['runs'], result)
        result.update(run_id=run['run_id'], run_state=run['state'], run_exit_code=run['exit_code'],
                      deleted_this_run=run['deleted_this_run'], phase='service_finished')
        helper.write_new(attempt / 'run.json', (STATE / 'runs' / run['run_id'] / 'run.json').read_bytes())
        require(run['ok'] and run['state'] == 'complete' and run['exit_code'] == 0
                and run['deleted_this_run'] == 10 and not run['already_complete'],
                'Application deferred/failed or count differs; inspect original report')
        journal_path = STATE / 'retention/journals' / (PLAN_ID + '.json')
        journal = json.loads(journal_path.read_bytes())
        helper.write_new(attempt / 'journal.json', journal_path.read_bytes())
        require(journal['status'] == 'complete' and len(journal['items']) == 10
                and all(i['phase'] == 'complete' for i in journal['items'].values()), 'Journal incomplete')
        for candidate in before['plan']['candidates']:
            require(not Path(candidate['entry']['path']).exists(), 'Candidate remains')
        for entry in before['plan']['keep'] + before['plan']['excluded']:
            require(sha((Path(entry['path']) / 'backup.json').read_bytes()) == entry['manifest_sha256'],
                    'Retained/excluded manifest changed')
        result['passed'] = True
    except BaseException as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        try:
            if installed or helper.fingerprint(helper.MANUAL) != before['manual_before']['fingerprint']:
                result['manual_finalization'] = restore_when_idle(helper, before['manual_before'], enabled)
            after_registry = registry()
            require(all(after_registry.get(p) == h for p, h in before['registry'].items()), 'Evidence changed')
            for path, expected in before['protected'].items():
                require(helper.fingerprint(path) == expected, 'Protected input changed: ' + path)
            require(helper.fingerprint(helper.TARGET) == before['recurring'], 'Recurring grant changed')
            after_units = helper.manager()
            for name in helper.PROCESS_UNITS:
                for key in ('MainPID', 'InvocationID', 'ActiveState'):
                    require(after_units[name][key] == before['protected_processes'][name][key], 'Protected process changed')
            helper.write_new(attempt / 'after.json', helper.blob({'units':after_units, 'registry':after_registry,
                'manual':helper.fingerprint(helper.MANUAL), 'recurring':helper.fingerprint(helper.TARGET)}))
            result['protected_inputs_and_processes_unchanged'] = True
        except BaseException as exc:
            result['passed'] = False
            result['finalization_error'] = f'{type(exc).__name__}: {exc}'
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        helper.write_new(attempt / 'result.json', helper.blob(result))
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--approve-plan')
    args = parser.parse_args()
    inputs, helper = load_inputs()
    require(not args.run or args.approve_plan == PLAN_ID, 'Exact approved plan ID required')
    with helper.locks():
        with runner_idle():
            before = preflight(inputs, helper)
        if args.run:
            return operate(inputs, helper, before)
        print(json.dumps({'passed':True, 'plan_id':PLAN_ID, 'candidates':10, 'retained':3,
                          'excluded':3, 'scope_id':before['proposal']['scope_id'],
                          'readonly':True, 'manual_enabled':False, 'automatic_retirement':'masked'}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
