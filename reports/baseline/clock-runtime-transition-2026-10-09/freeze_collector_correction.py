"""Preserve the approved parent; freeze only the observed shutdown-order correction."""
import difflib
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
WORK=Path('/tmp/clock-shutdown-correction-cpmkuynr')
PARENT=HERE.parent/'clock-evidence-policy-candidate-2026-10-09/release-final'
def sha(b):return hashlib.sha256(b).hexdigest()
parent=json.loads((PARENT/'candidate.json').read_text())
files={n:sha((WORK/n).read_bytes()) for n in parent['source_files_sha256']}
changed=[n for n,v in files.items() if v!=parent['source_files_sha256'][n]]
assert set(changed)=={'integrations/runtime/capture_runtime.py','tests/test_clock_evidence_policy.py'}
identity=sha(json.dumps(files,sort_keys=True,separators=(',',':')).encode())
meta={**parent,'release_id':'clock-evidence-policy-20261009-'+identity[:16],
      'parent_release_id':parent['release_id'],'source_files_sha256':files,'source_identity':identity,
      'correction':'sample final observation after the provider query; no policy change'}
rawmeta=json.dumps(meta,indent=2,sort_keys=True).encode()+b'\n'
(WORK/'candidate.json').write_bytes(rawmeta)
out=HERE/'collector-correction'
release=out/'release'
release.mkdir(parents=True,exist_ok=False)
patch=[]
for n in files:
 p=release/n;p.parent.mkdir(exist_ok=True,parents=True);p.write_bytes((WORK/n).read_bytes())
 if n in changed:
  patch.extend(difflib.unified_diff((PARENT/n).read_text().splitlines(True),p.read_text().splitlines(True),fromfile='a/'+n,tofile='b/'+n))
(release/'candidate.json').write_bytes(rawmeta)
(out/'correction.patch').write_text(''.join(patch))
buf=io.BytesIO()
with gzip.GzipFile(fileobj=buf,mode='wb',mtime=0) as gz:
 with tarfile.open(fileobj=gz,mode='w') as tar:
  for n in sorted([*files,'candidate.json']):
   blob=(release/n).read_bytes();info=tarfile.TarInfo(n);info.size=len(blob);info.mode=0o644;info.mtime=0
   tar.addfile(info,io.BytesIO(blob))
(out/'source.tar.gz').write_bytes(buf.getvalue())
(out/'release.json').write_text(json.dumps({**meta,'archive_sha256':sha(buf.getvalue()),'changed_files':changed},indent=2)+'\n')
print(json.dumps({'release_id':meta['release_id'],'sha256':sha(buf.getvalue()),'changed_files':changed}))
