"""Read saved T05 evidence and small filesystem metadata; never invoke a worker.

Writes only this report directory. Imports pure evidence classification from the
installed release; does not query restic, acquire workload locks, or verify data.
Host ownership and systemd observations are separately recorded.
"""
import hashlib
import json
import os
import stat
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[2]
STATE = Path('/var/lib/marketdata-checkpoint-retirement')
RUN = '7712cfd499d845cd988cad6cf5b818a4'
PLAN = 'e886258a9eed7ac0f22e540cd04c60bf1d53b8819176d0c17b1b43cf652aec0f'
RELEASE = Path('/opt/marketdata/releases/t05-recurring-retirement-20261018-e57a7995f03fb301')
DEPLOY = REPO / 'reports/baseline/t05-recurring-deploy-2026-10-18'
ARCHIVE = REPO / 'archive-state/b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558/historical-data-original-v1/state.json'
sys.path[:0] = [str(RELEASE), str(RELEASE / 'packages/marketdata/src')]
from marketdata.domain import json_bytes
from marketdata.checkpoint_evidence import Graph, decode_bundle, select
from scripts.clock_retirement import safely_deferred_before_application

sources = {}

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def raw(path, copy=None):
    path = Path(path)
    data = path.read_bytes()
    sources[str(path)] = {'sha256': digest(data), 'bytes': len(data)}
    if copy:
        target = OUT / 'evidence' / copy
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return data

def read(path, copy=None):
    return json.loads(raw(path, copy))

def node(path):
    path = Path(path)
    s = path.lstat()
    result = {'mode': stat.S_IMODE(s.st_mode)}
    if path.is_symlink():
        result.update(kind='symlink', target=os.readlink(path))
    else:
        assert stat.S_ISREG(s.st_mode), str(path)
        result.update(kind='file', sha256=digest(raw(path)))
    return result

run = read(STATE / 'runs' / RUN / 'run.json', 'run.json')
plan = read(STATE / 'retention/plans' / (PLAN + '.json'), 'plan.json')
journal = read(STATE / 'retention/journals' / (PLAN + '.json'), 'journal.json')
clock_rows = [json.loads(line) for line in raw(STATE / 'runs' / RUN / 'clock.jsonl', 'clock.jsonl').splitlines()]
op = read(DEPLOY / 'operation-plan.json')
activation = read(DEPLOY / 'activation-audit-20261018.json')
assert run['state'] == 'complete' and run['phase'] == 'finished' and run['exit_code'] == 0
assert run['action'] == 'run' and run['ok'] and run['deleted_this_run'] == 2
assert not run['needs_attention'] and not run['unfinished_retirement'] and not run['already_complete']
assert run['completed_before_run'] == 0 and run['plan'] == plan
assert run['result']['journal'] == journal and journal['status'] == 'complete'
assert len(journal['attempts']) == 1 and journal['attempts'][0]['ok']
assert plan['plan_id'] == PLAN == digest(json_bytes({k:v for k,v in plan.items() if k != 'plan_id'}))
assert plan['automation'] == {'run_id': RUN, 'scope_id': activation['scope_id']}
assert run['authorization']['grant_sha256'] == activation['active_grant_sha256']
assert run['authorization']['scope_id'] == activation['scope_id']
assert not run['authorization']['resume_interrupted'] and run['authorization']['plan_id'] is None
ctx = run['context']
assert ctx['classification'] == 'verified_timer' and ctx['scheduled'] and ctx['service_verified']

commands = []
bundle = None
daily = run['daily_evidence']
archive = read(ARCHIVE, 'archive-state.json')
stored = archive['checkpoints'][daily['backup_id']]
check = archive['repository_check']
assert stored['stored']['snapshot_id'] == daily['snapshot_id']
assert check['ok'] and check['read_data'] and check['status'] == 'complete'
assert daily['snapshot_id'] in check['snapshot_ids']
assert check['finished_at'] == daily['full_check_finished_at'] < run['started_at']
assert sorted(check['snapshot_ids']) == sorted(plan['checked_snapshot_ids'])
for path in sorted((STATE / 'runs' / RUN / 'commands').glob('*.json')):
    receipt = read(path)
    assert receipt['returncode'] == 0
    output = raw(receipt['stdout'])
    raw(receipt['stderr'])
    commands.append({'receipt': str(path), **receipt, 'stdout_sha256': digest(output)})
    if receipt['arguments'] == ['dump', daily['snapshot_id'], '/checkpoint-evidence.json']:
        assert digest(output) == stored['stored']['checkpoint_evidence']['sha256']
        bundle = decode_bundle(output)
        raw(receipt['stdout'], 'snapshot-checkpoint-evidence.json')
