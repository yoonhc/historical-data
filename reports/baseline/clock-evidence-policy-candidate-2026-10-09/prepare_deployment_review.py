"""Read operating inputs; write an INERT review bundle below this report only.

No service action, privileged write, approval execution or ledger mutation.
Actual root ownership was separately checked outside the mapped sandbox.
"""
import copy, difflib, hashlib, importlib.util, json, stat
from pathlib import Path

base=Path(__file__).resolve().parent
meta=json.loads((base/'release-final.json').read_text())
work=base/'release-final'
spec=importlib.util.spec_from_file_location('render',work/'integrations/runtime/render_units.py')
render=importlib.util.module_from_spec(spec); spec.loader.exec_module(render)
def digest(b): return hashlib.sha256(b).hexdigest()
def encode(v): return (json.dumps(v,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
def canonical(v): return digest(json.dumps(v,sort_keys=True).encode())
paths={'runtime':Path('/etc/marketdata/runtime.json'),'bootstrap':Path('/etc/marketdata/clock-bootstrap.json'),'collector_unit':Path('/etc/systemd/system/marketdata-collector@.service')}
before={k:p.read_bytes() for k,p in paths.items()}
r=json.loads(before['runtime']); b=json.loads(before['bootstrap'])
new_r={**r,'release_root':'/opt/marketdata/releases/'+meta['release_id']}
new_unit=render.render(new_r,str(paths['runtime']),str(paths['bootstrap'])).encode()
new_b=copy.deepcopy(b)
new_b['runtime_profile_sha256']=digest(encode(new_r))
new_b['guard_files'][str(paths['collector_unit'])]=digest(new_unit)
after={'runtime':encode(new_r),'bootstrap':encode(new_b),'collector_unit':new_unit}
ledger_path=Path(b['state_dir'])/'boot-state.json'; old_ledger=json.loads(ledger_path.read_bytes())
assert old_ledger['phase']=='closed' and old_ledger['schema']==1
assert old_ledger['profile_sha256']==canonical(b)
assert old_ledger['closed_observation']['ready']
assert old_ledger['boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip()
assert {k:v for k,v in new_r.items() if k!='release_root'}=={k:v for k,v in r.items() if k!='release_root'}
result=base/'deployment'; result.mkdir(exist_ok=True)
files={}; differences=[]
for role,path in paths.items():
    info=path.stat(); assert stat.S_IMODE(info.st_mode)==0o644
    # UID 0 was separately verified with host stat outside the user namespace.
    # The sandbox maps host root to nobody (65534); this is an inert review plan.
    assert info.st_uid in (0,65534)
    for side,values in [('before',before),('after',after)]:
        (result/(role+'.'+side)).write_bytes(values[role])
    files[role]={'path':str(path),'uid':0,'uid_evidence':'host stat outside sandbox, 2026-10-09; sandbox root maps to 65534','mode':stat.S_IMODE(info.st_mode),'before_sha256':digest(before[role]),'after_sha256':digest(after[role]),'before_file':role+'.before','after_file':role+'.after'}
    differences.extend(difflib.unified_diff(before[role].decode().splitlines(True),after[role].decode().splitlines(True),fromfile=str(path)+':before',tofile=str(path)+':after'))
plan={'schema':1,'kind':'collector_clock_evidence_runtime_review','executable':False,'release_id':meta['release_id'],'archive_sha256':meta['archive_sha256'],'files':files,'boot_id':old_ledger['boot_id'],'ledger_path':str(ledger_path),'ledger_before':old_ledger,'ledger_before_sha256':digest(ledger_path.read_bytes()),'before_bootstrap_canonical_sha256':canonical(b),'after_bootstrap_canonical_sha256':canonical(new_b),'chrony_config_sha256':r['chrony_config_sha256'],'approval':None,'work_allowed':False,'automatic_checkpoint_retirement':'remain_masked','promotion_boundary':'Exact apply/rollback executor and crash recovery must be prepared and verified against this plan before requesting operating approval. T10 source transition tool is not authorized for this scope.','required_preserved_ledger_fields':[k for k in old_ledger if k!='profile_sha256'],'required_sequence':['Install sealed release and verify non-editable import/source hashes; do not overwrite old release.','Drain and stop the two system collectors under an approved short collection gap. Confirm relevant scheduled jobs quiescent; prevent starts with the coordinator lock and pending schema.','Persist exact before files, approval, plan, and original closed ledger. Under coordinator exclusive lock write an explicitly scoped pending envelope (schema 2) before any profile/unit writes.','Apply and fsync the exact three files, daemon-reload, verify effective system unit and import identity under final permissions. No Chrony restart/step/RTC/reboot.','Commit a linked runtime promotion receipt and new bootstrap profile binding, preserving original closed evidence and appending history; query current strict clock separately.','Start the two collectors and verify singleton processes, actual storage, closed clock chunk and linked collection. Other jobs stay strict and retirement stays masked.'],'rollback_sequence':['Stop only candidate collectors; do not restart Chrony or reenroll clock state.','Use the same coordinator/pending mechanism and recorded actual completed stage. Verify current files belong to the approved before/after hashes.','Restore exact three before files, daemon-reload; append rollback receipt and restore old profile binding without deleting original or later history.','Recheck current strict readiness, restart old collectors; retain v2 catalog artifacts, spool and collection links. Use retained candidate query CLI to read new evidence.','If boot or ledger baseline changed, keep jobs blocked; do not overwrite newer state or automatically reopen initial correction.']}
plan['plan_id']=canonical(plan)
(result/'plan.json').write_bytes(encode(plan)); (result/'configuration.diff').write_text(''.join(differences))
(result/'approval.template.json').write_bytes(encode({'approved':False,'plan_id':plan['plan_id'],'release_id':meta['release_id'],'scope':'review only; no operational write authorized'}))
# Current-path substitution is ONLY for executable-exists syntax validation.
clean=json.loads((base/'clean-final-path.json').read_text())['path']
syntax_root=Path('/tmp/clock-evidence-unit-verify'); syntax_root.mkdir(exist_ok=True)
syn=render.render({**new_r,'release_root':clean},str(paths['runtime']),str(paths['bootstrap']))
(syntax_root/'marketdata-collector@.service').write_text(syn)
(result/'syntax-substitution.json').write_bytes(encode({'final_release_root':new_r['release_root'],'syntax_release_root':clean,'unit':str(syntax_root/'marketdata-collector@.service'),'limitation':'Only release path differs; no unit installed or started; final root ownership/protections require approved deployment.'}))
print(json.dumps({'release_id':meta['release_id'],'plan_id':plan['plan_id'],'files':list(files),'operational_applied':False}))
