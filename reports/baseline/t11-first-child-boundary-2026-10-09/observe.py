"""Read-only observation of the deployed T11 first child boundary.

Use the installed collector's Python. No Store initialization, provider control,
service changes, exchange requests, or backup writes. Outputs use exclusive create.
Full CLI responses and original artifact copies are kept in ignored reports/.
"""

from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
OUT = Path(__file__).resolve().parent
RAW = ROOT / 'reports/t11-first-child-boundary-2026-10-09'
RELEASE = Path('/opt/marketdata/releases/clock-evidence-policy-20261009-65296619a374c325')
CASES = {
    'depth': ('d2d18424592f4f549f5bda828e270759', '353238d9baf4406aa072eee7b1e5b4ea'),
    'aggtrade': ('c5b36646466344d5b1672275ffb2faf8', '558440612aa14970be0046d783997b89'),
}
REPAIR = '4ebe36bb51c14af68d20a3462b7ada07'


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def save(path, value):
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.write('\n')


def retain(name, blob):
    path = RAW / name
    if path.exists():
        assert path.read_bytes() == blob, 'Existing evidence differs: ' + str(path)
    else:
        with path.open('xb') as f:
            f.write(blob)
    return {'path': str(path.relative_to(ROOT)), 'bytes': len(blob), 'sha256': sha(blob)}


def utc(ns):
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat()


def run(argv):
    p = subprocess.run(list(map(str, argv)), capture_output=True, timeout=60)
    return {'argv': list(map(str, argv)), 'code': p.returncode,
            'stdout': p.stdout.decode(), 'stderr': p.stderr.decode()}


def host():
    plan = json.loads((OUT.parent / 'clock-runtime-transition-2026-10-09/operating-plan-final.json').read_text())
    ledger_path = Path('/var/lib/marketdata-clock/boot-state.json')
    ledger = json.loads(ledger_path.read_text())
    paths = set(v['path'] for v in plan['files'].values()) | {
        '/etc/chrony/marketdata.conf', '/etc/adjtime', str(ledger_path)}
    result = {
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'files': {p: sha(Path(p).read_bytes()) for p in sorted(paths)},
        'approved_files_match': all(sha(Path(v['path']).read_bytes()) == v['after_sha256']
                                    for v in plan['files'].values()),
        'original_boot_step_evidence_preserved': all(ledger.get(k) == v for k, v in plan['ledger_before'].items()
                                                    if k not in ('profile_sha256', 'configuration_history')),
        'ledger': ledger,
        'masks': {n: os.readlink(Path('/home/yoonhc/.config/systemd/user') / n) for n in (
            'marketdata-depth.service', 'marketdata-aggtrade.service',
            'marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer')},
        'streams': {},
    }
    for stream in CASES:
        latest = json.loads((ROOT / 'state/runtime-clock/v2' / stream / 'latest.json').read_text())
        result['streams'][stream] = {'latest': latest, 'processes': []}
        for key in ('supervisor_pid', 'child_pid'):
            pid = latest[key]
            proc = Path('/proc') / str(pid)
            result['streams'][stream]['processes'].append({
                'role': key, 'pid': pid, 'argv': (proc / 'cmdline').read_bytes().decode().split('\0'),
                'status': [s for s in (proc / 'status').read_text().splitlines()
                           if s.startswith(('Name:', 'State:', 'PPid:', 'Uid:', 'Gid:'))],
            })
    result['systemd'] = run(['/usr/bin/systemctl', 'show', 'chrony.service',
        'marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service', '--all',
        '--property=Id,ActiveState,SubState,MainPID,InvocationID,User,RuntimeMaxUSec,Restart,KillMode,KillSignal,TimeoutStopUSec,ExecMainStartTimestamp'])
    assert result['systemd']['code'] == 0, result['systemd']
    result['passed'] = (result['approved_files_match'] and result['original_boot_step_evidence_preserved']
                        and all(v == '/dev/null' for v in result['masks'].values()))
    save(OUT / 'host-state.json', result)
    print(json.dumps({'host_passed': result['passed'], 'observed_at': result['observed_at']}))


def collection(db, identity):
    result = dict(db.execute('SELECT * FROM event_collections WHERE id=?', (identity,)).fetchone())
    result['report'] = json.loads(result['report'])
    result['segments'] = [dict(r) for r in db.execute(
        'SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id', (identity,))]
    return result


