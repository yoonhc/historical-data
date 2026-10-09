"""Refresh the existing exact four-tree review. No deletion or service changes."""

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
BASE = Path(__file__).resolve().parent
PREVIOUS = BASE.parent / 'trial-artifact-retention-expanded-2026-10-05'
ARTIFACTS = ROOT / 'reports/trial-artifact-retention-refresh-2026-10-09'


def load_review():
    spec = importlib.util.spec_from_file_location('prior_trial_review', PREVIOUS / 'review.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BASE = BASE
    module.ARTIFACTS = ARTIFACTS
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, value):
    with (BASE / name).open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.write('\n')


def extra_host_review():
    candidates = json.loads((PREVIOUS / 'plan.json').read_text())['candidate_roots']
    needles = [x['path'] for x in candidates] + ['/home/yoonhc/.config/marketdata/backup-host.toml']
    files = []
    for directory in (Path('/etc/marketdata'), Path('/etc/systemd/system')):
        if directory.exists():
            files.extend(p for p in directory.rglob('*') if p.is_file() and not p.is_symlink()
                         and (directory.name == 'marketdata' or 'marketdata' in str(p)))
    hashes, references = {}, []
    for path in sorted(set(files)):
        if path.stat().st_size > 2_000_000:
            continue
        raw = path.read_bytes()
        hashes[str(path)] = hashlib.sha256(raw).hexdigest()
        for i, line in enumerate(raw.decode().splitlines(), 1):
            if any(value in line for value in needles):
                references.append({'path': str(path), 'line': i, 'matched_paths': [n for n in needles if n in line]})
    cmd = ['systemctl', 'show', 'marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service',
           'chrony.service', '-p', 'Id', '-p', 'MainPID', '-p', 'ActiveState', '-p', 'InvocationID']
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
    masks = {name: str((Path.home()/'.config/systemd/user'/name).readlink()) for name in (
        'marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer')}
    write('host-addendum.json', {'at': datetime.now(timezone.utc).isoformat(),
                                'configuration_hashes': hashes, 'candidate_references': references,
                                'systemd_command': cmd, 'systemd': result.stdout, 'automatic_retirement_links': masks})
    assert not references, references
    assert set(masks.values()) == {'/dev/null'}


def verify_retained():
    from marketdata.backup import verify_backup
    from marketdata.domain import json_bytes

    original = json.loads((PREVIOUS / 'plan.json').read_text())['replacement_sources'][0]
    path = Path(original['retained_source']['path'])
    started = datetime.now(timezone.utc).isoformat()
    clock = time.monotonic()
    result = verify_backup(path)
    value = {'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
             'seconds': time.monotonic()-clock, 'path': str(path), 'result': result,
             'scope': 'Fresh physical file hashes/catalog/pack verification of the retained 09-26 checkpoint only.'}
    assert result['ok'] and result['backup_id'] == original['backup_id']
    with (BASE/'retained-checkpoint-verification.json').open('xb') as f:
        f.write(json_bytes(value)+b'\n')
    print(json.dumps({'ok': result['ok'], 'files': result['files'], 'seconds': value['seconds']}), flush=True)


def main():
    assert len(sys.argv) == 2 and sys.argv[1] in ('probe', 'inventory', 'retained')
    stage = sys.argv[1]
    try:
        if stage == 'probe':
            load_review().probe()
            extra_host_review()
        elif stage == 'inventory':
            load_review().inventory()
        else:
            verify_retained()
        write(stage+'-execution.json', {'passed': True, 'stage': stage, 'at': datetime.now(timezone.utc).isoformat(),
                                      'wrapper_sha256': sha(Path(__file__)),
                                      'reused_review_sha256': sha(PREVIOUS/'review.py'), 'deleted_files': 0})
    except Exception as exc:
        write(stage+'-failure.json', {'passed': False, 'stage': stage, 'error': repr(exc),
                                    'at': datetime.now(timezone.utc).isoformat(), 'deleted_files': 0})
        raise


if __name__ == '__main__':
    main()
