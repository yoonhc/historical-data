"""One reviewed pre-lock Busy recovery with no installation or daemon-reload."""
import argparse
import copy
import importlib.util
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUSY_RUN = '2d9f7b8b4a434183a875dfa441171295'
TIMER_MARGIN = 60.0


def load():
    spec = importlib.util.spec_from_file_location('frozen_mount_recovery', HERE / 'repair_and_resume.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    base, helper, app = m.load()
    contract = json.loads((HERE / 'resume-only-plan.json').read_bytes())
    m.require(contract['operation_plan_id'] == m.sha(m.canonical({k:v for k,v in contract.items() if k != 'operation_plan_id'})), 'Recovery contract changed')
    m.require(contract['based_on_operation'] == base['operation_plan_id'] and contract['retirement_plan_id'] == m.PLAN_ID
              and contract['reviewed_busy_run_id'] == BUSY_RUN and contract['timer_start_margin_seconds'] == TIMER_MARGIN,
              'Unreviewed recovery boundary')
    m.require(m.file_hash(Path(__file__)) == contract['tool_sha256'], 'Recovery tool changed')
    for item in contract['files'].values():
        m.require(m.file_hash(item['path']) == item['sha256'], 'Recovery evidence changed')
    busy = json.loads(Path(contract['files']['busy_run']['path']).read_bytes())
    root = json.loads(Path(contract['files']['root_result']['path']).read_bytes())
    validate_busy(m, busy, root, base)
    extended = copy.deepcopy(base)
    extended['operation_plan_id'] = contract['operation_plan_id']
    extended['reviewed_runs'][BUSY_RUN] = contract['files']['busy_run']['sha256']
    return m, helper, app, extended, contract


def validate_busy(m, run, root, base):
    m.require(run['run_id'] == BUSY_RUN and run['plan_id'] == m.PLAN_ID and run['action'] == 'apply-plan'
              and run['state'] == 'failed' and run['phase'] == 'finished' and run['reason'] == 'Busy'
              and run['detail'] == 'Another archive/check/recovery holds the repository lock; retry later'
              and run['deleted_this_run'] == run['deleted_checkpoints'] == run['completed_before_run'] == 0
              and run['exit_code'] == 1 and run['unfinished_retirement'] and not run['already_complete']
              and run['authorization']['scope_id'] == base['after_scope_id'] and run['authorization']['resume_interrupted']
              and root['operation_plan_id'] == base['operation_plan_id'] and root['run_id'] == BUSY_RUN
              and root['configuration'] == 'after' and root['manual_finalization'] == 'original_disabled_restored'
              and root['protected_inputs_and_processes_unchanged'] and not root['passed'], 'Not the reviewed pre-lock Busy refusal')


def timer_window(app):
    """Admission margin only, never a claim of owning a future lock."""
    rows = []
    for service in app.PRESERVATION_UNITS:
        unit = service.removesuffix('.service') + '.timer'
        path = '/org/freedesktop/systemd1/unit/' + unit.replace('-', '_2d').replace('.', '_2e')
        row = {'unit': unit}
        for prop, clock in (('NextElapseUSecRealtime', time.time), ('NextElapseUSecMonotonic', time.monotonic)):
            value = json.loads(app.command(['/usr/bin/busctl', '--system', '--json=short', 'get-property',
                  'org.freedesktop.systemd1', path, 'org.freedesktop.systemd1.Timer', prop]))
            app.require(value['type'] == 't' and type(value['data']) is int, 'Unknown timer timestamp')
            usec = value['data']
            remaining = None if usec in (0, 2**64-1) else usec / 1e6 - clock()
            row[prop] = {'microseconds': usec, 'remaining_seconds': remaining}
            app.require(remaining is None or remaining > TIMER_MARGIN,
                        'Preservation timer is due within startup margin; no service requested: ' + unit)
        rows.append(row)
    return rows


def preflight(m, helper, app, plan):
    current = m.verify_operating(plan, helper, app)
    m.require(set(current['positions'].values()) == {'after'}, 'Only exact installed after configuration is accepted')
    m.require(app.idle(app.unit_state()), 'Manual service active or queued')
    retirement = m.assert_original_work(plan, app)
    app.installed_unit_check(helper)
    app.check_preservation_idle()
    clock = app.check_preview_anchor(helper, {'files':{'preview_plan':{'path':plan['files']['retirement_plan']['path']}}})
    timers = timer_window(app)
    app.check_preservation_idle()
    return retirement, clock, timers


def seal(m, helper, contract):
    m.OPS.mkdir(mode=0o755, exist_ok=True); helper.trusted_directory(m.OPS)
    out = m.OPS / contract['operation_plan_id']
    out.mkdir(mode=0o755, exist_ok=True); helper.trusted_directory(out)
    payloads = {'resume-only-plan.json': helper.blob(contract), 'resume_only.py': Path(__file__).read_bytes(),
                'authorization.md': Path(contract['files']['authorization']['path']).read_bytes()}
    for name, raw in payloads.items():
        path = out / name
        if path.exists():
            m.require(helper.fingerprint(path)['uid'] == 0 and path.read_bytes() == raw, 'Sealed recovery changed')
        else:
            helper.write_new(path, raw)
    return out


def operate(m, helper, app, plan, contract, action):
    with helper.locks():
        if action == 'finalize':
            out = m.OPS / contract['operation_plan_id']; helper.trusted_directory(out)
            m.require((out / 'resume-only-plan.json').read_bytes() == helper.blob(contract), 'Saved recovery changed')
            m.verify_operating(plan, helper, app, manual_disabled=False)
            return m.finalize(plan, helper, app, out, json.loads((out / 'state.json').read_bytes()))
        with app.runner_idle():
            retirement, clock, timers = preflight(m, helper, app, plan)
            if action == 'check':
                print(json.dumps({'passed':True, 'readonly':True, 'operation_plan_id':contract['operation_plan_id'],
                    'configuration':'after', 'configuration_writes':0, 'daemon_reload':False, 'reviewed_busy_run':BUSY_RUN,
                    'journal_unchanged':True, 'clock_ready':clock['ready'], 'preservation_timers':timers,
                    'automatic_retirement':'masked'}, indent=2))
                return 0
            out = seal(m, helper, contract)
            state_path = out / 'state.json'
            record = json.loads(state_path.read_bytes()) if state_path.exists() else {
                'operation_plan_id':contract['operation_plan_id'], 'based_on_operation':contract['based_on_operation'],
                'retirement_plan_id':m.PLAN_ID, 'reviewed_run_id':BUSY_RUN, 'passed':False, 'dispatch_intent':False,
                'started_at':datetime.now(timezone.utc).isoformat(), 'configuration':'after', 'configuration_writes':0,
                'daemon_reload':False, 'clock_control':False, 'automatic_retirement':'masked'}
            m.require(not record.get('dispatch_intent') and not record.get('cancelled_before_dispatch'),
                      'Recovery consumed; inspect/finalize instead of dispatching again')
            proposal = m.scope_transition_proposal(plan, retirement, helper.proposal())
            proposal['reviewed_prelock_busy'] = {'run_id':BUSY_RUN, 'run_sha256':contract['files']['busy_run']['sha256'],
                'based_on_operation':contract['based_on_operation'], 'configuration_already_installed':True}
            enabled = app.enabled_grant(proposal, datetime.now(timezone.utc).isoformat(), str(out/'authorization.md'))
            raw = helper.blob(enabled)
            grant = {'text':raw.decode(), 'fingerprint':{**plan['manual_before']['fingerprint'], 'sha256':m.sha(raw)}}
            record.update(phase='before_grant', runs_before_dispatch=sorted(app.runs()), preservation_timers=timers)
            m.persist_state(helper, out, record)
            m.require(not (out/'manual-enabled.json').exists(), 'Grant intent exists; finalize/review required')
            helper.write_new(out/'manual-enabled.json', raw)
            record.update(phase='grant_intent', runs_before_dispatch=sorted(app.runs()), preservation_timers=timers)
            m.persist_state(helper, out, record)
            try:
                helper.replace_checked(helper.MANUAL, plan['manual_before']['fingerprint'], grant,
                    lambda: preflight(m, helper, app, plan))
                record.update(dispatch_intent=True, service_start_requested=False, phase='dispatch_intent')
                m.persist_state(helper, out, record)
            except BaseException:
                if app.idle(app.unit_state()):
                    app.restore_when_idle(helper, plan['manual_before'], grant)
                raise
        try:
            app.command(['/usr/bin/systemctl', '--system', '--no-block', 'start', app.UNIT])
            record.update(service_start_requested=True, phase='service_requested')
            m.persist_state(helper, out, record)
        except BaseException as exc:
            record['start_command_error'] = f'{type(exc).__name__}: {exc}'
            m.persist_state(helper, out, record)
        return m.finalize(plan, helper, app, out, record)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--action', choices=('check','apply','finalize'), default='check')
    p.add_argument('--approve-operation'); p.add_argument('--approve-retirement-plan'); p.add_argument('--approve-recovery-run')
    args=p.parse_args()
    m, helper, app, plan, contract=load()
    if args.action != 'check':
        m.require(os.geteuid()==0 and args.approve_operation==contract['operation_plan_id'], 'Exact recovery approval and root required')
        if args.action=='apply':
            m.require(args.approve_retirement_plan==m.PLAN_ID and args.approve_recovery_run==BUSY_RUN,
                      'Exact plan and latest Busy run approval required')
    try:
        return operate(m, helper, app, plan, contract, args.action)
    except BaseException as exc:
        value={'passed':False, 'operation_plan_id':contract['operation_plan_id'], 'action':args.action,
               'error':f'{type(exc).__name__}: {exc}', 'automatic_retry':False, 'daemon_reload':False}
        if args.action != 'check':
            out=m.OPS/contract['operation_plan_id']
            if out.is_dir():
                helper.trusted_directory(out)
                helper.write_new(out/('command-error-'+str(time.time_ns())+'.json'), helper.blob(value))
        print(json.dumps(value, indent=2)); return 1


if __name__=='__main__':
    raise SystemExit(main())
