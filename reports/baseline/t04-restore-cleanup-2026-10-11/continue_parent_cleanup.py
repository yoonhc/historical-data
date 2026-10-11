"""Resume the approved T04 scope after a zero-deletion archive-lock hold.

Preserve the original plan and failed result. Only a completed, successful
archive operation may update the archive-state hash; every other protected
value, the snapshot set, full-check evidence, and deletion inventory must match.
"""
import argparse
import hashlib
import json
import os
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parent_cleanup as s

PLAN_ID = 'a6544cd5b0f8034d4babfdeb88d6a28fd700e222501c1d413d5524bc24f825b2'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.parse_args()
    s.review.require(os.geteuid() == 1000, 'Run as yoonhc on host')
    plan, previous = s.read_plan('parent-plan.json'), s.read_plan('plan.json')
    s.review.require(plan['plan_id'] == PLAN_ID and plan['scope'] == str(s.WORK), 'Scope changed')
    for path, digest in ((HERE/'plan.json', plan['previous_plan_sha256']),
                         (HERE/'parent_cleanup.py', plan['executor_sha256']),
                         (HERE/'prepare.py', plan['review_script_sha256']),
                         (Path(plan['inventory']), plan['inventory_sha256']),
                         (HERE/'parent-adapter-tests.json', plan['validation_sha256'])):
        s.review.require(s.review.digest(path) == digest, 'Pinned input changed: ' + str(path))
    validation = json.loads((HERE/'parent-adapter-tests.json').read_text())
    s.review.require(validation['passed'] and validation['sha256']['parent_cleanup.py'] == plan['executor_sha256'],
                     'Validation does not match approved executor')
    first = json.loads((HERE/'parent-result.json').read_text())
    s.review.require(first['plan_id'] == PLAN_ID and first['scope'] == str(s.WORK)
                     and not first['passed'] and first['phase'] == 'preflight'
                     and first['error'] == "BlockingIOError(11, 'Resource temporarily unavailable')"
                     and first['files_deleted'] == first['directories_deleted'] == 0,
                     'Prior attempt is not a zero-deletion lock hold')
    journal_path = s.RAW/'parent-deletion-journal.jsonl'
    output = HERE/'parent-recovery-result.json'
    s.review.require(not journal_path.exists() and not output.exists(), 'Prior deletion or recovery exists')
    lib, p = s.library(), s.profile()
    result = {'passed': False, 'plan_id': PLAN_ID, 'scope': str(s.WORK), 'phase': 'preflight',
              'started_at': datetime.now(timezone.utc).isoformat(),
              'original_plan_preserved': True, 'original_result_sha256': s.review.digest(HERE/'parent-result.json'),
              'continuation_sha256': s.review.digest(Path(__file__)),
              'clock_control': False, 'backup_requested': False, 'automatic_retirement': 'masked'}
    journal = None
    try:
        with ExitStack() as stack:
            s.locks(stack, p, Path(previous['retained_checkpoint']['path']))
            result['mount'] = s.mount(p)
            result['usage_before'] = s.use_check(previous)
            current = s.protected(lib, previous, p)
            changed = sorted(k for k in set(current) | set(plan['protected'])
                             if current.get(k) != plan['protected'].get(k))
            s.review.require(changed == ['archive_state_sha256'], 'Unexpected protected-state change: ' + str(changed))
            archive_path = Path(p['state_root'])/p['expected_repository_id']/p['archive_scope']/'state.json'
            archive = json.loads(archive_path.read_text())
            s.review.require(not archive.get('active_operation') and archive['last_operation']['ok']
                             and archive['repository_check'] == previous['existing_full_check'],
                             'Archive has not completed successfully with the same full check')
            s.review.require(lib.check_tree(s.WORK, Path(plan['inventory'])) == plan['entries'], 'Inventory changed')
            result.update(protected_before=current, refreshed_fields=changed,
                          completed_archive_operation=archive['last_operation'])
            reconciliation = {'plan_id': PLAN_ID, 'failed_result_sha256': result['original_result_sha256'],
                              'continuation_sha256': result['continuation_sha256'],
                              'archive_state_before_sha256': plan['protected']['archive_state_sha256'],
                              'archive_state_current_sha256': current['archive_state_sha256'],
                              'completed_archive_operation': archive['last_operation'],
                              'same_inventory_sha256': plan['inventory_sha256'],
                              'all_other_protected_values_equal': True}
            result['reconciliation_sha256'] = hashlib.sha256(s.review.encode(reconciliation)).hexdigest()
            s.review.save(HERE/'parent-reconciliation.json', reconciliation)
            lib.syncdir(HERE)
            result['space_before'] = lib.space()
            journal = lib.Journal(journal_path)
            lib.syncdir(s.RAW)
            journal.emit('approved_root', plan_id=PLAN_ID, root=str(s.WORK),
                         inventory_sha256=plan['inventory_sha256'], reconciliation_sha256=result['reconciliation_sha256'])
            result['phase'] = 'delete_exact_inventory'
            lib.delete_tree(s.WORK, Path(plan['inventory']), plan['root'], journal)
            s.review.require((journal.files, journal.directories) == (plan['file_count'], plan['directory_count']),
                             'Deletion count differs')
            result['phase'] = 'verify_preservation'
            result['protected_after'] = s.protected(lib, previous, p)
            s.review.require(result['protected_after'] == current, 'Protected state changed during cleanup')
            s.review.require(not s.WORK.exists() and not s.WORK.is_symlink() and s.WORK.parent.is_dir(),
                             'Target absence or permanent parent preservation failed')
            result['space_after'] = lib.space()
            result['observed_available_change_bytes'] = result['space_after']['available_bytes'] - result['space_before']['available_bytes']
            result.update(passed=True, phase='complete', exact_reclaimed_bytes=None,
                          target_absent=True, permanent_restore_parent_preserved=True)
    except BaseException as exc:
        result['error'] = repr(exc)
        result['retry'] = 'Do not automatically retry; reconcile first.'
        raise
    finally:
        result.update(files_deleted=journal.files if journal else 0,
                      directories_deleted=journal.directories if journal else 0)
        if journal:
            journal.close()
            result['journal_sha256'] = s.review.digest(journal_path)
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        s.review.save(output, result)
        print(json.dumps({k: v for k, v in result.items() if k not in (
            'protected_before', 'protected_after', 'usage_before', 'completed_archive_operation')}, indent=2))


if __name__ == '__main__':
    main()