assert bundle and bundle['object_ids'] == stored['stored']['checkpoint_evidence']['object_ids']
assert bundle['bundle']['identity']['backup_id'] == daily['backup_id']
assert bundle['bundle']['objects'] == plan['clock_safety']['objects']

maintenance = read(REPO / 'state/maintenance-daily-latest.json', 'maintenance-daily.json')
assert sources[str(REPO / 'state/maintenance-daily-latest.json')]['sha256'] == daily['daily_report_sha256']
assert maintenance['id'] == daily['daily_id'] and maintenance['ok'] and maintenance['phase'] == 'finished'
assert maintenance['backup']['backup_id'] == daily['backup_id'] and not maintenance['backup']['reused']
assert maintenance['validation']['ok'] and not maintenance['validation']['failures']
attempt = next(x for x in stored['attempts'] if x.get('snapshot_id') == daily['snapshot_id'])
assert attempt['status'] == 'stored' and attempt['returncode'] == 0
assert attempt['command']['finished_at'] == daily['archive_finished_at']
assert digest(raw(attempt['command']['stdout'], 'archive.stdout')) == daily['archive_stdout_sha256']
raw(attempt['command']['stderr'], 'archive.stderr')
assert check['receipt']['arguments'] == ['check', '--read-data'] and check['receipt']['returncode'] == 0
assert digest(raw(check['receipt']['stdout'], 'check.stdout')) == daily['check_stdout_sha256']
raw(check['receipt']['stderr'], 'check.stderr')
archive_run = read(Path(attempt['command']['stdout']).parent / 'run.json', 'archive-run.json')
assert archive_run['ok']

safety = plan['clock_safety']
graph = Graph(safety['objects'], plan['identity'])
entries = plan['keep'] + [c['entry'] for c in plan['candidates']]
kept, candidates, proofs = select(entries, graph, safety['observation']['lower_utc_ns'], keep_latest=3, min_age_hours=72)
assert proofs == safety['completion_bounds']
assert {x['backup_id'] for x in kept} == {x['backup_id'] for x in plan['keep']}
assert {x['backup_id'] for x in candidates} == set(journal['items'])
observations = [x['observation'] for x in clock_rows]
assert all(x['kind'] == 'provider' for x in clock_rows)
assert all(x['ready'] and not x['reasons'] and x['total_uncertainty'] <= 5 for x in observations)
deleted = []
for bid, item in journal['items'].items():
    assert item['phase'] == 'complete' and item['files_absent_when_resumed'] == 0
    assert not os.path.lexists(item['entry']['path']) and not os.path.lexists(item['staged'])
    inventory = STATE / 'retention/inventories' / PLAN / (bid + '.json')
    inventory_sha = digest(raw(inventory))
    assert inventory_sha == item['tree_sha256']
    proof = proofs[bid]
    age = (safety['observation']['lower_utc_ns'] - proof['completion']['completion_upper_utc_ns']) / 1e9
    assert age >= 72*3600+60 and len(proof['retained_successors']) == 3
    assert item['snapshot_id'] in check['snapshot_ids']
    deleted.append({'backup_id':bid,'entry':item['entry'],'snapshot_id':item['snapshot_id'], 'source_absent':True,'staging_absent':True,'unlinked_files':item['unlinked_files'],'inventory_sha256':inventory_sha,'conservative_age_seconds_at_plan':age,'completion_proof':proof})
retained = []
for category in ['keep','excluded']:
    for entry in plan[category]:
        marker_path = Path(entry['path']) / 'backup.json'
        marker = read(marker_path)
        assert marker['backup_id'] == entry['backup_id']
        assert sources[str(marker_path)]['sha256'] == entry['manifest_sha256']
        retained.append({'category':category,'path':entry['path'],'backup_id':entry['backup_id'],'manifest_sha256':entry['manifest_sha256']})
