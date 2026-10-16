"""Seal evidence for the isolated candidate; perform no operating mutations."""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime,timezone
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
m=json.loads((HERE/'manifest.json').read_bytes());parents=json.loads((HERE/'parents.json').read_bytes())
changed=[];unchanged=[];added=[]
for name,sha in m['runtime_files'].items():
    path=HERE/'candidate'/name;assert h(path)==sha
    old=parents['files'].get(name)
    if old and old['sha256']==sha:unchanged.append(name)
    elif old:changed.append({'path':name,'before_sha256':old['sha256'],'after_sha256':sha})
    else:added.append(name)
for path,sha in m['before'].items():assert h(Path(path))==sha,path
masks={}
for directory in ('/etc/systemd/system','/home/yoonhc/.config/systemd/user'):
    for suffix in ('service','timer'):
        p=Path(directory)/('marketdata-checkpoint-retirement.'+suffix)
        masks[str(p)]=os.readlink(p);assert masks[str(p)]=='/dev/null'
modules={}
for variant in ('base','daily','preserve'):
    root=HERE/'candidate'/variant
    src=root/'packages/marketdata/src/marketdata';installed=root/'.venv/lib/python3.12/site-packages/marketdata'
    assert all(h(p)==h(installed/p.name) for p in src.glob('*.py')),variant
    modules[variant]=len(list(src.glob('*.py')))
# No T05 clock/retention rules, collector/bootstrap control or archival content implementation changed.
for name in ('checkpoint_clock.py','checkpoint_evidence.py','backup.py'):
    rel='preserve/packages/marketdata/src/marketdata/'+name
    assert h(HERE/'candidate'/rel)==parents['files'][rel]['sha256']
for name in ('archive_backups.py','clock_retirement.py','retire_checkpoints.py','checkpoint_schedule.py'):
    rel='preserve/scripts/'+name
    assert h(HERE/'candidate'/rel)==parents['files'][rel]['sha256']
state=json.loads(Path('/var/lib/marketdata-clock/boot-state.json').read_bytes())
source={'release_id':m['release_id'],'source_sha256':m['source_sha256'],'changed':changed,'added':added,'unchanged':unchanged,
    'candidate_environment_source_copies_equal':modules,'operating_configuration_inputs_unchanged':True,
    'automatic_retirement_masks':masks,'ledger':{k:state[k] for k in ('boot_id','phase','step_attempts')},
    'operating_service_commands_issued':0,'operating_clock_commands_issued':0,'operating_data_mutations':0}
(HERE/'source-review.json').write_text(json.dumps(source,indent=2)+'\n')
groups=[('base','base-regression.xml'),('base','pipeline-regression.xml'),('daily','daily-regression.xml'),
        ('preserve','preserve-validation.xml'),('new-base','base-policy.xml'),('new-daily','daily-policy.xml'),('new-preserve','preserve-policy.xml')]
seen={};runs=[]
for variant,name in groups:
    root=ET.parse(HERE/name).getroot();cases=root.findall('.//testcase')
    assert cases and not root.findall('.//failure') and not root.findall('.//error') and not root.findall('.//skipped'),name
    ids={(c.attrib['classname'],c.attrib['name']) for c in cases}
    unique=ids-seen.setdefault(variant,set());seen[variant]|=ids
    runs.append({'variant':variant,'report':name,'sha256':h(HERE/name),'passed':len(cases),'new_in_variant':len(unique)})
unit=json.loads((HERE/'unit-verification.json').read_bytes());assert unit['returncode']==0
record={'release_id':m['release_id'],'source_sha256':m['source_sha256'],'reviewed_at':datetime.now(timezone.utc).isoformat(),
    'passed':True,'new_policy_tests':sum(len(v) for k,v in seen.items() if k.startswith('new-')),
    'regression_tests_per_variant':{k:len(v) for k,v in seen.items() if not k.startswith('new-')},'runs':runs,
    'unit_syntax_and_order':unit,'actual_system_manager_execution':False,'operating_deployed':False,
    'parents_preserved':True,'T05_deletion_authority':False}
(HERE/'validation.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({k:record[k] for k in ('release_id','passed','new_policy_tests','regression_tests_per_variant','operating_deployed')}))
