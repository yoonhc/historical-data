"""Review-bound mount repair and one explicitly authorized interrupted-plan recovery.

Four configuration files change; the original retirement plan, journal,
inventories, code and checkpoint evidence are never rewritten by this tool.
The ordinary proposal generator remains strict. This separate, explicit scope
transition accepts only the frozen two-unit ReadWritePaths delta and one EXDEV
run. The installed engine performs every real validation and deletion.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAN_ID = '31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08'
RUN_ID = '5d7deaf59b1f4c99847a732327ce77af'
PURPOSE = 't05-reviewed-mount-scope-recovery-v1'
CONFIG = Path('/etc/marketdata/checkpoint-clock-retirement')
STATE = Path('/var/lib/marketdata-checkpoint-retirement')
UNIT_FILE = Path('/etc/systemd/system/marketdata-checkpoint-retirement-apply@.service')
PARENT = Path('/mnt/backup/marketdata-historical')
OPS = Path('/var/lib/marketdata-clock/t05-mount-repairs')
UNIT_NAMES = ('marketdata-checkpoint-retirement-apply@.service', 'marketdata-checkpoint-retirement.service')
SPLIT = 'ReadWritePaths=/mnt/backup/marketdata-historical/automatic /mnt/backup/marketdata-historical/.automatic.retiring\n'
COMMON = 'ReadWritePaths=/mnt/backup/marketdata-historical\n'


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def file_hash(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def validate_transition(plan):
    require(plan['purpose'] == PURPOSE and plan['retirement_plan_id'] == PLAN_ID
            and plan['reviewed_run_id'] == RUN_ID, 'Unreviewed recovery identity')
    changes = {row['path']: row for row in plan['changes']}
    expected_paths = {str(CONFIG / 'units' / n) for n in UNIT_NAMES} | {str(UNIT_FILE), str(CONFIG / 'recurring.json')}
    require(set(changes) == expected_paths and len(plan['changes']) == 4, 'Change set exceeds four files')
    old = plan['old_manual_proposal']
    require(old['scope_id'] == sha(canonical(old['scope'])) and old['plan_id'] == PLAN_ID
            and old['resume_interrupted'] is True and old['enabled'] is False
            and old['approved_at'] is None and old['approval_reference'] is None,
            'Original recovery proposal differs')
    expected_scope = copy.deepcopy(old['scope'])
    for name in UNIT_NAMES:
        change = changes[str(CONFIG / 'units' / name)]
        before, after = change['before']['text'], change['after']['text']
        require(before.count(SPLIT) == 1 and after == before.replace(SPLIT, COMMON), 'Unexpected unit change')
        require(old['scope']['system_units'][name] == sha(before.encode()), 'Old unit not bound to scope')
        expected_scope['system_units'][name] = sha(after.encode())
    require(changes[str(UNIT_FILE)]['before'] == changes[str(CONFIG / 'units' / UNIT_NAMES[0])]['before']
            and changes[str(UNIT_FILE)]['after'] == changes[str(CONFIG / 'units' / UNIT_NAMES[0])]['after'],
            'Installed and canonical manual unit differ')
    recurring_change = changes[str(CONFIG / 'recurring.json')]
    before_recurring = json.loads(recurring_change['before']['text'])
    after_recurring = json.loads(recurring_change['after']['text'])
    expected_recurring = copy.deepcopy(before_recurring)
    expected_recurring.update(scope=expected_scope, scope_id=sha(canonical(expected_scope)))
    require(before_recurring['scope'] == old['scope'] and after_recurring == expected_recurring
            and after_recurring['enabled'] is False and after_recurring['approved_at'] is None
            and after_recurring['approval_reference'] is None, 'Recurring change exceeds disabled scope')
    require(plan['after_scope_id'] == after_recurring['scope_id'], 'After scope differs')
    for change in plan['changes']:
        for side in ('before', 'after'):
            value = change[side]
            require(value['fingerprint'] == {'uid': 0, 'gid': 0, 'mode': 0o644, 'kind': 'file', 'nlink': 1,
                                             'sha256': sha(value['text'].encode())}, 'Unsealed configuration')


def load():
    plan = json.loads((HERE / 'plan.json').read_bytes())
    require(sha(canonical({k: v for k, v in plan.items() if k != 'operation_plan_id'})) == plan['operation_plan_id'], 'Operation plan changed')
    require(file_hash(Path(__file__)) == plan['tool_sha256'], 'Repair tool changed')
    for item in plan['files'].values():
        require(file_hash(item['path']) == item['sha256'], 'Reviewed input changed: ' + item['path'])
    validate_transition(plan)
    helper = module('approved_scope_helper', plan['files']['helper']['path'])
    app = module('approved_resume_helpers', plan['files']['resume_helpers']['path'])
    return plan, helper, app


def reviewed_journal(value, retirement):
    require(value['plan_id'] == PLAN_ID and value['identity'] == retirement['identity']
            and value['status'] == 'interrupted' and len(value['attempts']) == 1
            and value['attempts'][0]['ok'] is False
            and 'OSError: [Errno 18] Invalid cross-device link:' in value['attempts'][0]['error'],
            'Not the reviewed first-rename interruption')
    expected = {c['entry']['backup_id']: c for c in retirement['candidates']}
    require(set(value['items']) == set(expected) and len(expected) == 10, 'Journal selection differs')
    for bid, item in value['items'].items():
        require(item['phase'] == 'prepared' and item['unlinked_files'] == 0
                and {k: item[k] for k in expected[bid]} == expected[bid], 'Journal contains mutation or changed target')
        require(item['staged'] == str(PARENT / '.automatic.retiring/historical-data-original-v1' / PLAN_ID / bid),
                'Staging path changed')


def assert_original_work(plan, app):
    path = STATE / 'retention/plans' / (PLAN_ID + '.json')
    require(file_hash(path) == plan['retirement_plan_sha256'], 'Original plan changed')
    retirement = json.loads(path.read_bytes())
    require(retirement['automation']['scope_id'] == plan['old_manual_proposal']['scope_id']
            and retirement['code'] == plan['old_manual_proposal']['scope']['code'], 'Original code/scope connection changed')
    journal_path = STATE / 'retention/journals' / (PLAN_ID + '.json')
    require(file_hash(journal_path) == plan['journal_sha256'], 'Journal changed; new recovery review required')
    reviewed_journal(json.loads(journal_path.read_bytes()), retirement)
    seen = {}
    for rid, path in app.runs().items():
        value = json.loads(path.read_bytes())
        if value.get('plan_id') == PLAN_ID and value.get('action') in ('apply-plan', 'run'):
            seen[rid] = file_hash(path)
    require(seen == plan['reviewed_runs'], 'Additional/changed retirement run; do not repeat')
    app.untouched_roots(retirement)
    stage = PARENT / '.automatic.retiring/historical-data-original-v1' / PLAN_ID
    require(stage.is_dir() and not stage.is_symlink() and not any(stage.iterdir()), 'Stage is not the reviewed empty directory')
    for path, expected in plan['inventories'].items():
        require(file_hash(path) == expected, 'Original inventory changed')
    require({str(p) for p in (STATE / 'retention/inventories' / PLAN_ID).iterdir()} == set(plan['inventories']),
            'Inventory set changed')
    return retirement


def configuration_positions(plan, helper):
    result = {}
    for row in plan['changes']:
        actual = helper.fingerprint(row['path'])
        matches = [side for side in ('before', 'after') if actual == row[side]['fingerprint']]
        require(len(matches) == 1, 'Unknown configuration; do not overwrite: ' + row['path'])
        result[row['path']] = matches[0]
    return result


def verify_operating(plan, helper, app, *, manual_disabled=True):
    require(helper.BOOT.read_text().strip() == plan['boot_id'], 'Boot changed; review required')
    for path, expected in plan['protected'].items():
        require(helper.fingerprint(path) == expected, 'Protected input changed: ' + path)
    if manual_disabled:
        require(helper.fingerprint(helper.MANUAL) == plan['manual_before']['fingerprint'], 'Manual grant is not original disabled state')
    for p in helper.MASKS:
        require(p.is_symlink() and os.readlink(p) == '/dev/null', 'Automatic mask changed')
    require(not PARENT.is_symlink() and set(p.name for p in PARENT.iterdir()) == {'automatic', '.automatic.retiring'},
            'Common parent has unreviewed entries')
    for p in (PARENT / 'automatic', PARENT / '.automatic.retiring'):
        require(p.is_dir() and not p.is_symlink() and p.stat().st_dev == PARENT.stat().st_dev,
                'Backup boundary changed')
    registry = app.registry()
    require(all(registry.get(p) == h for p, h in plan['existing_registry'].items()), 'Existing evidence changed')
    units = helper.manager()
    for name, old in plan['protected_processes'].items():
        require(all(units[name][k] == old[k] for k in ('MainPID', 'InvocationID', 'ActiveState')), 'Protected process changed')
    return {'positions': configuration_positions(plan, helper), 'units': units,
            'registry_files': len(registry), 'existing_registry_preserved': len(plan['existing_registry'])}


def scope_transition_proposal(plan, retirement, actual_recurring):
    """Explicit one-plan approval bridge; not an automatic exception in the engine."""
    validate_transition(plan)
    expected = json.loads(next(c for c in plan['changes'] if c['path'] == str(CONFIG / 'recurring.json'))['after']['text'])
    require(actual_recurring == expected, 'Installed after scope differs')
    require(retirement['plan_id'] == PLAN_ID and sha(canonical({k: v for k, v in retirement.items() if k != 'plan_id'})) == PLAN_ID,
            'Original plan digest differs')
    require(retirement['automation']['scope_id'] == plan['old_manual_proposal']['scope_id']
            and retirement['code'] == expected['scope']['code'], 'Plan is not the reviewed old scope/code')
    value = copy.deepcopy(plan['old_manual_proposal'])
    value.update(scope=expected['scope'], scope_id=expected['scope_id'])
    value['scope_transition'] = {'purpose': PURPOSE, 'operation_plan_id': plan['operation_plan_id'],
        'original_plan_id': PLAN_ID, 'original_plan_sha256': plan['retirement_plan_sha256'],
        'before_scope_id': retirement['automation']['scope_id'], 'after_scope_id': expected['scope_id'],
        'reviewed_run_id': RUN_ID, 'reviewed_journal_sha256': plan['journal_sha256'],
        'authorization_semantics': 'explicit one-plan recovery under reviewed unit-only scope; original plan and history retained'}
    return value


def require_approval(action, plan, approved_operation, approved_retirement, approved_run):
    if action == 'check':
        return
    require(approved_operation == plan['operation_plan_id'], 'Exact configuration/recovery operation approval required')
    if action == 'apply':
        require(approved_retirement == PLAN_ID and approved_run == RUN_ID, 'Exact retirement plan and EXDEV recovery approval required')


def transition_files(plan, helper, destination, recheck):
    rows = plan['changes'] if destination == 'after' else list(reversed(plan['changes']))
    for row in rows:
        recheck()
        pos = configuration_positions(plan, helper)[row['path']]
        if pos != destination:
            helper.replace_checked(Path(row['path']), row[pos]['fingerprint'], row[destination], recheck)
    require(set(configuration_positions(plan, helper).values()) == {destination}, 'Incomplete configuration transition')


def wait_preservation(app, deadline_seconds=600):
    deadline = time.monotonic() + deadline_seconds
    previous = 0
    while True:
        try:
            return app.check_preservation_idle()
        except BlockingIOError as exc:
            reason = str(exc)
        except RuntimeError as exc:
            if not str(exc).startswith('Preservation work is busy:'):
                raise
            reason = str(exc)
        current = time.monotonic()
        require(current < deadline, 'Preservation remains busy; no service start requested')
        if current - previous >= 30:
            print(json.dumps({'phase': 'waiting_after_reload', 'reason': reason, 'manual_enabled': False}), flush=True)
            previous = current
        time.sleep(3)


def persist_state(helper, directory, value):
    path = directory / 'state.json'
    raw = helper.blob(value)
    if not path.exists():
        helper.write_new(path, raw)
    else:
        before = helper.fingerprint(path)
        helper.replace_checked(path, before, {'text': raw.decode(), 'fingerprint': {**before, 'sha256': sha(raw)}}, lambda: None)


def seal_operation(plan, helper):
    OPS.mkdir(mode=0o755, exist_ok=True)
    helper.trusted_directory(OPS)
    out = OPS / plan['operation_plan_id']
    out.mkdir(mode=0o755, exist_ok=True)
    helper.trusted_directory(out)
    payloads = {'plan.json': helper.blob(plan), 'repair_and_resume.py': Path(__file__).read_bytes(),
                'authorization.md': (HERE / 'authorization.md').read_bytes()}
    for name, value in payloads.items():
        target = out / name
        if target.exists():
            require(helper.fingerprint(target)['uid'] == 0 and target.read_bytes() == value, 'Sealed operation evidence changed')
        else:
            helper.write_new(target, value)
    return out


def completed_journal(plan, retirement, original, current):
    require(current['plan_id'] == PLAN_ID and current['identity'] == original['identity']
            and current['status'] == 'complete' and set(current['items']) == set(original['items']), 'Final journal differs')
    require(len(current['attempts']) == len(original['attempts']) + 1
            and current['attempts'][:-1] == original['attempts'] and current['attempts'][-1]['ok'] is True,
            'Original failure history changed or unexpected attempts')
    for bid, item in current['items'].items():
        old = original['items'][bid]
        require(item['phase'] == 'complete' and all(item[k] == v for k, v in old.items() if k not in ('phase', 'unlinked_files')),
                'Completed target differs from approved journal')
        require(not Path(item['entry']['path']).exists() and not Path(item['staged']).exists(), 'Retired path remains')
    for entry in retirement['keep'] + retirement['excluded']:
        require(file_hash(Path(entry['path']) / 'backup.json') == entry['manifest_sha256'], 'Retained/excluded manifest changed')
    for path, expected in plan['inventories'].items():
        require(file_hash(path) == expected, 'Inventory changed during recovery')


def finalize(plan, helper, app, out, record):
    enabled_path = out / 'manual-enabled.json'
    require(enabled_path.is_file(), 'Missing exact grant evidence')
    raw = enabled_path.read_bytes()
    enabled = {'text': raw.decode(), 'fingerprint': {**plan['manual_before']['fingerprint'], 'sha256': sha(raw)}}
    if not record.get('dispatch_intent'):
        # A crash may happen after enabling the exact grant but before recording
        # dispatch intent. Never start a worker from a finalization command.
        require(app.idle(app.unit_state()), 'Service active; inspect ambiguous pre-dispatch interruption')
        assert_original_work(plan, app)
        record['manual_finalization'] = app.restore_when_idle(helper, plan['manual_before'], enabled)
        verify_operating(plan, helper, app)
        record.update(passed=False, phase='cancelled_before_dispatch', cancelled_before_dispatch=True,
                      finished_at=datetime.now(timezone.utc).isoformat(), recovery_review_required=True)
        persist_state(helper, out, record)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 1
    try:
        run = app.wait_result(record['runs_before_dispatch'], record)
        record.update(run_id=run['run_id'], run_state=run['state'], run_exit_code=run['exit_code'],
                      deleted_this_run=run['deleted_this_run'], phase='service_finished')
        require(run['ok'] and run['state'] == 'complete' and run['exit_code'] == 0
                and run['deleted_this_run'] == 10 and not run['already_complete'], 'Recovery did not complete; inspect original run')
        retirement = json.loads((STATE / 'retention/plans' / (PLAN_ID + '.json')).read_bytes())
        original = json.loads(Path(plan['files']['journal_copy']['path']).read_bytes())
        current = json.loads((STATE / 'retention/journals' / (PLAN_ID + '.json')).read_bytes())
        completed_journal(plan, retirement, original, current)
        record['passed'] = True
    except BaseException as exc:
        record.update(passed=False, error=f'{type(exc).__name__}: {exc}')
    finally:
        try:
            record['manual_finalization'] = app.restore_when_idle(helper, plan['manual_before'], enabled)
            after = verify_operating(plan, helper, app)
            require(set(after['positions'].values()) == {'after'}, 'Configuration changed during service')
            require(file_hash(STATE / 'retention/plans' / (PLAN_ID + '.json')) == plan['retirement_plan_sha256'], 'Original plan changed')
            record['protected_inputs_and_processes_unchanged'] = True
        except BaseException as exc:
            record.update(passed=False, finalization_error=f'{type(exc).__name__}: {exc}')
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        persist_state(helper, out, record)
        raw = helper.blob(record)
        final_path = out / ('result-' + str(time.time_ns()) + '.json')
        helper.write_new(final_path, raw)
        print(json.dumps({**record, 'report': str(final_path)}, ensure_ascii=False, indent=2), flush=True)
    return 0 if record['passed'] else 1


def operate(plan, helper, app, action):
    with helper.locks():
        if action == 'finalize':
            out = OPS / plan['operation_plan_id']
            helper.trusted_directory(out)
            require((out / 'plan.json').read_bytes() == helper.blob(plan), 'Saved plan changed')
            record = json.loads((out / 'state.json').read_bytes())
            verify_operating(plan, helper, app, manual_disabled=False)
            return finalize(plan, helper, app, out, record)
        with app.runner_idle():
            current = verify_operating(plan, helper, app)
            require(app.idle(app.unit_state()), 'Apply service is active or queued')
            if action != 'rollback':
                retirement = assert_original_work(plan, app)
                if set(current['positions'].values()) == {'before'}:
                    require(app.manual_proposal(helper) == plan['old_manual_proposal'], 'Ordinary old-scope proposal changed')
                app.check_preservation_idle()
                clock = app.check_preview_anchor(helper, {'files': {'preview_plan': {'path': plan['files']['retirement_plan']['path']}}})
            if action == 'check':
                print(json.dumps({'passed': True, 'readonly': True, 'positions': current['positions'],
                                  'operation_plan_id': plan['operation_plan_id'], 'retirement_plan_id': PLAN_ID,
                                  'reviewed_run_id': RUN_ID, 'clock_ready': clock['ready'],
                                  'manual_enabled': False, 'automatic_retirement': 'masked'}, indent=2))
                return 0
            out = seal_operation(plan, helper)
            state_path = out / 'state.json'
            record = json.loads(state_path.read_bytes()) if state_path.exists() else {
                'operation_plan_id': plan['operation_plan_id'], 'retirement_plan_id': PLAN_ID, 'reviewed_run_id': RUN_ID,
                'started_at': datetime.now(timezone.utc).isoformat(), 'passed': False, 'dispatch_intent': False,
                'clock_control': False, 'automatic_retirement': 'masked', 'phase': 'before_configuration'}
            if action == 'rollback':
                transition_files(plan, helper, 'before', lambda: verify_operating(plan, helper, app))
                app.command(['/usr/bin/systemctl', '--system', 'daemon-reload'])
                require(verify_operating(plan, helper, app)['positions'] == {c['path']: 'before' for c in plan['changes']}, 'Rollback incomplete')
                record.update(configuration_rollback_at=datetime.now(timezone.utc).isoformat(), configuration='before',
                              rollback_preserved_all_work_records=True)
                persist_state(helper, out, record)
                print(json.dumps({'passed': True, 'configuration': 'before', 'work_records_preserved': True,
                                  'automatic_retirement': 'masked', 'service_start_requested': False}, indent=2))
                return 0
            require(record.get('dispatch_intent') is False and not record.get('cancelled_before_dispatch'),
                    'Operation consumed or dispatch recorded; inspect/finalize, never dispatch again')
            persist_state(helper, out, record)
            transition_files(plan, helper, 'after', lambda: verify_operating(plan, helper, app))
            record.update(configuration='after', phase='configuration_installed')
            persist_state(helper, out, record)
            if set(current['positions'].values()) != {'after'} or app.unit_state()['NeedDaemonReload'] == 'yes':
                app.command(['/usr/bin/systemctl', '--system', 'daemon-reload'])
            app.installed_unit_check(helper)
            actual = helper.proposal()
            proposal = scope_transition_proposal(plan, retirement, actual)
            record['after_scope_id'] = proposal['scope_id']
        # Never hold runner/maintenance/archive EX while waiting for a worker.
        wait_preservation(app)
        with app.runner_idle():
            verify_operating(plan, helper, app)
            require(set(configuration_positions(plan, helper).values()) == {'after'}, 'Configuration no longer after')
            assert_original_work(plan, app)
            app.installed_unit_check(helper)
            app.check_preservation_idle()
            proposal = scope_transition_proposal(plan, retirement, helper.proposal())
            enabled = app.enabled_grant(proposal, datetime.now(timezone.utc).isoformat(), str(out / 'authorization.md'))
            raw = helper.blob(enabled)
            target = out / 'manual-enabled.json'
            require(not target.exists(), 'Prior grant intent exists; explicit finalize/rollback review required')
            helper.write_new(target, raw)
            enabled_file = {'text': raw.decode(), 'fingerprint': {**plan['manual_before']['fingerprint'], 'sha256': sha(raw)}}
            record.update(phase='grant_intent', runs_before_dispatch=sorted(app.runs()))
            persist_state(helper, out, record)
            try:
                helper.replace_checked(helper.MANUAL, plan['manual_before']['fingerprint'], enabled_file,
                                       lambda: verify_operating(plan, helper, app))
                # Intent precedes the start command. Ambiguous termination is never retried.
                record.update(dispatch_intent=True, phase='dispatch_intent', service_start_requested=False)
                persist_state(helper, out, record)
            except BaseException:
                if app.idle(app.unit_state()):
                    app.restore_when_idle(helper, plan['manual_before'], enabled_file)
                raise
        try:
            app.command(['/usr/bin/systemctl', '--system', '--no-block', 'start', app.UNIT])
            record.update(service_start_requested=True, phase='service_requested')
            persist_state(helper, out, record)
        except BaseException as exc:
            record['start_command_error'] = f'{type(exc).__name__}: {exc}'
            persist_state(helper, out, record)
        return finalize(plan, helper, app, out, record)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--action', choices=('check', 'apply', 'finalize', 'rollback'), default='check')
    p.add_argument('--approve-operation')
    p.add_argument('--approve-retirement-plan')
    p.add_argument('--approve-recovery-run')
    args = p.parse_args()
    plan, helper, app = load()
    require_approval(args.action, plan, args.approve_operation, args.approve_retirement_plan, args.approve_recovery_run)
    require(args.action == 'check' or os.geteuid() == 0, 'Root authentication required')
    try:
        return operate(plan, helper, app, args.action)
    except BaseException as exc:
        error = {'passed': False, 'action': args.action, 'operation_plan_id': plan['operation_plan_id'],
                 'error': f'{type(exc).__name__}: {exc}', 'automatic_retirement': 'masked',
                 'automatic_retry': False, 'finished_at': datetime.now(timezone.utc).isoformat()}
        if args.action != 'check':
            out = OPS / plan['operation_plan_id']
            if out.is_dir():
                helper.trusted_directory(out)
                target = out / ('command-error-' + str(time.time_ns()) + '.json')
                helper.write_new(target, helper.blob(error))
                error['report'] = str(target)
        print(json.dumps(error, ensure_ascii=False, indent=2), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
