"""Verify the completed installed preview; no new preview, restore or data audit."""
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELEASE = Path('/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7')
sys.path[:0] = [str(RELEASE), str(RELEASE / 'packages/marketdata/src')]
from marketdata import checkpoint_evidence as evidence
from marketdata import checkpoint_clock as clock
from marketdata.domain import json_bytes
from scripts import clock_retirement as retirement

RUN_ID = '9690925e44194b73bfba206ba40360e7'
RUN = Path('/var/lib/marketdata-checkpoint-retirement/runs') / RUN_ID
LATEST_BID = '9b916470eca7e5da1462dff9c39380869242bf6711861eaea754c010ccf00963'
LATEST_SID = '0911b25d3a270fad604eeb222748e950ea45383b68d9b451e6d785b92672c58f'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(name, value):
    path = HERE / name
    raw = value if isinstance(value, bytes) else json_bytes(value)
    with path.open('xb') as handle:
        handle.write(raw)
    return {'path': name, 'sha256': sha(raw), 'bytes': len(raw)}


def main():
    raw = (RUN / 'run.json').read_bytes()
    run = json.loads(raw)
    assert run['action'] == 'preview' and run['state'] in ('preview_ready', 'no_candidates')
    assert run['ok'] and run['exit_code'] == 0 and run['selection_assessed']
    assert not run['deletion_attempted'] and run['deleted_this_run'] == 0
    assert not run['authorization']['enabled']
    run_copy = save('run.json', raw)
    plan_path = Path('/var/lib/marketdata-checkpoint-retirement/retention/plans') / (run['plan_id'] + '.json')
    plan_raw = plan_path.read_bytes()
    plan = json.loads(plan_raw)
    assert plan == run['plan']
    assert sha(json_bytes({k:v for k,v in plan.items() if k != 'plan_id'})) == plan['plan_id']
    assert plan['code'] == retirement.retirement.current_code()
    assert plan['automation']['scope_id'] == run['authorization']['scope_id']
    plan_copy = save('plan.json', plan_raw)
    observation = plan['clock_safety']['observation']
    clock.validate_observation(observation)
    graph = evidence.Graph(plan['clock_safety']['objects'], plan['identity'])
    entries = plan['keep'] + [c['entry'] for c in plan['candidates']]
    keep, candidates, proofs = evidence.select(entries, graph, observation['lower_utc_ns'],
                                              keep_latest=plan['keep_latest'], min_age_hours=plan['min_age_hours'])
    assert {x['backup_id'] for x in keep} == {x['backup_id'] for x in plan['keep']}
    assert {x['backup_id'] for x in candidates} == {x['entry']['backup_id'] for x in plan['candidates']}
    assert proofs == plan['clock_safety']['completion_bounds']
    candidate_by_id = {c['entry']['backup_id']: c for c in plan['candidates']}
    snapshots = {e['backup_id']:e['snapshot_id'] for e in plan['keep']}
    snapshots.update({c['entry']['backup_id']:c['snapshot_id'] for c in plan['candidates']})
    table = []
    for entry in sorted(keep + candidates, key=lambda e: e['path']):
        bid = entry['backup_id']
        proof = proofs[bid]
        table.append({'disposition':'retained' if entry['protection'] else 'candidate',
                      'path':entry['path'], 'backup_id':bid, 'manifest_sha256':entry['manifest_sha256'],
                      'snapshot_id':snapshots[bid], 'protection':entry['protection'],
                      'conservative_age_seconds':entry['age_seconds'],
                      'minimum_age_seconds':plan['min_age_hours']*3600+clock.POLICY['retention_margin_seconds'],
                      'completion_proof':proof['completion'],
                      'retained_successors':proof['retained_successors'],
                      'tree_sha256':candidate_by_id.get(bid,{}).get('tree_sha256')})
    command_evidence = []
    latest_bundles = []
    for path in sorted((RUN/'commands').glob('*.json')):
        raw_receipt = path.read_bytes()
        receipt = json.loads(raw_receipt)
        assert receipt['returncode'] == 0
        args = receipt['arguments']
        assert args[0] in ('cat','snapshots','dump'), args
        info = {'source':str(path), 'receipt_sha256':sha(raw_receipt), 'receipt':receipt}
        if args[0] == 'dump' and args[-1] == '/checkpoint-evidence.json':
            raw_bundle = Path(receipt['stdout']).read_bytes()
            decoded = evidence.decode_bundle(raw_bundle)
            assert decoded['bundle']['scope'] == plan['identity']
            for digest, value in decoded['bundle']['objects'].items():
                assert graph.objects[digest] == value
                assert {'snapshot_id':args[1], 'bundle_sha256':decoded['sha256']} in plan['clock_safety']['locations'][digest]
            info['bundle'] = save('bundle-' + path.stem + '.json', raw_bundle)
            if args[1] == LATEST_SID:
                assert decoded['bundle']['identity']['backup_id'] == LATEST_BID
                entry = next(e for e in entries if e['backup_id'] == LATEST_BID)
                assert sha((Path(entry['path'])/'checkpoint-evidence.json').read_bytes()) == decoded['sha256']
                independent = evidence.Graph(decoded['bundle']['objects'], plan['identity'])
                latest_bundles.append({'receipt':info,'objects':len(independent.objects),
                    'proof':independent.proof(decoded['bundle']['identity']),
                    'ancestors':sorted(independent.ancestors(LATEST_BID))})
        command_evidence.append(info)
    assert latest_bundles
    events = [json.loads(line) for line in (RUN/'clock.jsonl').read_bytes().splitlines() if line.strip()]
    clocks = [e['observation'] for e in events if e.get('kind') == 'provider']
    for obs in clocks:
        clock.validate_observation(obs)
    after = json.loads((HERE/'after.json').read_bytes())
    assert after['passed'] and after['protected_processes_unchanged']
    assert after['new_runs'] == [RUN_ID] and not after['new_registry_files']
    clock_copy = save('clock.jsonl', (RUN/'clock.jsonl').read_bytes())
    command_copy = save('command-receipts.json', command_evidence)
    save('selection.json', table)
    result = {'checked_at':datetime.now(timezone.utc).isoformat(), 'passed':True,
              'run_id':RUN_ID,'plan_id':plan['plan_id'],'scope_id':run['authorization']['scope_id'],
              'started_at':run['started_at'],'finished_at':run['finished_at'],
              'counts':{'managed':len(entries),'candidates':len(candidates),'retained':len(keep),'excluded':len(plan['excluded'])},
              'uncertainty_seconds':observation['uncertainty_seconds'],
              'decision_lower_utc_ns':observation['lower_utc_ns'],
              'clock_observations':len(clocks),
              'reference_age_range_seconds':[min(c['reference_age_seconds'] for c in clocks),max(c['reference_age_seconds'] for c in clocks)],
              'objects':len(graph.objects),'baselines':list(graph.baselines),'space':plan['space'],
              'commands':dict(Counter(' '.join(x['receipt']['arguments'][:1]+x['receipt']['arguments'][-1:]) for x in command_evidence)),
              'latest_snapshot_bundle':latest_bundles,'run_copy':run_copy,'plan_copy':plan_copy,
              'clock_copy':clock_copy,'commands_copy':command_copy,'registry_unchanged':True,
              'protected_processes_unchanged':True,'retained_full_content_audit_performed':False,
              'candidate_metadata_inventory_performed_by_preview':True,
              'independent_reinventory_performed':False,'bulk_restore':False,'new_restic_full_check':False,
              'deletion_attempted':False,'automatic_retirement':'masked','deletion_authority':False}
    save('verification.json', result)
    print(json.dumps({k:result[k] for k in ['passed','plan_id','counts','uncertainty_seconds','objects','space','commands']},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
