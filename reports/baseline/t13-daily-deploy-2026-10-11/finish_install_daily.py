"""Seal only the newly installed T13 release and finish external configs.

Preserves the first failed result. No reinstall, service control or clock change.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPS = Path('/var/lib/marketdata-t13-deploy-20261011')


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    inputs = json.loads((OPS/'install-inputs.json').read_text())
    for path, digest in inputs['files'].items():
        if sha(path) != digest:
            raise RuntimeError('Reviewed original input changed: '+path)
    if sha(OPS/'installer.py') != inputs['files'][str(HERE/'install_daily.py')]:
        raise RuntimeError('Saved installer differs')
    if (OPS/'finish-result.json').exists():
        raise RuntimeError('Continuation already recorded; review before retry')
    original = json.loads((OPS/'result.json').read_text())
    if original['passed'] or original['phase'] != 'installed_environment' or 'Release ownership/permissions differ' not in original['error']:
        raise RuntimeError('Different installation failure')
    spec = importlib.util.spec_from_file_location('saved_installer', OPS/'installer.py')
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    before = json.loads((OPS/'operating-before.json').read_text())
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != before['boot_id']:
        raise RuntimeError('Boot changed')
    for path, digest in before['protected_hashes'].items():
        if sha(path) != digest:
            raise RuntimeError('Protected file changed: '+path)
    for path, value in before['daily_files'].items():
        if sha(path) != value['sha256'] or os.readlink(path) != value['symlink']:
            raise RuntimeError('Operating daily changed')
    manifest = json.loads((OPS/'manifest.json').read_text())
    release = Path('/opt/marketdata/releases')/manifest['release_id']
    for path, digest in manifest['source_sha256'].items():
        if sha(release/path) != digest:
            raise RuntimeError('Installed source differs: '+path)
    for name in old.CONFIGS:
        if (Path('/etc/marketdata')/name).exists():
            raise RuntimeError('Configuration already exists')
    if not args.run:
        print(json.dumps({'continuation_valid':True,'services_changed':False}))
        return
    if os.geteuid() != 0:
        raise SystemExit('Administrator authentication required')
    os.umask(0o022)
    result = {'release_id':manifest['release_id'],'passed':False,'services_changed':False,
              'clock_control':False,'original_result_preserved':True,
              'started_at':datetime.now(timezone.utc).isoformat(),'phase':'seal_new_release'}
    try:
        old.write_new(OPS/'finish-installer.py',Path(__file__).read_bytes())
        expected = json.loads((OPS/'expected-environment.json').read_text())
        actual = json.loads(old.run([release/'.venv/bin/python','-B','-I',OPS/'inspect_environment.py']))
        if actual != expected:
            raise RuntimeError('Installed environment differs before sealing')
        changes = []
        for p in [release,*release.rglob('*')]:
            if p.is_symlink():
                if not p.resolve().is_relative_to(release) and p.resolve() != Path('/usr/bin/python3').resolve():
                    raise RuntimeError('Unexpected external symlink: '+str(p))
                continue
            st = p.stat()
            if st.st_uid != 0:
                raise RuntimeError('Non-root installed file: '+str(p))
            if st.st_mode & 0o022:
                mode = (st.st_mode & 0o777) & ~0o022
                changes.append({'path':str(p.relative_to(release)),'before':oct(st.st_mode&0o777),'after':oct(mode)})
                p.chmod(mode)
            if p.stat().st_mode & 0o022:
                raise RuntimeError('Release remains writable: '+str(p))
        old.write_new(OPS/'permission-seal.json',(json.dumps(changes,indent=2)+'\n').encode())
        actual = json.loads(old.run(['/usr/sbin/runuser','-u','yoonhc','--',release/'.venv/bin/python','-B','-I',OPS/'inspect_environment.py']))
        if actual != expected:
            raise RuntimeError('User installation differs after sealing')
        result['phase'] = 'external_configuration'
        for name,source in old.CONFIGS.items():
            old.write_new(Path('/etc/marketdata')/name,(release/source).read_bytes())
        code = "import os,tomllib,json; from pathlib import Path; a=tomllib.loads(Path('/etc/marketdata/daily-scheduler.toml').read_text()); b=tomllib.loads(Path('/etc/marketdata/daily-publication-probe.toml').read_text()); assert b['observation']['enabled'] is False; p=Path('/home/yoonhc/projects/algoTrade/historical_data'); assert os.access(p/'state',os.R_OK|os.W_OK|os.X_OK); assert os.access(p/'data',os.R_OK|os.W_OK|os.X_OK); print(json.dumps({'uid':os.getuid(),'observer_enabled':False,'data_access':True}))"
        old.run(['/usr/sbin/runuser','-u','yoonhc','--',release/'.venv/bin/python','-B','-I','-c',code])
        for path,digest in before['protected_hashes'].items():
            if sha(path) != digest:
                raise RuntimeError('Protected file changed during continuation: '+path)
        result.update(passed=True,phase='installed_pending_daily_activation',sealed_files=len(changes),
                      continuation_sha256=sha(__file__))
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    old.write_new(OPS/'finish-result.json',(json.dumps(result,indent=2)+'\n').encode())
    print(json.dumps(result,indent=2),flush=True)
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