def brief(item):
    keys = ('messages', 'persisted_messages', 'snapshots', 'complete', 'sync_losses', 'coverage_resets',
            'coverage_top_shortfalls', 'synchronized', 'unreconstructed_messages', 'connection_errors',
            'connections', 'first_id', 'last_id', 'last_update_id', 'last_received_at',
            'previous_persisted_id', 'detected_gaps', 'repaired_gaps', 'unrepaired_gaps',
            'repairs', 'failure_notice', 'runtime_clock', 'errors')
    return {k: v for k, v in item.items() if k not in ('segments', 'report')} | {
        k: item['report'][k] for k in keys if k in item['report']} | {
        'segments': len(item['segments']), 'stored_rows': sum(s['row_count'] for s in item['segments'])}


def market_sample(db, item, stream, all_segments=False):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from marketdata.fileio import read_file
    selected = item['segments'] if all_segments else item['segments'][:1] + item['segments'][-1:]
    if stream == 'depth' and item['id'] == CASES['depth'][0]:
        selected += [s for s in item['segments'] if s['first_id'] <= 34626 <= s['last_id']]
    selected = list({s['id']: s for s in selected}.values())
    proofs, id_values = [], []
    for s in selected:
        blob = read_file(ROOT, db, s['path'])
        assert sha(blob) == s['file_sha']
        columns = (['sequence', 'kind', 'epoch', 'received_at', 'final_update_id', 'previous_update_id']
                   if stream == 'depth' else ['agg_trade_id', 'received_at'])
        rows = pq.ParquetFile(pa.BufferReader(blob)).read(columns=columns).to_pylist()
        identity = 'sequence' if stream == 'depth' else 'agg_trade_id'
        ids = [r[identity] for r in rows]
        assert (len(rows), ids[0], ids[-1]) == (s['row_count'], s['first_id'], s['last_id'])
        source = dict(db.execute('SELECT * FROM artifacts WHERE id=?', (s['source_id'],)).fetchone())
        raw = read_file(ROOT, db, source['path'])
        assert sha(raw) == source['id']
        proof = {'segment': s, 'raw_artifact': source, 'hashes_match': True,
                 'rows_checked': len(rows), 'first_row': rows[0], 'last_row': rows[-1]}
        if stream == 'depth':
            proof['failure_rows'] = [r for r in rows if r['sequence'] == 34626]
        proofs.append(proof)
        id_values.extend(ids)
    return {'full_collection': all_segments, 'proofs': proofs, 'rows_checked': len(id_values),
            'contiguous_ids': id_values == list(range(id_values[0], id_values[-1] + 1)) if all_segments else None,
            'id_min': min(id_values), 'id_max': max(id_values)}


