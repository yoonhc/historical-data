"""Freeze the isolated candidate, without touching installed code or operating configuration."""
import difflib
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'work'
PARENT = Path('/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee')

def sha(blob):
    return hashlib.sha256(blob).hexdigest()

def main():
    parent = json.loads((ROOT / 'parent.json').read_text())
    names = set(parent['source_files_sha256']) | {
        'packages/marketdata/src/marketdata/clock_evidence.py',
        'integrations/runtime/capture_runtime.py',
        'tests/test_clock_evidence_policy.py',
    }
    files = {name:sha((WORK/name).read_bytes()) for name in sorted(names)}
    identity = sha(json.dumps(files,sort_keys=True,separators=(',',':')).encode())
    metadata = {'schema':1,'release_id':'clock-evidence-policy-20261009-'+identity[:16],
                'parent_release_id':parent['release_id'],'base_git_commit':parent['base_git_commit'],
                'build':parent['build'],'source_files_sha256':files,'source_identity':identity,
                'operational_applied':False,'policy_version':'reference-freshness-v2'}
    metadata_bytes = json.dumps(metadata,indent=2,sort_keys=True).encode()+b'\n'
    (WORK/'candidate.json').write_bytes(metadata_bytes)
    release=ROOT/'release-final'
    release.mkdir(exist_ok=False)
    patch=[]
    for name in files:
        target=release/name
        target.parent.mkdir(parents=True,exist_ok=True)
        raw=(WORK/name).read_bytes(); target.write_bytes(raw)
        before=(PARENT/name).read_bytes() if (PARENT/name).exists() else b''
        if before!=raw:
            patch.extend(difflib.unified_diff(before.decode().splitlines(True),raw.decode().splitlines(True),
                                             fromfile='a/'+name,tofile='b/'+name))
    (release/'candidate.json').write_bytes(metadata_bytes)
    (ROOT/'candidate-final.patch').write_text(''.join(patch))
    buffer=io.BytesIO()
    with gzip.GzipFile(fileobj=buffer,mode='wb',mtime=0) as compressed:
        with tarfile.open(fileobj=compressed,mode='w') as archive:
            for name in sorted([*files,'candidate.json']):
                raw=(release/name).read_bytes()
                entry=tarfile.TarInfo(name); entry.size=len(raw); entry.mode=0o644; entry.mtime=0
                archive.addfile(entry,io.BytesIO(raw))
    blob=buffer.getvalue()
    (ROOT/'source-final.tar.gz').write_bytes(blob)
    (ROOT/'release-final.json').write_text(json.dumps({**metadata,'archive_sha256':sha(blob),
        'archive_bytes':len(blob),'changed_files':[n for n,v in files.items() if parent['source_files_sha256'].get(n)!=v]},indent=2)+'\n')
    for path in release.rglob('*'):
        path.chmod(0o555 if path.is_dir() else 0o444)
    release.chmod(0o555)
    print(json.dumps({'release_id':metadata['release_id'],'archive_sha256':sha(blob),'files':len(files)}))

if __name__ == '__main__':
    main()
