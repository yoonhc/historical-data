"""Read-only final audit; writes only this report directory, never operating state."""

import hashlib
import json
import os
import stat
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path('/tmp/clock-shutdown-correction-cpmkuynr')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(name):
    return json.loads((HERE / name).read_text())


def record(path):
    p = Path(path)
    if p.is_symlink():
        return {'link': os.readlink(p)}
    if not p.exists():
        return {'missing': True}
    s = p.stat()
    return {'sha256': sha(p), 'uid': s.st_uid, 'mode': stat.S_IMODE(s.st_mode)}


def show(unit, properties):
    r = subprocess.run(
        ['/usr/bin/systemctl', 'show', unit, '--all', '--no-pager',
         '--property=' + ','.join(properties)],
        capture_output=True, text=True, timeout=15, check=True,
        env={**os.environ, 'LC_ALL': 'C', 'SYSTEMD_COLORS': '0'},
    )
    return dict(line.split('=', 1) for line in r.stdout.splitlines() if '=' in line)


def audit_archive(archive, metadata, source_dir, files_key):
    assert sha(archive) == metadata['archive_sha256']
    expected = metadata[files_key]
    assert all(sha(source_dir / n) == h for n, h in expected.items())
    with tarfile.open(archive, 'r:gz') as tf:
        files = {m.name: hashlib.sha256(tf.extractfile(m).read()).hexdigest()
                 for m in tf if m.isfile()}
    assert all(files[n] == h for n, h in expected.items())
    return {'archive_sha256': sha(archive), 'source_files_checked': len(expected),
            'archive_and_source_match': True}


def main():
    # Outside-sandbox read access is necessary for actual UID 0 comparisons.
    baseline = read('system-preflight-result-08b7a916b7fc4a46.json')['after']
    result = {'observed_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'read-only operating state and fixed-candidate hash audit'}
    result['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    result['files'] = {p: record(p) for p in baseline['files']}
    result['file_differences'] = [p for p, v in result['files'].items()
                                  if v != baseline['files'][p]]
    result['chrony'] = show('chrony.service', ['MainPID', 'InvocationID', 'ActiveState'])
    result['chrony_unchanged'] = result['chrony'] == baseline['chrony']
    result['collectors'] = {
        stream: show('marketdata-collector@' + stream + '.service',
                     ['FragmentPath', 'DropInPaths', 'ExecStart', 'ExecStartPre',
                      'ActiveState', 'SubState', 'RuntimeMaxUSec', 'TimeoutStopUSec'])
        for stream in ('depth', 'aggtrade')
    }
    old = '/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee'
    result['old_collector_configuration'] = all(
        c['FragmentPath'] == '/etc/systemd/system/marketdata-collector@.service'
        and c['DropInPaths'] == '' and old in c['ExecStart'] and old in c['ExecStartPre']
        and c['TimeoutStopUSec'] == '2min 30s' and c['RuntimeMaxUSec'] == 'infinity'
        for c in result['collectors'].values()
    )
    root_runs = sorted(HERE.glob('system-preflight-result-*.json'))
    units = [u for p in root_runs for u in json.loads(p.read_text())['cleanup']['units']]
    result['test_units'] = {u: show(u, ['LoadState'])['LoadState'] for u in units}
    result['test_paths_absent'] = all(
        not (Path(base) / ('md-clock-probe-' + json.loads(p.read_text())['nonce'])).exists()
        for p in root_runs for base in ('/opt', '/var/lib')
    )
    plan = read('operating-plan-final.json')
    result['plan_id'] = plan['plan_id']
    ledger = json.loads(Path('/var/lib/marketdata-clock/boot-state.json').read_text())
    result['plan_matches_current_boot_and_ledger'] = (
        result['boot_id'] == plan['boot_id'] and ledger == plan['ledger_before']
    )
    result['configuration_matches_plan_before'] = all(
        record(v['path']) == {'sha256': v['before_sha256'], 'uid': v['uid'], 'mode': v['mode']}
        for v in plan['files'].values()
    )
    result['collector'] = audit_archive(
        HERE / 'collector-correction/source.tar.gz', read('collector-correction/release.json'),
        HERE / 'collector-correction/release', 'source_files_sha256',
    )
    result['transition'] = audit_archive(
        HERE / 'transition-source-final.tar.gz', read('release-final.json'),
        HERE / 'release-final', 'files',
    )
    parent = HERE.parent / 'clock-evidence-policy-candidate-2026-10-09'
    parent_meta = json.loads((parent / 'release-final.json').read_text())
    result['parent_collector'] = audit_archive(
        parent / 'source-final.tar.gz', parent_meta, parent / 'release-final',
        'source_files_sha256',
    )
    t10 = HERE.parent / 'chrony-source-candidate-2026-10-09'
    expected_t10 = read('provenance.json')['parent_transition_sha256']
    result['t10_tool_preserved'] = all(
        sha(t10 / n / 'clock_config_transition.py') == expected_t10
        for n in ('release-final', 'work')
    )
    manifest = json.loads((WORK / 'runtime-manifest.json').read_text())
    assert not manifest['editable']
    assert all(sha(WORK / n) == h for n, h in manifest['runtime_files_sha256'].items())
    result['installed_runtime'] = {'release_id': manifest['release_id'],
                                   'files_checked': len(manifest['runtime_files_sha256']),
                                   'editable': manifest['editable'], 'python': manifest['python']}
    (HERE / 'collector-correction/verified-runtime-manifest.json').write_bytes(
        (WORK / 'runtime-manifest.json').read_bytes()
    )
    result['passed'] = all([
        not result['file_differences'], result['chrony_unchanged'],
        result['old_collector_configuration'], result['test_paths_absent'],
        all(v == 'not-found' for v in result['test_units'].values()),
        result['plan_matches_current_boot_and_ledger'],
        result['configuration_matches_plan_before'], result['t10_tool_preserved'],
    ])
    (HERE / 'final-audit.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ('files', 'collectors', 'test_units')}, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
