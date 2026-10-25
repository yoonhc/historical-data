"""Read-only comparison of the authorized ten-checkpoint completion.

Only workspace evidence copies are written. No preview, registration, service
control, restic command, deletion, grant update or full data verification runs.
"""
import importlib.util
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = 'f57f334be39e4137a4b7785836b6d2bd'
RESULT = 'result-1792236756392909971.json'


def main():
    spec = importlib.util.spec_from_file_location('frozen_reviewed_resume', HERE / 'resume_only.py')
    n = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(n)
    m, helper, app, plan, contract = n.load()
    operating = m.verify_operating(plan, helper, app)
    m.require(set(operating['positions'].values()) == {'after'}, 'Not exact installed after')
    app.installed_unit_check(helper)
    unit = app.unit_state()
    m.require(app.idle(unit), 'Retirement service is active or queued')

    root = m.OPS / contract['operation_plan_id']
    root_result = root / RESULT
    result = json.loads(root_result.read_bytes())
    m.require(helper.fingerprint(root_result)['uid'] == 0, 'Root receipt ownership changed')
    m.require(json.loads((root / 'state.json').read_bytes()) == result, 'Root state differs from final receipt')
    m.require((root / 'resume-only-plan.json').read_bytes() == helper.blob(contract)
              and m.file_hash(root / 'resume_only.py') == contract['tool_sha256'], 'Root sealed operation changed')
    m.require(result['operation_plan_id'] == contract['operation_plan_id']
              and result['retirement_plan_id'] == m.PLAN_ID and result['run_id'] == RUN
              and result['passed'] and result['run_state'] == 'complete' and result['run_exit_code'] == 0
              and result['deleted_this_run'] == 10 and result['configuration'] == 'after'
              and result['configuration_writes'] == 0 and result['daemon_reload'] is False
              and result['manual_finalization'] == 'original_disabled_restored'
              and result['protected_inputs_and_processes_unchanged'], 'Unexpected root result')

    run_path = m.STATE / 'runs' / RUN / 'run.json'
    run = json.loads(run_path.read_bytes())
    m.require(run['run_id'] == RUN and run['plan_id'] == m.PLAN_ID
              and run['action'] == 'apply-plan' and run['ok'] and run['state'] == 'complete'
              and run['phase'] == 'finished' and run['exit_code'] == 0
              and run['deleted_this_run'] == run['deleted_checkpoints'] == 10
              and run['completed_before_run'] == 0 and not run['already_complete']
              and not run['unfinished_retirement'] and not run['needs_attention'], 'Worker completion differs')
    m.require(run['context']['manager'] == 'system' and run['context']['uid'] == 1000
              and run['context']['service_verified'] and not run['context']['scheduled']
              and run['context']['service_properties']['Id'] == app.UNIT, 'Worker identity differs')
    grant_path = root / 'manual-enabled.json'
    grant = json.loads(grant_path.read_bytes())
    m.require(run['authorization']['grant_sha256'] == m.file_hash(grant_path)
              and run['authorization']['scope_id'] == plan['after_scope_id']
              and grant['reviewed_prelock_busy']['run_id'] == n.BUSY_RUN, 'Exact grant connection differs')

    plan_path = m.STATE / 'retention/plans' / (m.PLAN_ID + '.json')
    m.require(m.file_hash(plan_path) == plan['retirement_plan_sha256'], 'Original retirement plan changed')
    retirement = json.loads(plan_path.read_bytes())
    original = json.loads(Path(plan['files']['journal_copy']['path']).read_bytes())
    journal_path = m.STATE / 'retention/journals' / (m.PLAN_ID + '.json')
    journal = json.loads(journal_path.read_bytes())
    m.completed_journal(plan, retirement, original, journal)
    m.require(run['result']['journal'] == journal, 'Worker and committed journal differ')
    inventories = m.STATE / 'retention/inventories' / m.PLAN_ID
    m.require({str(p) for p in inventories.iterdir()} == set(plan['inventories']), 'Inventory set changed')
    expected_runs = {**plan['reviewed_runs'], RUN: m.file_hash(run_path)}
    actual_runs = {}
    for rid, path in app.runs().items():
        value = json.loads(path.read_bytes())
        if value.get('plan_id') == m.PLAN_ID and value.get('action') in ('apply-plan', 'run'):
            actual_runs[rid] = m.file_hash(path)
    m.require(actual_runs == expected_runs, 'Extra or changed application run')

    deleted = []
    for bid, item in journal['items'].items():
        m.require(not os.path.lexists(item['entry']['path']) and not os.path.lexists(item['staged']),
                  'Retired source/staging still present')
        deleted.append({'backup_id':bid, 'path':item['entry']['path'], 'manifest_sha256':item['entry']['manifest_sha256'],
                        'snapshot_id':item['snapshot_id'], 'phase':item['phase'], 'unlinked_files':item['unlinked_files']})
    kept = retirement['keep'] + retirement['excluded']
    markers = set((m.PARENT / 'automatic').glob('*/*/backup.json'))
    m.require(markers == {Path(e['path']) / 'backup.json' for e in kept}, 'Unexpected remaining checkpoint set')
    stage = m.PARENT / '.automatic.retiring/historical-data-original-v1' / m.PLAN_ID
    m.require(stage.is_dir() and not stage.is_symlink() and not any(stage.iterdir()), 'Staging not empty')
    recurring = json.loads((m.CONFIG / 'recurring.json').read_bytes())
    manual = json.loads(helper.MANUAL.read_bytes())
    m.require(not manual['enabled'] and not recurring['enabled']
              and recurring['approved_at'] is None and recurring['approval_reference'] is None, 'Automatic or manual approval changed')

    clock_path = run_path.parent / 'clock.jsonl'
    clocks = [json.loads(line) for line in clock_path.read_text().splitlines()]
    observations = [r['observation'] for r in clocks if r.get('kind') == 'provider']
    m.require(observations and all(r['ready'] and not r['reasons'] and r['total_uncertainty'] <= 5 for r in observations),
              'Recorded clock fault or uncertainty failure')
    commands = []
    for path in sorted((run_path.parent / 'commands').glob('*.json')):
        commands.append({'path':str(path), 'sha256':m.file_hash(path), 'record':json.loads(path.read_bytes())})
    m.require(all(c['record']['returncode'] == 0 and c['record']['operation'] in ('dump','cat','snapshots')
                  for c in commands), 'Unexpected archive operation or command failure')
    installed_engine = Path(run['context']['python']).parents[2] / 'scripts/retire_checkpoints.py'

    output = HERE / 'completion-evidence'
    output.mkdir(exist_ok=True)
    inputs = {'root-result.json':root_result, 'root-state.json':root/'state.json',
              'manual-enabled.json':grant_path, 'run.json':run_path, 'clock.jsonl':clock_path,
              'journal.json':journal_path, 'retirement-plan.json':plan_path}
    sources = {}
    for name, path in inputs.items():
        raw = path.read_bytes()
        target = output / name
        if target.exists():
            m.require(target.read_bytes() == raw, 'Existing evidence differs; do not overwrite')
        else:
            target.write_bytes(raw)
        sources[name] = {'source':str(path), 'sha256':m.sha(raw), 'bytes':len(raw)}
    (output / 'commands.json').write_text(json.dumps(commands, ensure_ascii=False, indent=2) + '\n')
    logs = subprocess.run(['/usr/bin/journalctl','--system','--since','2026-10-17 19:58:30',
          '--until','2026-10-17 20:33:00','-u',app.UNIT,'--no-pager','-o','json'],
          capture_output=True,text=True,check=True)
    (output / 'manager-journal.jsonl').write_text(logs.stdout)
    after = m.verify_operating(plan, helper, app)
    m.require(after == operating, 'Protected state changed during audit')
    for name, ref in sources.items():
        m.require(m.file_hash(ref['source']) == ref['sha256'], 'Completed evidence changed during audit')

    report = {'passed':True, 'checked_at':datetime.now(timezone.utc).isoformat(),
      'operation_plan_id':contract['operation_plan_id'], 'retirement_plan_id':m.PLAN_ID, 'run_id':RUN,
      'started_at':run['started_at'], 'finished_at':run['finished_at'],
      'elapsed_seconds':(datetime.fromisoformat(run['finished_at'])-datetime.fromisoformat(run['started_at'])).total_seconds(),
      'run_exit_code':0, 'deleted_this_run':10, 'deleted':deleted, 'retained':retirement['keep'], 'excluded':retirement['excluded'],
      'remaining_checkpoint_count':len(markers), 'stage_empty':True, 'journal_attempts':len(journal['attempts']),
      'original_failure_history_preserved':True, 'original_plan_preserved':True, 'inventories_preserved':len(plan['inventories']),
      'unlinked_files':sum(i['unlinked_files'] for i in journal['items'].values()),
      'space_observation':journal['attempts'][-1]['space_observation'], 'run_hashes':actual_runs,
      'configuration':'after', 'scope_id':plan['after_scope_id'], 'operating':operating, 'current_unit':unit,
      'manual_fingerprint':helper.fingerprint(helper.MANUAL), 'manual_enabled':False, 'recurring_enabled':False,
      'automatic_retirement':'masked', 'daily_evidence':run['daily_evidence'], 'service_context':run['context'],
      'clock_observation_count':len(observations), 'clock_all_ready':True,
      'uncertainty_seconds':{'min':min(r['total_uncertainty'] for r in observations),'max':max(r['total_uncertainty'] for r in observations)},
      'archive_command_counts':dict(Counter(c['record']['operation'] for c in commands)),
      'source_evidence':sources, 'installed_engine':{'path':str(installed_engine),'sha256':m.file_hash(installed_engine)},
      'audit_tool_sha256':m.file_hash(Path(__file__)), 'operating_mutation':False,
      'limitations':['No repeated full verification or recovery of retained market data in this audit; installed apply verifies retained bytes before retirement.',
                     'Filesystem free-space delta includes concurrent activity and is not an exact attribution of reclaimed bytes.',
                     'First manual application completion does not authorize recurring automatic retirement.']}
    (HERE / 'completion-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('passed','checked_at','operation_plan_id','run_id','elapsed_seconds',
       'deleted_this_run','remaining_checkpoint_count','stage_empty','journal_attempts','original_failure_history_preserved',
       'inventories_preserved','unlinked_files','manual_enabled','recurring_enabled','automatic_retirement',
       'clock_observation_count','uncertainty_seconds','operating_mutation')},indent=2))


if __name__ == '__main__':
    main()