def clock_records(db, stream, first, second):
    from marketdata.fileio import read_file
    link = first['report']['runtime_clock']
    start2 = second['report']['runtime_clock']['collection_start']['boottime_ns']
    prefix = 'runtime-clock://' + link['run_id'] + '/'
    artifacts = []
    for r in db.execute('SELECT a.*,f.url,f.metadata FROM artifacts a JOIN fetches f ON f.artifact_id=a.id '
                        'WHERE f.url>=? AND f.url<?', (prefix, prefix + '\uffff')):
        r = dict(r)
        r['metadata'] = json.loads(r['metadata'])
        if r['metadata']['first_sample']['boottime_ns'] <= start2 + 10_000_000_000:
            artifacts.append(r)
    artifacts.sort(key=lambda a: a['metadata']['chunk'])
    rows, previous, index = [], None, 0
    for number, a in enumerate(artifacts):
        blob = read_file(ROOT, db, a['path'])
        assert sha(blob) == a['id']
        decoded = gzip.decompress(blob)
        lines = [json.loads(line) for line in decoded.splitlines()]
        header, records = lines[0], lines[1:]
        m = a['metadata']
        assert number == m['chunk'] == header['chunk']
        assert previous == m['previous'] == header['previous']
        assert header['run_id'] == m['run_id'] == link['run_id']
        assert len(records) == m['records'] and sha(decoded) == m['uncompressed_sha256']
        assert records[0]['sample'] == m['first_sample'] and records[-1]['sample'] == m['last_sample']
        for r in records:
            assert r['record'] == index and r['boot_id'] == link['boot_id'] and r['run_id'] == link['run_id']
            assert r['stream'] == stream and r['policy_version'] == link['policy_version']
            index += 1
            rows.append({'artifact_id': a['id'], 'row': r})
        previous = a['id']
        a['retained_original'] = retain(a['id'] + '.jsonl.gz', blob)
    rows = [r for r in rows if r['row']['sample']['boottime_ns'] <= start2 + 10_000_000_000]
    finished = [r for r in rows if r['row']['state'] == 'child_finished' and r['row'].get('child_id') == link['child_id']]
    assert len(finished) == 1
    finish = finished[0]['row']
    following = [r for r in rows if r['row']['record'] > finish['record']]
    starting = next(r for r in following if r['row']['state'] == 'starting_child')
    active = next(r for r in following if r['row']['state'] == 'running' and r['row'].get('child_pid'))
    last_record = active['row']['record']
    gap_rows = [r for r in rows if finish['record'] <= r['row']['record'] <= last_record]
    samples = [r['row']['sample']['boottime_ns'] for r in gap_rows]
    all_samples = [r['row']['sample']['boottime_ns'] for r in rows]
    assert all(b >= a for a, b in zip(all_samples, all_samples[1:]))
    continuity = {k: sorted({str(r['row'].get(k)) for r in rows}) for k in
                  ('run_id', 'boot_id', 'supervisor_pid', 'supervisor_start_ticks', 'systemd_invocation_id', 'profile_sha256')}
    assert all(len(v) == 1 for v in continuity.values())
    decisions = [r['row'] for r in rows if 'decision' in r['row']]
    historical = [r for r in decisions if r.get('history', {}).get('reference_stale_seen')]
    if historical:
        assert all(r['history']['reference_stale_seen'] for r in decisions if r['record'] >= historical[0]['record'])
    assert finish['collector_exit_code'] == (2 if stream == 'depth' else 0)
    if stream == 'depth':
        assert finish['terminal_partial_confirmed']
    assert active['row']['child_pid'] != finish['child_pid']
    assert first['report']['runtime_clock']['run_id'] == second['report']['runtime_clock']['run_id']
    assert first['report']['runtime_clock']['child_id'] != second['report']['runtime_clock']['child_id']
    assert starting['row']['decision']['raw_start_allowed']
    assert not any(r['row']['state'] == 'start_refused_after_commit' for r in gap_rows)
    assert all(r['row']['decision']['current_estimate_usable'] for r in gap_rows)
    assert max(b-a for a, b in zip(samples, samples[1:])) <= 5e9
    result = {
        'identities': continuity, 'artifacts': artifacts, 'records_checked': len(rows),
        'first_child_finished': finished[0], 'next_start_decision': starting, 'next_running': active,
        'boundary_original_records': gap_rows,
        'wait_after_child_finished_seconds': (starting['row']['sample']['boottime_ns'] - finish['sample']['boottime_ns']) / 1e9,
        'terminal_collection_to_next_start_seconds': (start2 - link['collection_end']['boottime_ns']) / 1e9,
        'max_boundary_sample_gap_seconds': max(b-a for a, b in zip(samples, samples[1:])) / 1e9,
        'max_checked_sample_gap_seconds': max(b-a for a, b in zip(all_samples, all_samples[1:])) / 1e9,
        'boundary_stale_start': starting['row']['decision']['reference_stale'],
        'past_stale_preserved': bool(historical),
        'hard_fault_seen': any(r.get('history', {}).get('hard_fault_seen') for r in decisions),
        'state_counts': dict(Counter(r['row']['state'] for r in rows)),
    }
    save(OUT / (stream + '-boundary.json'), result)
    return {k: v for k, v in result.items() if k not in ('artifacts', 'boundary_original_records', 'first_child_finished', 'next_start_decision', 'next_running')} | {
        'artifact_count': len(artifacts), 'first_exit_code': finish['collector_exit_code'],
        'first_child_pid': finish['child_pid'], 'next_child_pid': active['row']['child_pid'],
        'finished_utc': utc(finish['sample']['wall_ns']), 'next_start_utc': utc(starting['row']['sample']['wall_ns']),
        'next_start_decision': starting['row']['decision'], 'next_start_clock': starting['row']['clock'],
    }