actual = {str(p.parent) for p in Path('/mnt/backup/marketdata-historical/automatic').glob('*/*/backup.json')}
assert actual == {x['path'] for x in retained}
stage = Path(next(iter(journal['items'].values()))['staged']).parent
assert not stage.exists() or not list(stage.iterdir())
unfinished = []
all_journals = list((STATE / 'retention/journals').glob('*.json'))
# The historical engine state also has a journal set; its path is in the plan profile.
all_journals += list(ARCHIVE.parent.joinpath('working-retention/journals').glob('*.json'))
complete = set()
for path in all_journals:
    obj = read(path)
    if obj['status'] == 'complete':
        complete.add(obj['plan_id'])
    else:
        unfinished.append(str(path))
for path in (STATE / 'runs').glob('*/run.json'):
    obj = read(path)
    if obj.get('deletion_attempted') and obj.get('plan_id') not in complete and not safely_deferred_before_application(obj):
        unfinished.append(str(path))
assert not unfinished
all_runs = sorted(p.parent.name for p in (STATE / 'runs').glob('*/run.json'))
assert set(all_runs) - {x['run_id'] for x in activation['runs_at_audit']} == {RUN}
for prior in activation['runs_at_audit']:
    assert digest(raw(prior['path'])) == prior['sha256']

verified_nodes = {}
for group, expected_nodes in [('after',activation['after_nodes']),('protected',op['protected_files']),('preserved',op['preserved_records'])]:
    for name, expected in expected_nodes.items():
        observed = node(name)
        assert observed == {k:v for k,v in expected.items() if k not in ('uid','gid')}, (group,name,observed)
        verified_nodes[name] = {'group':group, **observed}
for name, expected_hash in plan['code']['source_files'].items():
    assert digest(raw(RELEASE / name)) == expected_hash
for oid, obj in safety['objects'].items():
    assert read(STATE / 'evidence/objects' / (oid+'.json')) == obj
grant = read('/etc/marketdata/checkpoint-clock-retirement/recurring.json', 'recurring.json')
manual = read('/etc/marketdata/checkpoint-clock-retirement/manual.json', 'manual.json')
assert grant['enabled'] and grant['scope_id'] == activation['scope_id'] and not manual['enabled']
assert grant['approved_at'] == activation['authority']['approved_at']
assert read(grant['approval_reference'], 'authority.json') == activation['authority']

jout = subprocess.run(['journalctl','--system','--no-pager','-o','json','--since','2026-10-19 15:39:50','--until','2026-10-19 16:09:15','-u','marketdata-checkpoint-retirement.service','-u','marketdata-checkpoint-retirement.timer'],capture_output=True,check=True).stdout
(OUT/'evidence/system-journal.jsonl').write_bytes(jout)
system_rows = [json.loads(l) for l in jout.splitlines()]
assert any(x.get('INVOCATION_ID') == ctx['service_properties']['InvocationID'] and x.get('JOB_RESULT') == 'done' for x in system_rows)

result = {'observed_at':datetime.now(timezone.utc).isoformat(),'passed':True,'read_only_operating_audit':True,'run_id':RUN,'plan_id':PLAN,'invocation_id':ctx['service_properties']['InvocationID'],'timer_context':ctx,'daily_evidence':daily,'clock':{'observations':len(observations),'all_ready':True,'uncertainty_min':min(x['total_uncertainty'] for x in observations),'uncertainty_max':max(x['total_uncertainty'] for x in observations),'reference_age_min':min(x['reference_age_seconds'] for x in observations),'reference_age_max':max(x['reference_age_seconds'] for x in observations)},'archived_bundle_objects':len(bundle['object_ids']),'deleted':deleted,'retained':retained,'remaining_checkpoints':len(actual),'staging_empty':True,'unfinished':unfinished,'journal_count':len(all_journals),'commands':commands,'command_counts':dict(Counter(x['operation'] for x in commands)),'verified_nodes':verified_nodes,'preserved_record_count':len(op['preserved_records']),'code_files_verified':len(plan['code']['source_files']),'runs':all_runs,'duration_seconds':(datetime.fromisoformat(run['finished_at'])-datetime.fromisoformat(run['started_at'])).total_seconds(),'attempt':journal['attempts'][0],'sources':sources,'limits':['No repeated full checkpoint verification, restic check/dump, restore, or worker execution.','Retained full integrity and lock entry/exit are evidenced by the successful installed code path and final run/journal; no separate per-checkpoint integrity receipt is emitted.','Current host ownership/systemd/collector incident are recorded separately; namespace UIDs are not host ownership evidence.']}
(OUT/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['passed','run_id','plan_id','remaining_checkpoints','staging_empty','unfinished','clock','duration_seconds']},ensure_ascii=False))
