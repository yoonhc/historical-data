"""Reuse the existing read-only deployed runtime checks; never control services."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OBSERVER = HERE.parent / 't11-first-child-boundary-2026-10-09/observe.py'

parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['before', 'after'])
stage = parser.parse_args().stage
spec = importlib.util.spec_from_file_location('existing_boundary_checks', OBSERVER)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)
observer.OUT = HERE
captured = {}
observer.save = lambda path, value: captured.update(value)
observer.host()
captured['observer_sha256'] = hashlib.sha256(OBSERVER.read_bytes()).hexdigest()
captured['user_systemd'] = observer.run([
    '/usr/bin/systemctl', '--user', 'show', 'marketdata-backup.service',
    'marketdata-restic-archive.service', 'marketdata-checkpoint-retirement.service',
    'marketdata-checkpoint-retirement.timer', '--all',
    '--property=Id,LoadState,UnitFileState,ActiveState,SubState,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp',
])
assert captured['passed'] and captured['user_systemd']['code'] == 0
if stage == 'after':
    previous = json.loads((HERE / 'host-before.json').read_text())
    captured['unchanged_from_before'] = {
        key: captured[key] == previous[key] for key in ('boot_id', 'files', 'ledger', 'masks')
    }
    assert all(captured['unchanged_from_before'].values())
with (HERE / ('host-' + stage + '.json')).open('x') as output:
    json.dump(captured, output, indent=2, ensure_ascii=False)
    output.write('\n')
print(json.dumps({'stage': stage, 'passed': captured['passed'],
                  'unchanged': captured.get('unchanged_from_before')}))