def cli_query(stream, identity):
    cmd = [RELEASE / '.venv/bin/python', '-B', '-I', '-c',
           'from marketdata.cli import main; raise SystemExit(main())', '--root', ROOT,
           'clock-evidence', '--collection-id', identity]
    response = run(cmd)
    stdout = response.pop('stdout').encode()
    query = json.loads(stdout)
    response['retained_stdout_gzip'] = retain(stream + '-clock-query.json.gz', gzip.compress(stdout, mtime=0))
    response['stdout_sha256'] = sha(stdout)
    assert response['code'] == 0 and query['evidence_state'] == 'available' and not query['unknown_reasons']
    intervals = query['intervals']
    assert intervals[0]['start_boottime_ns'] == query['link']['collection_start']['boottime_ns']
    assert intervals[-1]['end_boottime_ns'] == query['link']['collection_end']['boottime_ns']
    assert all(a['end_boottime_ns'] == b['start_boottime_ns'] for a, b in zip(intervals, intervals[1:]))
    summary = {k: v for k, v in query.items() if k != 'intervals'} | {
        'cli': response, 'intervals': len(intervals),
        'stale_intervals': sum(r['reference_stale'] is True for r in intervals),
        'unusable_intervals': sum(not r['current_estimate_usable'] for r in intervals),
        'raw_start_refused_intervals': sum(not r['raw_start_allowed'] for r in intervals),
        'max_reference_age_seconds': max(r['clock_observation']['reference_age_seconds'] for r in intervals),
        'first_interval': intervals[0], 'last_interval': intervals[-1],
    }
    save(OUT / (stream + '-clock-query.json'), summary)
    return {k: summary[k] for k in ('collection_id', 'evidence_state', 'unknown_reasons', 'chunks', 'link', 'intervals',
                                   'stale_intervals', 'unusable_intervals', 'raw_start_refused_intervals')}


