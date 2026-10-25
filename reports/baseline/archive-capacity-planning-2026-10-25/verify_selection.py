"""Read only the selected historical stdout files; create a proposal, not authority.

No service, repository, source output, receipt or ledger writes. At most two
manifest-sized byte buffers; no payload copy on disk. Average read budget 64MiB/s.
"""

import hashlib
import importlib.util
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / 't18-capacity-recovery-2026-10-19'
RELEASE = Path('/opt/marketdata/releases/restic-output-20261022-585c3e3686704e5c')
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location('unchanged_dedup', OLD / 'deduplicate.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def ref(path):
    raw = Path(path).read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': d.digest(raw)}


def main():
    selection_path = HERE / 'selection-metadata.json'
    selection = json.loads(selection_path.read_bytes())
    compatibility = json.loads((OLD / 'hardlink-compatibility.json').read_bytes())
    contract = RELEASE / 'policy/request_contract.py'
    assert ref(contract)['sha256'] == compatibility['contract_sha256']
    report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'purpose': 'proposal_only',
              'approval_status': 'not_requested', 'operating_writes': False,
              'selection_ref': ref(selection_path), 'content_verified': False,
              'old_algorithm': ref(OLD / 'deduplicate.py'), 'compatibility_evidence': ref(OLD / 'hardlink-compatibility.json'),
              'current_contract': ref(contract), 'current_writer': ref(RELEASE / 'preserve/scripts/archive_backups.py'),
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'root': str(d.ROOT), 'groups': [], 'capacity': {}, 'limits': {
                  'average_read_budget_bytes_per_second': 64 * 1024 ** 2,
                  'payload_copies_created': 0, 'all_historical_outputs_examined': False,
                  'old_tool_requires_stopped_timer': True,
                  'active_timer_application_adapter_prepared': False}}
    seen = set()
    verified = total_bytes = 0
    start = time.monotonic()
    receipts = {}
    for sid, value in selection['snapshots'].items():
        items = value['files']
        assert len(items) == 512
        expected = value['entry']['manifest_sha256']
        d.metadata(items[0])
        body = Path(items[0]['path']).read_bytes()
        assert hashlib.sha256(body).hexdigest() == expected
        for item in items:
            path = Path(item['path'])
            assert path.is_relative_to(d.ROOT) and len(path.relative_to(d.ROOT).parts) == 2
            assert path.suffix == '.stdout' and item['nlink'] == 1
            before = d.metadata(item)
            assert (before.st_dev, before.st_ino) not in seen
            seen.add((before.st_dev, before.st_ino))
            raw = path.read_bytes()
            assert raw == body and d.digest(raw) == expected
            d.metadata(item)
            for prefix in ('command_receipt', 'run_receipt'):
                receipt = ref(item[prefix])
                assert receipt['sha256'] == item[prefix + '_sha256']
                receipts[receipt['path']] = receipt['sha256']
            command = json.loads(Path(item['command_receipt']).read_bytes())
            run = json.loads(Path(item['run_receipt']).read_bytes())
            assert command['arguments'] == ['dump', sid, '/backup.json']
            assert command['stdout'] == str(path) and command['returncode'] == 0
            assert command['finished_at'] <= run['finished_at'] < selection['metadata_cutoff']
            assert run['ok'] and run['result']['ok']
            total_bytes += len(raw)
            verified += 1
            delay = total_bytes / (64 * 1024 ** 2) - (time.monotonic() - start)
            if delay > 0:
                time.sleep(min(delay, 1))
            if verified % 128 == 0:
                print(json.dumps({'verified_files': verified, 'target_files': 1024,
                                  'bytes_read': total_bytes, 'operating_writes': False}), flush=True)
        report['groups'].append({'snapshot_id': sid, 'backup_id': value['entry']['backup_id'],
                                 'sha256': expected, 'source': items[0], 'targets': items[1:]})
    # Recheck names and receipts at the end; later application still needs its
    # own locked revalidation. This observer takes no production locks.
    for group in report['groups']:
        for item in [group['source'], *group['targets']]:
            d.metadata(item)
    for path, expected in receipts.items():
        assert ref(path)['sha256'] == expected
    v = os.statvfs(ROOT)
    available = v.f_bavail * v.f_frsize
    reclaim = sum(i['allocated_bytes'] for g in report['groups'] for i in g['targets'])
    report['content_verified'] = True
    report['counts'] = {'files_read_and_compared': verified, 'retained_sources': 2,
                        'replacement_paths': 1022, 'bytes_compared': total_bytes,
                        'distinct_receipts_preserved': len(receipts)}
    report['capacity'] = {'available_at_verification_bytes': available,
        'expected_reclaim_bytes': reclaim, 'projected_available_before_audit_bytes': available + reclaim,
        'collector_reserve_bytes': 10 * 1024 ** 3, 'temporary_payload_bytes': 0,
        'proposed_audit_metadata_budget_bytes': 8 * 1024 ** 2,
        'projected_available_after_audit_budget_bytes': available + reclaim - 8 * 1024 ** 2,
        'caveat': 'Concurrent workload and final locked metadata revalidation may change capacity.'}
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    report['elapsed_seconds'] = time.monotonic() - start
    plan_id = d.digest(d.canonical(report))
    envelope = {'proposal_id': plan_id, 'proposal': report}
    output = HERE / 'verified-selection.json'
    with output.open('xb') as f:
        f.write(d.canonical(envelope) + b'\n')
    print(json.dumps({'report': str(output), 'proposal_id': plan_id,
                     'file_sha256': ref(output)['sha256'], 'counts': report['counts'],
                     'capacity': report['capacity'], 'elapsed_seconds': report['elapsed_seconds']}, indent=2))


if __name__ == '__main__':
    main()
