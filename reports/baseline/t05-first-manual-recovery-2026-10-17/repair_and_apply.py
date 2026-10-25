"""Materialize one identical root-owned apply unit, then the same manual plan.

No engine, policy, clock, checkpoint evidence, recurring grant or mask changes.
The original failure remains. Only its exact pre-deletion refusal is accepted.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('manual_continuation', HERE / 'manual_apply.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
UNIT_FILE = app.UNIT_FILE
CANONICAL = app.CONFIG / 'units/marketdata-checkpoint-retirement-apply@.service'
ROOT_REPORTS = Path('/var/lib/marketdata-clock/t05-manual-unit-repairs')


def validate_repair(inputs):
    repair = inputs['repair']
    app.require(repair['target'] == str(UNIT_FILE) and repair['source'] == str(CANONICAL), 'Wrong repair path')
    app.require(app.sha(Path(__file__).read_bytes()) == inputs['repair_tool_sha256'], 'Repair tool changed')
    app.require(repair['before'] == {'uid':0,'gid':0,'mode':0o777,'kind':'symlink','target':str(CANONICAL)},
                'Unexpected original installation')
    app.require(repair['after'] == {'uid':0,'gid':0,'mode':0o644,'kind':'file','nlink':1,
                                  'sha256':app.sha(CANONICAL.read_bytes())}, 'Unit content/permissions differ')
    app.require(repair['after']['sha256'] == '9932a3c4a8bc5acc74d5ba9c98d6348e2e367524999e15181b246fd0ac3addd0',
                'Unit differs from reviewed plan')
    expected = json.loads(Path(inputs['files']['proposal']['path']).read_bytes())
    app.require(expected['scope']['system_units'][CANONICAL.name] == repair['after']['sha256'],
                'Existing scope does not bind these bytes')
    app.require(expected['scope_id'] == 'da7b03dba075a816d1c8277298dd123ce1db62e54aa9a85920711a18913a6c22',
                'Existing scope changed')
    return repair


def reviewed_prior(inputs):
    raw = Path(inputs['files']['failed_run']['path']).read_bytes()
    app.require(app.sha(raw) == inputs['files']['failed_run']['sha256'], 'Original failed run changed')
    app.reviewed_predelete_failure(json.loads(raw))
    result = json.loads(Path(inputs['files']['failed_root_result']['path']).read_bytes())
    app.require(result['run_id'] == app.FAILED_RUN and not result['passed']
                and result['manual_finalization'] == 'original_disabled_restored'
                and result['protected_inputs_and_processes_unchanged'] is True, 'Original root result differs')
    app.require(not (app.STATE / 'retention/journals' / (app.PLAN_ID + '.json')).exists(),
                'Retirement journal exists; separate review required')
    for path in app.runs().values():
        value = json.loads(path.read_bytes())
        if value.get('action') == 'apply-plan' and value.get('plan_id') == app.PLAN_ID:
            app.reviewed_predelete_failure(value)
            app.require(app.sha(path.read_bytes()) == inputs['files']['failed_run']['sha256'],
                        'Another/changed application; do not retry')


def check(inputs, helper, repair):
    scope_plan = json.loads(Path(inputs['files']['scope_plan']['path']).read_bytes())
    helper.validate_plan(scope_plan)
    live = helper.verify_live(scope_plan)
    app.require(live['position'] == 'after', 'Disabled recurring scope changed')
    row = app.unit_state()
    app.require(app.idle(row) and row['FragmentPath'] == str(UNIT_FILE)
                and row['DropInPaths'] == '', 'Unexpected/running apply service')
    actual = helper.fingerprint(UNIT_FILE)
    app.require(actual in (repair['before'], repair['after']), 'Unreviewed unit change')
    app.require(UNIT_FILE.read_bytes() == CANONICAL.read_bytes(), 'Unit bytes changed')
    app.validate_grant(app.manual_proposal(helper), json.loads(Path(inputs['files']['proposal']['path']).read_bytes()))
    reviewed_prior(inputs)
    baseline = json.loads(Path(inputs['files']['preview_after']['path']).read_bytes())
    for name in helper.PROCESS_UNITS:
        for key in ('MainPID', 'InvocationID', 'ActiveState'):
            app.require(live['units'][name][key] == baseline['units'][name][key], 'Protected process changed')
    current = app.registry()
    app.require(all(current.get(p) == h for p,h in baseline['registry_files'].items()), 'Existing evidence changed')
    app.require(helper.fingerprint(CANONICAL) == repair['after'], 'Canonical unit is not sealed')
    return {'position':'before' if actual == repair['before'] else 'after', 'unit':row,
            'units':live['units'], 'registry':current, 'unit_fingerprint':actual}


def restore_link(helper, target, before, after, recheck):
    current = helper.fingerprint(target)
    if current == before:
        return
    app.require(current == after, 'Unknown unit content; do not overwrite')
    temp = target.parent / ('.t05-apply-link-' + uuid.uuid4().hex)
    try:
        temp.symlink_to(before['target'])
        app.require(helper.fingerprint(temp) == before, 'Staged original link differs')
        recheck()
        app.require(helper.fingerprint(target) == after, 'Unit changed before rollback')
        os.replace(temp, target)
        helper.sync_dir(target.parent)
        app.require(helper.fingerprint(target) == before, 'Restored link differs')
    finally:
        if temp.is_symlink():
            temp.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('check','apply','rollback'), default='check')
    parser.add_argument('--approve-plan')
    args = parser.parse_args()
    inputs, helper = app.load_inputs()
    repair = validate_repair(inputs)
    app.require(args.action == 'check' or (os.geteuid() == 0 and args.approve_plan == app.PLAN_ID),
                'Root and exact plan acknowledgement required')
    record = None
    with helper.locks(), app.runner_idle():
        before = check(inputs, helper, repair)
        if args.action == 'check':
            print(json.dumps({'passed':True,'readonly':True,'position':before['position'],
                              'same_plan':app.PLAN_ID,'unit_sha256':repair['after']['sha256'],
                              'manual_enabled':False,'automatic_retirement':'masked'},indent=2))
            return 0
        out = ROOT_REPORTS / uuid.uuid4().hex
        out.mkdir(parents=True, mode=0o755)
        helper.trusted_directory(out)
        helper.write_new(out/'inputs.json',(HERE/'inputs.json').read_bytes())
        for name in ('repair_and_apply.py','manual_apply.py','authorization.md'):
            helper.write_new(out/name,(HERE/name).read_bytes())
        helper.write_new(out/'before.json',helper.blob(before))
        record={'started_at':datetime.now(timezone.utc).isoformat(),'action':args.action,
                'plan_id':app.PLAN_ID,'passed':False,'target':str(UNIT_FILE),
                'checkpoint_deletion':False,'manual_grant_changed':False,
                'automatic_retirement':'masked','report':str(out/'result.json')}
        try:
            if args.action == 'rollback':
                restore_link(helper,UNIT_FILE,repair['before'],repair['after'],
                             lambda:check(inputs,helper,repair))
                app.command(['/usr/bin/systemctl','--system','daemon-reload'])
                app.require(check(inputs,helper,repair)['position']=='before','Rollback differs')
                record['passed']=True
            else:
                if before['position']=='before':
                    desired={'text':CANONICAL.read_text(),'fingerprint':repair['after']}
                    helper.replace_checked(UNIT_FILE,repair['before'],desired,
                                           lambda:check(inputs,helper,repair))
                app.command(['/usr/bin/systemctl','--system','daemon-reload'])
                app.require(check(inputs,helper,repair)['position']=='after','Repair differs')
                app.installed_unit_check(helper)
                actual = app.preflight(inputs,helper)
                helper.write_new(out/'after.json',helper.blob(actual))
                record.update(passed=True,installed_guard_passed_as_uid1000=True,
                              unchanged_scope_id=actual['proposal']['scope_id'],
                              installed_unit=helper.fingerprint(UNIT_FILE))
        except BaseException as exc:
            record['error']=f'{type(exc).__name__}: {exc}'
            if args.action=='apply':
                try:
                    restore_link(helper,UNIT_FILE,repair['before'],repair['after'],
                                 lambda:app.require(app.idle(app.unit_state()),'Service active'))
                    app.command(['/usr/bin/systemctl','--system','daemon-reload'])
                    record['rollback']='original_link_restored'
                except BaseException as rollback_error:
                    record['rollback_error']=f'{type(rollback_error).__name__}: {rollback_error}'
        finally:
            record['finished_at']=datetime.now(timezone.utc).isoformat()
            helper.write_new(out/'result.json',helper.blob(record))
            print(json.dumps(record,ensure_ascii=False,indent=2),flush=True)
    if not record['passed']:
        return 1
    if args.action=='rollback':
        return 0
    # Release the repair locks before the continuation obtains the same locks.
    result=subprocess.run(['/usr/bin/python3','-B','-I',str(HERE/'manual_apply.py'),
                           '--run','--approve-plan',app.PLAN_ID],env=app.ENV,cwd='/')
    return result.returncode


if __name__=='__main__':
    raise SystemExit(main())
