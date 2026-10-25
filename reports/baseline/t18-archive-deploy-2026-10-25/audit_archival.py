"""Audit completed natural runs from receipts; never invoke restic or services.

Uses the installed evidence decoder and request contract. Large manifest bodies
are not reread: fresh verification is evidenced by sealed command receipts, and
allocation is checked by inode/stat. Only small archived T05 bundles are decoded.
"""

import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from marketdata import checkpoint_evidence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RELEASE = Path('/opt/marketdata/releases/restic-output-20261022-585c3e3686704e5c')
REPORTS = ROOT / 'reports/restic-archive-runs'
SOURCE = HERE / 'archive-observed-20261025T085709Z.json'


def read(path):
    return json.loads(Path(path).read_bytes())


def ref(path):
    path = Path(path)
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def node(path):
    s = Path(path).stat()
    return {'path': str(path), 'device': s.st_dev, 'inode': s.st_ino,
            'bytes': s.st_size, 'allocated_bytes': s.st_blocks * 512}


def key(n):
    return n['device'], n['inode']


def main():
    observed = read(SOURCE)
    contract_path = RELEASE / 'policy/request_contract.py'
    spec = importlib.util.spec_from_file_location('audited_contract', contract_path)
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    audit = {'observed_at': datetime.now(timezone.utc).isoformat(), 'passed': False,
             'operating_mutations': False, 'restic_commands_dispatched': 0,
             'source_observation': ref(SOURCE), 'contract': ref(contract_path),
             'evidence_decoder': ref(checkpoint_evidence.__file__), 'targets': [], 'runs': []}
    attempts = {}
    for path in (ROOT / 'state/scheduled-clock-policy/restic-archive/attempts').glob('*/*.json'):
        if path.stat().st_mtime < 1792911721:
            continue
        request = read(path)
        result = request.get('work_result', {}).get('result', {})
        if result.get('run_id'):
            attempts[result['run_id']] = (path, request)
    chronological = sorted(observed['runs'], key=lambda r: r['scheduled']['started_at'])
    for r in chronological:
        s, a = r['scheduled'], r['archive_result']
        assert read(r['scheduled_ref']['path']) == s
        assert read(r['archive_ref']['path']) == a
        assert s['ok'] and a['ok'] and a['result']['ok']
        ctx = s['context']
        assert ctx['classification'] == 'verified_timer' and ctx['manager'] == 'system'
        assert ctx['provenance_method'] == 'current_start_job_activation_details'
        assert ctx['uid'] == 1000 and ctx['service_verified']
        assert ctx['start_job']['activation_details']['trigger_unit'] == 'marketdata-restic-archive.timer'
        path, request = attempts[s['run_id']]
        assert request['work_result']['result'] == s
        disposition = contract.classify('restic-archive', request['work_exit_code'],
                                       request['work_result'], request['pins'], request['arguments'])
        assert disposition == request['disposition'] and disposition['state'] == 'complete'
        assert request['state'] == 'complete' and request['exit_code'] == 0
        attempt = request['attempt']
        assert not attempt['errors'] and attempt['monitor']['stopped']
        assert attempt['work'] == {'observed_exit_code': 0, 'recovered_committed_result': False}
        assert attempt['invocation_id'] == ctx['service_properties']['InvocationID']
        shared, commands, other_files = [], [], []
        for c in r['commands']:
            record = c['record']
            assert ref(c['receipt']['path']) == c['receipt']
            assert record['returncode'] == 0 and record['finished_at'] >= record['started_at']
            commands.append({'ref': c['receipt'], 'arguments': record['arguments'],
                             'started_at': record['started_at'], 'finished_at': record['finished_at']})
            proof = record.get('verified_stdout')
            if proof:
                out, body = node(record['stdout']), node(proof['body'])
                assert key(out) == key(body) and out['bytes'] == body['bytes'] == proof['bytes']
                assert proof['verification'] == 'fresh_dump_expected_hash_match'
                assert record['arguments'] == ['dump', proof['snapshot_id'], '/backup.json']
                assert proof['storage'] == 'hardlink_by_sha256_v1'
                shared.append({'stdout': out, 'body': body, 'snapshot_id': proof['snapshot_id'],
                               'sha256': proof['sha256'], 'command_receipt': c['receipt']})
        assert {x['snapshot_id'] for x in shared} == set(a['result']['stored_snapshots'])
        shared_paths = {x['stdout']['path'] for x in shared}
        for p in (REPORTS / r['archive_run_id']).iterdir():
            if p.is_file() and str(p) not in shared_paths:
                other_files.append(node(p))
        other_files.extend([node(r['scheduled_ref']['path']), node(path)])
        audit['runs'].append({'archive_run_id': r['archive_run_id'], 'scheduled_run_id': s['run_id'],
            'started_at': a['started_at'], 'finished_at': a['finished_at'],
            'processed': a['result']['processed'], 'snapshot_ids': a['result']['stored_snapshots'],
            'scheduled_ref': r['scheduled_ref'], 'archive_ref': r['archive_ref'],
            'trigger_at': ctx['trigger_at'], 'start_job': ctx['start_job'],
            'invocation_id': attempt['invocation_id'], 'environment_matches_job': ctx['environment_matches_job'],
            'request_id': request['request_id'], 'attempt_ref': ref(path), 'attempt': attempt,
            'disposition': disposition, 'commands': commands, 'shared': shared,
            'other_files': other_files, 'other_logical_bytes': sum(n['bytes'] for n in other_files),
            'other_allocated_bytes': sum({key(n): n['allocated_bytes'] for n in other_files}.values())})
    assert [r['processed'] for r in audit['runs']] == [3, 3, 0, 0]
    for t in observed['targets']:
        sid = t['stored']['snapshot_id']
        owner = next(r for r in chronological if any(c['record']['arguments'][0] == 'backup'
                     and 'backup_id=' + t['backup_id'] in c['record']['arguments'] for c in r['commands']))
        cmds = [c for c in owner['commands'] if c['record']['arguments'] == ['dump', sid, '/checkpoint-evidence.json']]
        assert len(cmds) == 1
        cmd = cmds[0]
        raw = Path(cmd['record']['stdout']).read_bytes()
        identity = {k: t[k] for k in ('backup_id', 'manifest_sha256')}
        identity.update(source_root=str(ROOT), scope='catalog_and_all_referenced_raw_revisions_snapshots')
        decoded = checkpoint_evidence.decode_bundle(raw, identity)
        bundle = decoded['bundle']
        assert decoded['sha256'] == t['stored']['checkpoint_evidence']['sha256']
        assert decoded['object_ids'] == t['stored']['checkpoint_evidence']['object_ids']
        sidecar = Path(t['path']) / 'checkpoint-evidence.json'
        assert sidecar.read_bytes() == raw
        backup = next(c for c in owner['commands'] if c['record']['arguments'][0] == 'backup'
                      and 'backup_id=' + t['backup_id'] in c['record']['arguments'])
        assert backup['record']['cwd'] == t['path'] and backup['record']['returncode'] == 0
        out = Path(backup['record']['stdout'])
        with out.open('rb') as f:
            f.seek(max(0, out.stat().st_size - 8192))
            summary = json.loads(f.read().splitlines()[-1])
        assert summary['message_type'] == 'summary' and summary['snapshot_id'] == sid
        check = next(c for c in owner['commands'] if c['record']['arguments'] == ['check', '--read-data'])
        assert check['record']['returncode'] == 0
        assert sid in owner['archive_result']['result']['stored_snapshots']
        assert t['included_in_successful_full_check']
        completion = bundle['objects'][bundle['completion']]
        audit['targets'].append({'date': Path(t['path']).parent.name, 'backup_id': t['backup_id'],
            'manifest_sha256': t['manifest_sha256'], 'snapshot_id': sid, 'snapshot_kind': 'new_backup_exit_0_summary',
            'archive_run_id': owner['archive_run_id'], 'backup_receipt': backup['receipt'],
            'full_check_receipt': check['receipt'], 'full_check_finished_at': check['record']['finished_at'],
            'full_check_stdout': ref(check['record']['stdout']),
            'bundle_receipt': cmd['receipt'], 'bundle_stdout': ref(cmd['record']['stdout']),
            'sidecar_ref': ref(sidecar), 'object_count': len(decoded['object_ids']),
            'completion_id': bundle['completion'], 'completion_upper_utc_ns': completion['observation']['upper_utc_ns'],
            'baseline_id': completion['order']['baseline'], 'predecessors': completion['order']['predecessors'],
            'graph_reconstructed': True, 'request_and_monitor_complete': True})
    first, baseline, *noops = audit['runs']
    base_nodes = {key(x['body']): x['body'] for x in baseline['shared']}
    measurements = []
    for run in noops:
        assert run['snapshot_ids'] == baseline['snapshot_ids']
        nodes = {key(x['body']): x['body'] for x in run['shared']}
        assert nodes.keys() == base_nodes.keys()
        assert {k: n['bytes'] for k, n in nodes.items()} == {k: n['bytes'] for k, n in base_nodes.items()}
        assert not any(c['arguments'][0] in ('backup', 'check', 'restore') for c in run['commands'])
        measurements.append({'archive_run_id': run['archive_run_id'], 'processed': 0,
            'fresh_verified_dumps': len(run['shared']), 'same_snapshot_set': True,
            'large_body_new_inodes': 0, 'large_body_additional_retained_allocation_bytes': 0,
            'output_logical_bytes': sum(x['stdout']['bytes'] for x in run['shared']),
            'other_logical_bytes': run['other_logical_bytes'], 'other_allocated_bytes': run['other_allocated_bytes']})
    decisions = ROOT / 'state/scheduled-clock-policy/restic-archive/decisions.jsonl'
    per_attempt = {r['attempt']['id']: {'records': 0, 'bytes': 0} for r in audit['runs']}
    for line in decisions.read_bytes().splitlines(keepends=True):
        row = json.loads(line)
        aid = row.get('attempt_id')
        if aid in per_attempt:
            per_attempt[aid]['records'] += 1
            per_attempt[aid]['bytes'] += len(line)
    audit['decision_records_by_attempt'] = per_attempt
    audit['allocation'] = {'method': 'same_device_inode_union; no large-body byte reread',
        'baseline_archive_run_id': baseline['archive_run_id'], 'body_count': len(base_nodes),
        'body_logical_bytes': sum(n['bytes'] for n in base_nodes.values()),
        'body_unique_allocated_bytes': sum(n['allocated_bytes'] for n in base_nodes.values()),
        'natural_noops': measurements, 'temporary_independent_stdout_and_write_io_still_required': True,
        'small_file_accounting': 'run files except shared stdout + scheduled report + immutable wrapper attempt; decisions reported separately; directory metadata/current request not included'}
    v = os.statvfs(ROOT)
    audit['home_available_bytes'] = v.f_bavail * v.f_frsize
    audit['passed'] = True
    output = HERE / ('archival-audit-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    with output.open('x') as f:
        f.write(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed': audit['passed'], 'report': str(output), 'targets': len(audit['targets']),
        'runs': [{'id': r['archive_run_id'], 'processed': r['processed']} for r in audit['runs']],
        'allocation': audit['allocation'], 'decision_records_by_attempt': per_attempt,
        'home_available_bytes': audit['home_available_bytes']}, indent=2))


if __name__ == '__main__':
    main()
