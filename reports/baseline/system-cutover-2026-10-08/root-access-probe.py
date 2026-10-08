"""Read-only production-path probe under the bootstrap service's protections."""
import json
import sys
from pathlib import Path

ROOT = Path('/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee')
sys.path.insert(0, str(ROOT / 'integrations/runtime'))
import clock_gate as gate

p, runtime = gate.read_profile('/etc/marketdata/clock-bootstrap.json')
host = gate.Host(p, runtime)
host.rtc_check()
host.audit_quiescent()
ledger = gate.Ledger(p['state_dir'])
with ledger.lock(shared=True):
    state = ledger.read()
assert state['phase'] == 'installation_hold' and state['step_attempts'] == 0
before = sorted(Path('/run/chrony').glob('chronyc.*.sock'))
tracking = host.runner(['/usr/bin/chronyc', '-n', '-h', p['chrony_socket'],
                        '-m', 'retries 0', 'tracking'])
after = sorted(Path('/run/chrony').glob('chronyc.*.sock'))
assert before == after, 'Leaked Chrony client reply socket'
provider = gate.rt.provider_identity()
assert '-f' in provider['argv'] and provider['argv'][provider['argv'].index('-f')+1] == runtime['chrony_config']
print(json.dumps({'passed': True, 'uid': 0, 'rtc_checked': True, 'quiescent_inventory_checked': True,
                  'ledger': state, 'socket_tracking': tracking, 'client_reply_socket_cleanup': True,
                  'provider': provider, 'user_manager_jobs': host.user_command('list-jobs', '--no-pager')}))
