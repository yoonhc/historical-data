import hashlib,json,stat,tarfile
from pathlib import Path
base=Path('/tmp/clock-shutdown-correction-cpmkuynr')
out=Path('/home/yoonhc/projects/algoTrade/historical_data/reports/baseline/clock-runtime-transition-2026-10-09')
archive=Path('/tmp/clock-system-probe-environment-corrected.tar.gz')
pins=json.loads((out/'work/release-pins.json').read_text())['after']
files=set(pins['source_files_sha256'])|{'candidate.json','runtime-manifest.json'}
for p in (base/'.venv').rglob('*'):
 if '__pycache__' not in p.parts and (p.is_file() or p.is_symlink()): files.add(str(p.relative_to(base)))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
for name,h in pins['source_files_sha256'].items(): assert sha(base/name)==h,name
records={}
with tarfile.open(archive,'w:gz') as tar:
 for name in sorted(files):
  p=base/name
  if p.is_symlink(): records[name]={'link':p.readlink().as_posix()}
  else: records[name]={'sha256':sha(p),'bytes':p.stat().st_size,'mode':stat.S_IMODE(p.stat().st_mode)}
  tar.add(p,arcname=name,recursive=False)
inv=out/'system-preflight-inventory.json'
inv.write_text(json.dumps(records,indent=2)+'\n')
root=Path('/home/yoonhc/projects/algoTrade/historical_data')
previous=json.loads((root/'reports/baseline/clock-evidence-policy-candidate-2026-10-09/operating-after.json').read_text())
print('previous keys',previous.keys())
operating=['/etc/marketdata/runtime.json','/etc/marketdata/clock-bootstrap.json','/var/lib/marketdata-clock/boot-state.json','/etc/chrony/marketdata.conf','/etc/adjtime','/etc/systemd/system/marketdata-collector@.service']
operating += list(json.loads(Path('/etc/marketdata/clock-bootstrap.json').read_text())['guard_files'])
operating += [str(p) for p in Path('/home/yoonhc/.config/systemd/user').glob('marketdata-*') if p.is_file() or p.is_symlink()]
properties={'Type':'exec','StartLimitIntervalSec':'0','Restart':'always','RestartSec':'15s','RuntimeMaxSec':'infinity',
 'TimeoutStartSec':'150s','TimeoutStopSec':'330s','KillMode':'mixed','KillSignal':'SIGINT','SendSIGKILL':'yes',
 'UMask':'0077','NoNewPrivileges':'yes','CapabilityBoundingSet':'','ProtectClock':'yes','ProtectSystem':'strict',
 'ProtectHome':'read-only','PrivateTmp':'yes','RestrictAddressFamilies':'AF_INET AF_INET6 AF_UNIX AF_NETLINK'}
inputs={'release_id':pins['release_id'],'archive':str(archive),'inventory':str(inv),'files':{str(p):sha(p) for p in [archive,inv,out/'system_probe_worker.py',out/'system_preflight.py']},'properties':properties,'operating_files':sorted(set(operating))}
(out/'system-preflight-inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'files':len(files)}))
