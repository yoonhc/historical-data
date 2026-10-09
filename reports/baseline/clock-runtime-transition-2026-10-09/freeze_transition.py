"""Freeze the collector-specific transaction; operating paths are never written."""
import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
files=[Path('work')/p.name for p in (HERE/'work').iterdir() if p.is_file()]+[Path('test_transition.py')]
files += [p.relative_to(HERE) for p in (HERE/'fixtures').rglob('*') if p.is_file()]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
hashes={str(p):sha(HERE/p) for p in sorted(files)}
identity=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
pins=json.loads((HERE/'work/release-pins.json').read_text())
release=HERE/'release-final'
release.mkdir(exist_ok=False)
for rel in files:
 (release/rel).parent.mkdir(exist_ok=True,parents=True)
 shutil.copyfile(HERE/rel,release/rel)
meta={'schema':1,'release_id':'collector-transition-20261009-'+identity[:16],
      'source_files_sha256':hashes,'collector_release':pins['after']['release_id'],
      'operational_applied':False,'scope':'runtime/profile/unit transition only; no service or clock control','tests':58}
(release/'candidate.json').write_text(json.dumps(meta,sort_keys=True,indent=2)+'\n')
archive=HERE/'transition-source-final.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
 for rel in [*files,Path('candidate.json')]:tar.add(release/rel,arcname=str(rel),recursive=False)
clean=Path(tempfile.mkdtemp(prefix='clock-transition-final-'))
with tarfile.open(archive) as tar:tar.extractall(clean,filter='data')
for rel,h in hashes.items():assert sha(clean/rel)==h
summary={'release_id':meta['release_id'],'collector_release':pins['after']['release_id'],
         'archive_sha256':sha(archive),'clean_path':str(clean),'files':hashes}
(HERE/'release-final.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
