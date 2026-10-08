"""Check the final collector's permissions without starting a collector."""
import json
import os
import sys
from pathlib import Path

ROOT = Path('/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee')
sys.path.insert(0, str(ROOT / 'integrations/runtime'))
import clock_gate as gate

p, runtime = gate.read_profile('/etc/marketdata/clock-bootstrap.json')
assert os.geteuid() == 1000
release_id = gate.rt.environment_check(runtime)
with gate.Ledger(p['state_dir']).lock(shared=True):
    state = gate.Ledger(p['state_dir']).read()
assert state['phase'] == 'installation_hold' and state['step_attempts'] == 0
try:
    gate.check_gate(p, runtime)
except gate.Hold as exc:
    assert str(exc) == 'This boot has not closed initial clock preparation'
else:
    raise AssertionError('Installation hold unexpectedly passed')
print(json.dumps({'passed': True, 'uid': os.geteuid(), 'release_id': release_id,
                  'shared_lock_readable': True, 'installation_gate_refused': True,
                  'environment_checked': True, 'clock_observation': gate.rt.probe(runtime)}))
