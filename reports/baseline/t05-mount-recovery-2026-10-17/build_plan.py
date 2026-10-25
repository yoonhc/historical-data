"""Freeze a review plan from read-only operating inputs; no installation or grant write."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    m = load_module('mount_recovery', HERE / 'repair_and_resume.py')
    helper_path = BASE / 't05-disabled-scope-2026-10-17/refresh_scope.py'
    app_path = BASE / 't05-first-manual-resume-2026-10-17/manual_resume.py'
    helper = load_module('scope_helper', helper_path)
    app = load_module('resume_helpers', app_path)
    exdev = BASE / 't05-first-manual-exdev-2026-10-17'
    baseline = json.loads((BASE / 't05-formal-preview-2026-10-17/after.json').read_bytes())
    audit = json.loads((exdev / 'audit.json').read_bytes())
    trial = exdev / 'system-rename-result-6b0b3591a09c42dc.json'
    tested = json.loads(trial.read_bytes())
    assert tested['passed'] and tested['operating_unchanged'] and tested['cleanup']['paths_absent'] and not tested['cleanup']['errors']
    assert tested['tool_sha256'] == m.file_hash(exdev / 'system_rename_preflight.py')
    assert [x['result']['rename_errno'] for x in tested['cases']] == [18, None]
    assert all(x['result']['uid'] == 1000 and x['result']['outside_write_errno'] == 30 for x in tested['cases'])
    validation = json.loads((HERE / 'validation.json').read_bytes())
    assert validation['passed'] and not any(validation[k] for k in ('errors', 'failures', 'skipped'))
    for name, expected in validation['tested_files'].items():
        assert m.file_hash(HERE / name) == expected, 'Validation source changed: ' + name
    original_proposal = json.loads((BASE / 't05-first-manual-resume-2026-10-17/manual-proposal.json').read_bytes())
    assert app.manual_proposal(helper) == original_proposal
    changes = []
    for target, candidate in [*( (m.CONFIG / 'units' / n, exdev / 'candidate' / n) for n in m.UNIT_NAMES),
                               (m.UNIT_FILE, exdev / 'candidate' / m.UNIT_NAMES[0]),
                               (m.CONFIG / 'recurring.json', exdev / 'candidate/recurring.json')]:
        before = {'text': target.read_text(), 'fingerprint': helper.fingerprint(target)}
        after_raw = candidate.read_text()
        changes.append({'path': str(target), 'before': before,
                        'after': {'text': after_raw, 'fingerprint': {**before['fingerprint'], 'sha256': m.sha(after_raw.encode())}}})
    frozen_paths = {'helper': helper_path, 'resume_helpers': app_path, 'authorization': HERE / 'authorization.md',
                    'retirement_plan': BASE / 't05-formal-preview-2026-10-17/plan.json',
                    'journal_copy': exdev / 'evidence/retirement-journal.json', 'root_failure': exdev / 'evidence/root-attempt/result.json',
                    'run_failure': exdev / 'evidence/run/run.json', 'system_trial': trial,
                    'system_trial_tool': exdev / 'system_rename_preflight.py',
                    'contract_tests': HERE / 'test_recovery_contract.py',
                    'installed_tests': HERE / 'test_installed_recovery.py',
                    'validation_runner': HERE / 'run_validation.py',
                    'validation': HERE / 'validation.json', 'test_results': HERE / 'tests.xml'}
    files = {key: {'path': str(path), 'sha256': m.file_hash(path)} for key, path in frozen_paths.items()}
    work = json.loads(Path(files['retirement_plan']['path']).read_bytes())
    journal_path = m.STATE / 'retention/journals' / (m.PLAN_ID + '.json')
    assert m.file_hash(journal_path) == files['journal_copy']['sha256']
    runs = {}
    for rid, path in app.runs().items():
        value = json.loads(path.read_bytes())
        if value.get('plan_id') == m.PLAN_ID and value.get('action') in ('apply-plan', 'run'):
            runs[rid] = m.file_hash(path)
    assert set(runs) == {app.FAILED_RUN, app.BUSY_RUN, m.RUN_ID}
    exclude = {x['path'] for x in changes} | {str(helper.MANUAL)}
    protected = {k: v for k, v in baseline['protected'].items() if k not in exclude}
    live = helper.manager()
    for k, v in protected.items():
        assert helper.fingerprint(k) == v, k
    for name in helper.PROCESS_UNITS:
        assert all(live[name][k] == baseline['units'][name][k] for k in ('MainPID', 'InvocationID', 'ActiveState'))
    plan = {'schema': 1, 'purpose': m.PURPOSE, 'created_at': datetime.now(timezone.utc).isoformat(),
            'boot_id': helper.BOOT.read_text().strip(), 'retirement_plan_id': m.PLAN_ID, 'reviewed_run_id': m.RUN_ID,
            'tool_sha256': m.file_hash(HERE / 'repair_and_resume.py'), 'files': files, 'changes': changes,
            'old_manual_proposal': original_proposal,
            'after_scope_id': json.loads((exdev / 'candidate/recurring.json').read_bytes())['scope_id'],
            'retirement_plan_sha256': files['retirement_plan']['sha256'], 'journal_sha256': files['journal_copy']['sha256'],
            'reviewed_runs': runs, 'inventories': {i['path']: i['sha256'] for i in audit['inventories']},
            'manual_before': {'text': helper.MANUAL.read_text(), 'fingerprint': helper.fingerprint(helper.MANUAL)},
            'protected': protected, 'existing_registry': baseline['registry_files'],
            'protected_processes': {name: {k: live[name][k] for k in ('MainPID', 'InvocationID', 'ActiveState')} for name in helper.PROCESS_UNITS}}
    m.validate_transition(plan)
    plan['operation_plan_id'] = m.sha(m.canonical(plan))
    (HERE / 'plan.json').write_bytes(helper.blob(plan))
    m.scope_transition_proposal(plan, work, json.loads((exdev / 'candidate/recurring.json').read_bytes()))
    print(json.dumps({'operation_plan_id': plan['operation_plan_id'], 'retirement_plan_id': m.PLAN_ID,
                      'changes': len(changes), 'protected_inputs': len(protected), 'operating_mutations': False}, indent=2))


if __name__ == '__main__':
    main()
