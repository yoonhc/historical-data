"""Validate fresh evidence and pin the existing four-root, no-delete scope."""

import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess

from refresh import BASE, PREVIOUS, ROOT, load_review


review = load_review()


def load(path):
    return json.loads(path.read_bytes())


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def evidence(path):
    return {'path': str(path), 'sha256': digest(path)}


def main():
    old = load(PREVIOUS / 'plan.json')
    probe = load(BASE / 'probe.json')
    host = load(BASE / 'host-addendum.json')
    inventory = load(BASE / 'inventory.json')
    comparison = load(BASE / 'comparison.json')
    retained = load(BASE / 'retained-checkpoint-verification.json')
    assert retained['result']['ok']
    assert retained['result']['backup_id'] == probe['pilot_retained_sources'][0]['backup_id']
    assert not inventory['shared_inodes'] and inventory['external_hardlinked_inodes'] == 0
    assert all(x['metadata_inventory_unchanged'] for x in inventory['original_twelve_trees'])
    assert [x['path'] for x in inventory['targets']] == [x['path'] for x in old['candidate_roots']]
    for current, previous in zip(inventory['targets'], old['candidate_roots'], strict=True):
        assert current['root'] == previous['root']
        for item in (current, previous):
            path = ROOT / item['inventory']
            assert digest(path) == item['gzip_sha256']
            with gzip.open(path, 'rb') as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == item['inventory_sha256']
    allowed_changes = {'pilot-repository': ['locks'], 'pilot-work': [],
                       'independent-trial': ['repository/locks'], 'production-restore-run': []}
    for item in comparison['inventory_comparison']:
        assert [r['path'] for r in item['changes']] == allowed_changes[item['label']]
        assert all(set(r['changed_fields']) == {'mtime_ns', 'ctime_ns'} for r in item['changes'])
    assert probe['trial_lock_files_after_probe'] == 0
    assert not host['candidate_references']
    assert set(host['automatic_retirement_links'].values()) == {'/dev/null'}
    for group in (probe['configuration_sha256'], host['configuration_hashes']):
        for path, expected in group.items():
            assert digest(Path(path)) == expected, path
    for name, destination in host['automatic_retirement_links'].items():
        assert str((Path.home()/'.config/systemd/user'/name).readlink()) == destination

    proofs = old['preserved_proofs']
    assert digest(Path(proofs['old_inventory'])) == proofs['old_inventory_sha256']
    for item in proofs['historical_proofs']:
        assert digest(ROOT / item['evidence']) == item['evidence_sha256']
    for item in proofs['copied_manifests_and_restore_receipts']:
        path = ROOT / item['copy']
        assert digest(path) == item['compressed_sha256']
        raw = gzip.decompress(path.read_bytes())
        assert len(raw) == item['bytes']
        assert hashlib.sha256(raw).hexdigest() == item['sha256']
        assert Path(item['source']).read_bytes() == raw
    protected_receipt = probe['protected_recovery_receipt']
    assert digest(Path(protected_receipt['path'])) == protected_receipt['sha256']

    check = probe['existing_production_full_check']
    assert check['ok'] and check['read_data'] and check['status'] == 'complete'
    prod = probe['repositories'][str(review.PRODUCTION)]['snapshots']
    assert {r['id'] for r in prod} == set(check['snapshot_ids'])
    assert len(probe['clone_snapshot_equivalence']) == 7
    for item in comparison['full_check_receipt_files']:
        assert digest(Path(item['path'])) == item['sha256']
    check_summary = json.loads(Path(check['receipt']['stdout']).read_text())
    assert check['receipt']['returncode'] == 0 and check_summary['num_errors'] == 0
    assert not check_summary['broken_packs']
    state = load(Path(probe['state_path']))
    assert not state.get('active_operation')
    assert state['last_operation']['ok']
    assert state['repository_check'] == check

    prior_run_path = ROOT / 'reports/restic-archive-runs/checkpoint-scheduled/2db85579ca0d47818af88623ea1b360a/run.json'
    prior_run = load(prior_run_path)
    assert prior_run['state'] == 'complete' and prior_run['ok']
    assert prior_run['exit_code'] == 0 and prior_run['deleted_checkpoints'] == 2
    assert not prior_run['needs_attention'] and not prior_run['unfinished_retirement']
    assert prior_run['result']['journal']['status'] == 'complete'
    prerequisite = {**evidence(prior_run_path), 'finished_at': prior_run['finished_at'],
                    'state': prior_run['state'], 'deleted_checkpoints': prior_run['deleted_checkpoints'],
                    'plan_id': prior_run['plan_id'], 'preferred_prior_observation_satisfied': True}

    command = ['du', '-s', '-c', '-x', '-B1', '--', *probe['targets'].values()]
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
    sizes = {line.split('\t', 1)[1]: int(line.split('\t', 1)[0]) for line in result.stdout.splitlines()}
    assert sizes['total'] == inventory['estimated_reclaimable_allocated_bytes']
    for row in inventory['targets']:
        assert sizes[row['path']] == row['regular_allocated_path_bytes'] + row['directory_blocks_bytes']
    for protected in (ROOT, review.PRODUCTION, review.KIT, Path('/mnt/backup/marketdata-historical')):
        assert all(not protected.is_relative_to(t) and not t.is_relative_to(protected)
                   for t in review.TARGETS.values())

    profile = probe['trial_profile']
    assert Path(profile['source']).read_bytes() == (ROOT / profile['preserved_copy']).read_bytes()
    assert profile['source_sha256'] == old['profile_retirement']['source_sha256']
    guidance = old['kit_guidance']
    assert digest(Path(guidance['prepared_source'])) == guidance['sha256']
    assert not Path(guidance['planned_destination']).exists()
    inputs = ['refresh.py', 'build_plan.py', 'probe.json', 'host-addendum.json', 'inventory.json',
              'comparison.json', 'retained-checkpoint-verification.json',
              'probe-execution.json', 'inventory-execution.json', 'retained-execution.json',
              'manual-procedure.md']
    filesystem = os.statvfs('/mnt/backup')
    plan = copy.deepcopy(old)
    plan.pop('plan_id')
    plan.pop('additional_allocated_bytes_vs_old_plan')
    plan.update({
        'schema': 2, 'kind': 'refreshed_one_off_trial_retirement_review', 'created_at': review.now(),
        'supersedes_review_plan_id': old['plan_id'],
        'previous_plan': evidence(PREVIOUS / 'plan.json'),
        'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
        'input_sha256': {name: digest(BASE / name) for name in inputs},
        'reused_read_only_code': evidence(PREVIOUS / 'review.py'),
        'authorization': {'deletion_authorized': False, 'apply_enabled': False,
                          'recurring_checkpoint_grant_applies': False,
                          'status': 'Refreshed exact manual scope; destructive application requires explicit approval.'},
        'candidate_roots': inventory['targets'], 'replacement_sources': probe['pilot_retained_sources'],
        'cloned_snapshots_also_in_production': probe['clone_snapshot_equivalence'],
        'retained_original_checkpoint_validation': retained,
        'existing_production_full_check': check,
        'full_check_receipt_files': comparison['full_check_receipt_files'],
        'profile_retirement': {**profile, 'source_identity': review.signature(Path(profile['source']).stat()),
                               'planned_action': old['profile_retirement']['planned_action'], 'performed': False},
        'completed_prior_checkpoint_observation': prerequisite,
        'inventory_changes_since_previous_plan': comparison['inventory_comparison'],
        'estimated_reclaimable_allocated_bytes': inventory['estimated_reclaimable_allocated_bytes'],
        'estimated_change_since_previous_plan_bytes': inventory['estimated_reclaimable_allocated_bytes'] - old['estimated_reclaimable_allocated_bytes'],
        'apparent_file_bytes': inventory['apparent_file_bytes'], 'du_cross_check': sizes,
        'candidate_file_count': sum(x['files'] for x in inventory['targets']),
        'candidate_directory_count': sum(x['directories'] for x in inventory['targets']),
        'usage': probe['process_usage_after'],
        'automatic_retirement': {'state': 'masked', 'new_authorization': False,
                                'links': host['automatic_retirement_links']},
        'space_observation': {'at': review.now(), 'available_bytes': filesystem.f_bavail * filesystem.f_frsize,
                              'total_bytes': filesystem.f_blocks * filesystem.f_frsize,
                              'exact_reclaimed_bytes': 0, 'actual_files_deleted': 0},
        'pre_apply_requirements': [
            'Obtain approval for this exact four-root scope, inactive trial profile retirement and additive kit note/receipt. No checkpoint grant or former plan approval is reused.',
            'Use the linked manual procedure and exact inventory, with a persistent progress record; no wildcard root deletion or automatic checkpoint cleaner.',
            'Immediately recheck filesystem/UUID, root identities, inventory, retained sources and full-check evidence; reject unexpected changes.',
            'Verify current absence of trial jobs and repository locks; fence the trial profile and acquire applicable nonblocking exclusive cooperative locks before deleting. Process samples do not prove exclusive use.',
            'Keep all protected data, artifacts, historical receipts and source_root unchanged. No forget/prune, service restart, Chrony/RTC or automatic-retirement grant change.',
            'Record partial deletion if interrupted; resume only the same approved list and reject unexplained absences/changes.',
            'After completion verify protected identities/settings and record filesystem free-space change separately from the allocated-block estimate.',
        ],
        'actual_files_deleted': 0, 'actual_reclaimed_bytes': 0,
    })
    plan['plan_id'] = review.sha(review.encoded(plan))
    review.write(BASE / 'plan.json', plan)
    review.write(BASE / 'validation.json', {
        'at': review.now(), 'passed': True, 'plan_id': plan['plan_id'],
        'old_manifest_and_restore_receipt_copies_verified': 12,
        'current_source_metadata_matches_preserved_copies': True,
        'inventory_changes_only_empty_restic_lock_directory_timestamps': True,
        'configurations_unchanged_since_probe': True, 'automatic_retirement_still_masked': True,
        'production_snapshot_count': len(prod), 'existing_full_read_check_covers_current_snapshot_set': True,
        'fresh_full_repository_read': False, 'fresh_restore_test': False,
        'retained_checkpoint_full_verification': True, 'independent_du_command': command,
        'independent_du_totals_match': True, 'previous_scheduled_retirement_observed': prerequisite,
        'actual_files_deleted': 0,
    })
    print(json.dumps({'plan_id': plan['plan_id'], 'estimated_reclaimable_allocated_bytes': sizes['total'],
                      'deleted_files': 0}), flush=True)


if __name__ == '__main__':
    main()