def data():
    RAW.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect((ROOT / 'state/catalog.sqlite').as_uri() + '?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    result = {'observed_at': datetime.now(timezone.utc).isoformat(), 'streams': {}, 'collection_summaries': {}}
    all_items = {identity: collection(db, identity) for identities in CASES.values() for identity in identities}
    all_items[REPAIR] = collection(db, REPAIR)
    result['full_catalog_records'] = retain('catalog-records.json.gz', gzip.compress(
        json.dumps(all_items, ensure_ascii=False).encode(), mtime=0))
    proofs = {}
    for stream, (first_id, second_id) in CASES.items():
        first, second = all_items[first_id], all_items[second_id]
        assert first['state'] == ('partial' if stream == 'depth' else 'complete')
        assert first['report']['messages'] == first['report']['persisted_messages']
        expected_rows = first['report']['persisted_messages'] + (first['report']['snapshots'] if stream == 'depth' else 0)
        assert expected_rows == sum(s['row_count'] for s in first['segments'])
        result['streams'][stream] = clock_records(db, stream, first, second)
        for item in (first, second):
            result['collection_summaries'][item['id']] = brief(item)
            proofs[item['id']] = market_sample(db, item, stream)
    repair = all_items[REPAIR]
    result['collection_summaries'][REPAIR] = brief(repair)
    proofs[REPAIR] = market_sample(db, repair, 'aggtrade', all_segments=True)
    assert repair['state'] == 'complete' and proofs[REPAIR]['contiguous_ids']
    assert proofs[REPAIR]['id_min'] == all_items[CASES['aggtrade'][0]]['report']['last_id'] + 1
    assert proofs[REPAIR]['id_max'] + 1 == all_items[CASES['aggtrade'][1]]['segments'][0]['first_id']
    db.close()
    save(OUT / 'market-boundary-files.json', proofs)
    result['clock_queries'] = {stream: cli_query(stream, ids[0]) for stream, ids in CASES.items()}
    result['passed'] = True
    save(OUT / 'result.json', result)
    print(json.dumps({'passed': True, 'boundaries': result['streams'],
                      'queries': {s: {k: v for k, v in q.items() if k not in ('chunks', 'link')} for s, q in result['clock_queries'].items()}}))


def verify():
    result = json.loads((OUT / 'result.json').read_text())
    host_result = json.loads((OUT / 'host-state.json').read_text())
    initial = json.loads((OUT.parent / 'clock-runtime-deploy-2026-10-09/operating-observation-20261009T104329Z.json').read_text())
    manifest_path = OUT.parent / 'clock-runtime-transition-2026-10-09/collector-correction/release.json'
    manifest = json.loads(manifest_path.read_text())
    source_prefix = 'packages/marketdata/src/'
    files = {}
    for relative, expected in manifest['source_files_sha256'].items():
        if relative.startswith(source_prefix) and relative.endswith('.py'):
            path = RELEASE / '.venv/lib/python3.12/site-packages' / relative.removeprefix(source_prefix)
        elif relative == 'integrations/runtime/capture_runtime.py':
            path = RELEASE / relative
        else:
            continue
        actual = sha(path.read_bytes())
        assert actual == expected, str(path)
        files[str(path)] = actual
    checks = {
        'data_observation_passed': result['passed'], 'host_observation_passed': host_result['passed'],
        'ledger_matches_initial_observation': host_result['ledger'] == initial['ledger'],
        'original_config_still_matches': all(sha(Path(p).read_bytes()) == h for p, h in host_result['files'].items()),
        'chrony_config_and_rtc_mode_unchanged': all(host_result['files'][p] == initial['non_target_configuration_hashes'][p]
                                                 for p in ('/etc/chrony/marketdata.conf', '/etc/adjtime')),
        'both_boundaries_started_while_reference_stale': all(s['boundary_stale_start'] for s in result['streams'].values()),
        'supervisors_match_first_observation': all(
            str(initial['streams'][s]['latest']['supervisor_pid']) == result['streams'][s]['identities']['supervisor_pid'][0]
            == str(host_result['streams'][s]['latest']['supervisor_pid']) and
            initial['streams'][s]['latest']['systemd_invocation_id'] == result['streams'][s]['identities']['systemd_invocation_id'][0]
            == host_result['streams'][s]['latest']['systemd_invocation_id'] for s in CASES),
    }
    assert all(checks.values()), checks
    targets = {}
    for stream, q in result['clock_queries'].items():
        boundary = json.loads((OUT / (stream + '-boundary.json')).read_text())
        query_summary = json.loads((OUT / (stream + '-clock-query.json')).read_text())
        full_query = json.loads(gzip.decompress((ROOT / query_summary['cli']['retained_stdout_gzip']['path']).read_bytes()))
        targets[stream] = {
            'collection_id': q['collection_id'], 'link': q['link'], 'evidence_state': q['evidence_state'],
            'query_artifacts': q['chunks'], 'intervals': q['intervals'],
            'query_semantic_sha256': sha(json.dumps(full_query, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()),
            'collection_report_sha256': sha(json.dumps(json.loads(gzip.decompress(
                (ROOT / result['full_catalog_records']['path']).read_bytes()))[q['collection_id']]['report'],
                sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()),
            'artifact_chain_prefix': [{k: a[k] for k in ('id', 'path', 'bytes', 'url', 'metadata')} for a in boundary['artifacts']],
        }
    save(OUT / 'backup-followup.json', {
        'state': 'operating_checkpoint_restic_restore_pending', 'release_id': RELEASE.name,
        'targets': targets, 'next_scheduled_checkpoint_kst': '2026-10-10T13:10:00+09:00',
        'required': ['actual completed checkpoint/backup ID', 'same backup ID restic snapshot and full read-data check',
                     'explicit snapshot extraction into separate location', 'application restore and same collection evidence query'],
        'not_proven': 'Current local availability does not establish inclusion in an operating recovery point.',
    })
    save(OUT / 'verification.json', {'verified_at': datetime.now(timezone.utc).isoformat(),
        'checks': checks, 'passed': True, 'release_manifest': str(manifest_path.relative_to(ROOT)),
        'release_manifest_sha256': sha(manifest_path.read_bytes()), 'installed_files_sha256': files,
        'operational_changes': [], 'new_exchange_requests': 0, 'repeated_suite_runs': 0})
    print(json.dumps({'passed': True, 'installed_files_checked': len(files), 'checks': checks}))


if __name__ == '__main__':
    if sys.argv[1:] == ['--host']:
        host()
    elif sys.argv[1:] == ['--data']:
        data()
    elif sys.argv[1:] == ['--verify']:
        verify()
    else:
        raise SystemExit('Choose --host, --data, or --verify; only report paths are written.')
