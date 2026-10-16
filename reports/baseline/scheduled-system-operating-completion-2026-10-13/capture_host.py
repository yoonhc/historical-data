"""Read-only final manager/configuration and bounded execution-journal capture."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
HANDOFF = HERE.parent / 'scheduled-system-handoff-2026-10-12'
sys.path.insert(0, str(HANDOFF))
import handoff_common as c
from handoff import Host, STATE, reader
from handoff_inputs import load_checked

plan = load_checked(HANDOFF / 'host-plan-preflight-passed.json')
managers = c.Managers()
fs = c.SafeFiles('/', (), {})
host = Host(plan, fs, managers)
host.immutable()
report = {'observed_at': datetime.now(timezone.utc).isoformat(), 'plan_id': plan['plan_id'],
          'boot_id': c.boot(), 'operating_mutations': False, 'checks': {'protected_configuration': True}}
for manager in ('user', 'system'):
    report[manager] = managers.states(manager, (*c.SERVICES, *c.TIMERS, c.WAITER))
report['checks']['user_masked_idle'] = all(report['user'][n]['properties']['LoadState'] == 'masked' and
    report['user'][n]['classification'] == 'idle' for n in (*c.SERVICES, *c.TIMERS))
report['checks']['system_timers_enabled_active'] = all(report['system'][n]['properties']['ActiveState'] == 'active' and
    report['system'][n]['properties']['UnitFileState'] == 'enabled' for n in c.TIMERS)
report['checks']['uid_and_clock_protection'] = all(report['system'][n]['properties']['User'] == 'yoonhc' and
    report['system'][n]['properties']['ProtectClock'] == 'yes' for n in c.SERVICES)
for name, spec in plan['inner']['topology']['units'].items():
    reader.verify(report['system'][name]['properties'], spec, quiescent=False)
report['checks']['effective_units_match'] = True
report['checks']['configuration_files_match'] = all(fs.node(p) == op['after'] for p, op in plan['inner']['operations'].items())
receipt = fs.json('/var/lib/marketdata-clock/scheduled-transitions/' + plan['inner']['plan_id'] + '/apply-receipt.json')
ledger = fs.json('/var/lib/marketdata-clock/boot-state.json')
report['checks']['ledger_matches_committed'] = ledger == receipt['ledger_after']
report['checks']['closed_boot_correction_preserved'] = all(ledger[k] == v for k,v in plan['inner']['ledger_before'].items()
    if k not in ('profile_sha256', 'configuration_history'))
before = json.loads((HERE.parent / 'scheduled-system-deploy-2026-10-13/root-evidence/attempt-0d473fad0e2245b59b7cf8b1b3956359.json').read_bytes())
report['protected_processes'] = {}
for name in c.IDENTITIES:
    p = managers.show('system', name)
    report['protected_processes'][name] = {k:p[k] for k in ('MainPID','InvocationID','ActiveState')}
report['checks']['chrony_supervisors_preserved'] = report['protected_processes'] == before['operating_before']
report['service_results'] = {}
for name in c.SERVICES:
    result = managers.command('system', 'show', name, '--no-pager',
        '--property=Result,ExecMainCode,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp,SuccessExitStatus,InvocationID,ControlGroup')
    report['service_results'][name] = c.parse(result.stdout)
report['retirement_masks'] = {str(base/n):os.readlink(base/n) for base in (Path('/etc/systemd/system'),Path('/home/yoonhc/.config/systemd/user'))
    for n in ('marketdata-checkpoint-retirement.service','marketdata-checkpoint-retirement.timer')}
report['checks']['retirement_masked'] = all(v == '/dev/null' for v in report['retirement_masks'].values())
report['journals'] = {}
for label, start, end, units in [
    ('noon', '2026-10-13 12:18:00', '2026-10-13 12:26:00', ['daily','clock-ready']),
    ('backup-archive', '2026-10-13 13:09:00', '2026-10-13 14:45:00', ['backup','restic-archive','clock-ready']),
]:
    argv = ['/usr/bin/journalctl','-b','--since',start,'--until',end,'--no-pager','-o','json',
            '--output-fields=__REALTIME_TIMESTAMP,__MONOTONIC_TIMESTAMP,_BOOT_ID,_SYSTEMD_UNIT,_SYSTEMD_INVOCATION_ID,_UID,_PID,MESSAGE']
    for unit in units:
        argv += ['-u', 'marketdata-' + unit + '.service']
    result = subprocess.run(argv,capture_output=True,timeout=45)
    assert result.returncode == 0, result.stderr.decode()
    raw = gzip.compress(result.stdout,mtime=0)
    name = label + '-journal.jsonl.gz'
    with (HERE/name).open('xb') as handle:
        handle.write(raw)
    report['journals'][label] = {'argv':argv,'code':0,'stderr':result.stderr.decode(),'path':name,
                                'sha256':hashlib.sha256(raw).hexdigest(),'rows':len(result.stdout.splitlines())}
report['passed'] = all(report['checks'].values())
with (HERE/'host-state.json').open('x') as handle:
    json.dump(report,handle,ensure_ascii=False,indent=2)
    handle.write('\n')
print(json.dumps({'passed':report['passed'],'checks':report['checks'],'service_results':report['service_results']},indent=2))
