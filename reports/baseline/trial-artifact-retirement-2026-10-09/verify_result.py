"""Read-only completion cross-check; write one local verification record."""

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path


BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('completed_trial_retirement', BASE/'apply.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main():
    plan = json.loads((module.REVIEW/'plan.json').read_bytes())
    result = json.loads((BASE/'result.json').read_bytes())
    approval = json.loads((BASE/'approval.json').read_bytes())
    assert result['passed'] and result['phase'] == 'complete'
    assert result['plan_id'] == approval['plan_id'] == plan['plan_id']
    assert module.sha(BASE/'apply.py') == result['executor_sha256'] == approval['executor_sha256']
    assert module.sha(BASE/'approval.json') == result['approval_sha256']
    assert result['files_deleted'] == plan['candidate_file_count']
    assert result['directories_deleted'] == plan['candidate_directory_count']
    for row in plan['candidate_roots']:
        path = Path(row['path'])
        assert not path.exists() and not path.is_symlink()
    profile = plan['profile_retirement']
    assert not Path(profile['source']).exists()
    assert module.sha(module.ROOT/profile['preserved_copy']) == profile['source_sha256']
    progress = module.AUDIT/'progress.jsonl'
    assert module.sha(progress) == result['progress_sha256']
    intents, completed, roots = {}, {}, []
    for line in progress.read_text().splitlines():
        event = json.loads(line)
        if event['kind'] == 'batch_intent':
            assert event['index'] not in intents
            intents[event['index']] = event
        elif event['kind'] == 'batch_complete':
            assert event['index'] in intents and event['index'] not in completed
            for key in ('root', 'count'):
                assert event[key] == intents[event['index']][key]
            completed[event['index']] = event
        elif event['kind'] == 'root_complete':
            roots.append(event['root'])
    assert intents.keys() == completed.keys()
    assert sum(x['count'] for x in completed.values()) == result['files_deleted'] + result['directories_deleted']
    assert roots == list(module.EXPECTED)
    assert result['protected_before']['services'] == result['protected_after']['services']
    assert result['protected_before']['snapshot_ids'] == result['protected_after']['snapshot_ids']
    assert result['protected_after']['automatic_retirement'] == 'masked'
    for item in plan['protected']:
        assert Path(item['path']).exists()
    for kind in ('kit_note', 'kit_receipt'):
        item = result[kind]
        assert module.sha(Path(item['path'])) == item['sha256']
    kit = Path(result['kit_note']['path']).parent
    before = json.loads((module.AUDIT/'kit-before.json').read_bytes())
    current = module.kit_inventory(kit)
    additions = {Path(result[key]['path']).name for key in ('kit_note', 'kit_receipt')}
    assert [x for x in current if x['path'] not in additions] == before
    assert {x['path'] for x in current} - {x['path'] for x in before} == additions
    receipt = json.loads(Path(result['kit_receipt']['path']).read_bytes())
    assert receipt == {k:v for k,v in result.items() if k != 'kit_receipt'}
    v = {'at': datetime.now(timezone.utc).isoformat(), 'passed': True, 'plan_id': plan['plan_id'],
         'completed_batches': len(completed), 'unresolved_batches': 0, 'absent_roots': roots,
         'deleted_files': result['files_deleted'], 'deleted_directories': result['directories_deleted'],
         'profile_copy_preserved': True, 'kit_original_entries_preserved': len(before),
         'kit_additions': sorted(additions), 'production_snapshots_retained': len(result['protected_after']['snapshot_ids']),
         'same_collector_and_chrony_invocations_during_operation': True,
         'automatic_retirement': 'masked', 'space_change_is_observed_not_attributed': True,
         'result_sha256': module.sha(BASE/'result.json'), 'progress_sha256': module.sha(progress),
         'validator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    module.new_json(BASE/'verification.json', v)
    print(json.dumps(v, ensure_ascii=False))


if __name__ == '__main__':
    main()
