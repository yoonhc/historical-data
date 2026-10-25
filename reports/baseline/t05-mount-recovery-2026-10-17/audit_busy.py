"""Read-only audit of the post-reload repository-lock refusal. No service control."""
import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = '2d9f7b8b4a434183a875dfa441171295'
RESULT = 'result-1792230449307681504.json'


def main():
    spec = importlib.util.spec_from_file_location('frozen_mount_recovery', HERE / 'repair_and_resume.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    plan, helper, app = m.load()
    operating = m.verify_operating(plan, helper, app)
    m.require(set(operating['positions'].values()) == {'after'}, 'Configuration not exact after')
    app.installed_unit_check(helper)
    root = m.OPS / plan['operation_plan_id']
    result = json.loads((root / RESULT).read_bytes())
    run_path = m.STATE / 'runs' / RUN / 'run.json'
    run = json.loads(run_path.read_bytes())
    m.require(result['run_id'] == RUN and result['configuration'] == 'after'
              and not result['passed'] and result['manual_finalization'] == 'original_disabled_restored', 'Root result differs')
    m.require(run['plan_id'] == m.PLAN_ID and run['state'] == 'failed' and run['exit_code'] == 1
              and run['reason'] == 'Busy' and run['detail'] == 'Another archive/check/recovery holds the repository lock; retry later'
              and run['deleted_this_run'] == 0 and run['unfinished_retirement'], 'Not exact reviewed Busy result')
    original = json.loads((m.STATE / 'retention/plans' / (m.PLAN_ID + '.json')).read_bytes())
    jpath = m.STATE / 'retention/journals' / (m.PLAN_ID + '.json')
    journal = json.loads(jpath.read_bytes())
    m.require(m.file_hash(jpath) == plan['journal_sha256'], 'Journal changed')
    m.reviewed_journal(journal, original)
    app.untouched_roots(original)
    stage = m.PARENT / '.automatic.retiring/historical-data-original-v1' / m.PLAN_ID
    m.require(not any(stage.iterdir()), 'Staging not empty')
    for path, digest in plan['inventories'].items():
        m.require(m.file_hash(path) == digest, 'Inventory changed')
    run_hashes = {}
    for rid, path in app.runs().items():
        value = json.loads(path.read_bytes())
        if value.get('plan_id') == m.PLAN_ID and value.get('action') in ('apply-plan', 'run'):
            run_hashes[rid] = m.file_hash(path)
    expected = {**plan['reviewed_runs'], RUN: m.file_hash(run_path)}
    m.require(run_hashes == expected, 'Additional/changed application')
    out = HERE / 'postapply-busy-evidence'; out.mkdir(exist_ok=True)
    inputs = {'root-result.json': root / RESULT, 'root-state.json': root / 'state.json',
              'manual-enabled.json': root / 'manual-enabled.json', 'run.json': run_path,
              'clock.jsonl': run_path.parent / 'clock.jsonl', 'journal.json': jpath}
    index = {}
    for name, source in inputs.items():
        raw = source.read_bytes()
        (out / name).write_bytes(raw)
        index[name] = {'source': str(source), 'sha256': m.sha(raw), 'bytes': len(raw)}
    commands = []
    for path in sorted((run_path.parent / 'commands').glob('*.json')):
        raw = path.read_bytes(); value = json.loads(raw)
        (out / path.name).write_bytes(raw)
        commands.append({'source': str(path), 'sha256': m.sha(raw), 'record': value})
    logs = subprocess.run(['/usr/bin/journalctl', '--system', '--since', '2026-10-17 18:46:55', '--until', '2026-10-17 18:48:05',
        '-u', app.UNIT, '-u', 'marketdata-restic-archive.service', '-u', 'marketdata-pack.service', '--no-pager', '-o', 'json'],
        text=True, capture_output=True, check=True)
    (out / 'manager-journal.jsonl').write_text(logs.stdout)
    timer = app.command(['/usr/bin/systemctl', '--system', 'show', 'marketdata-restic-archive.timer',
        '--property=Id,ActiveState,SubState,TimersMonotonic,TimersCalendar,LastTriggerUSec,NextElapseUSecRealtime,NextElapseUSecMonotonic'])
    (out / 'restic-timer.txt').write_text(timer)
    report = {'passed': True, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'operation_plan_id': plan['operation_plan_id'], 'plan_id': m.PLAN_ID, 'run_id': RUN,
        'configuration': 'after', 'scope_id': plan['after_scope_id'], 'reason': run['reason'], 'detail': run['detail'],
        'run_started_at': run['started_at'], 'run_finished_at': run['finished_at'],
        'journal_sha256': m.file_hash(jpath), 'journal_unchanged': True, 'journal_attempts': len(journal['attempts']),
        'all_items_prepared': True, 'unlinked_files': 0, 'deleted_this_run': 0, 'stage_empty': True,
        'candidates': len(original['candidates']), 'retained': len(original['keep']), 'excluded': len(original['excluded']),
        'manifest_and_candidate_roots_preserved': True, 'inventories_preserved': len(plan['inventories']),
        'protected_inputs': len(plan['protected']), 'operating': operating, 'manual_enabled': False, 'recurring_enabled': False,
        'automatic_retirement': 'masked', 'run_hashes': run_hashes, 'source_evidence': index,
        'commands': commands, 'clock_uncertainty_seconds': run['initial_clock']['total_uncertainty'],
        'reference_age_seconds': run['initial_clock']['reference_age_seconds'], 'current_unit': app.unit_state(),
        'operating_mutation': False, 'new_service_requested': False}
    (HERE / 'postapply-busy-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('operating','source_evidence','commands','current_unit','run_hashes')}, indent=2))


if __name__ == '__main__':
    main()
