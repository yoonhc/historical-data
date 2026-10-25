"""Native-host read-only systemd/journal/config checks; no service control."""
from pathlib import Path
import datetime as dt
import hashlib
import json
import os
import stat
import subprocess

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
OUT = Path(__file__).resolve().parent
JOBS = ('oi', 'oi-probe', 'oi-quality', 'daily', 'pack', 'backup', 'restic-archive', 'network-observer')
RELEASE = '/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11'
calls = []


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(argv):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    calls.append({'argv': argv, 'read_at': now(), 'returncode': r.returncode, 'stdout': r.stdout, 'stderr': r.stderr})
    if r.returncode:
        raise RuntimeError(str(argv) + ': ' + r.stderr)
    return r.stdout


def show(units, user=False):
    props = 'Id,LoadState,ActiveState,SubState,UnitFileState,User,Group,MainPID,InvocationID,ExecStart,ExecMainStatus,Result,FragmentPath,DropInPaths,Requires,After,TriggeredBy,Triggers,LastTriggerUSec,NextElapseUSecRealtime,Persistent,Restart,ProtectClock,ReadWritePaths'
    raw = run(['systemctl'] + (['--user'] if user else []) + ['show', *units, '--property=' + props])
    return {d['Id']: d for block in raw.strip().split('\n\n') if (d := dict(line.split('=', 1) for line in block.splitlines() if '=' in line))}


started = now()
plan = json.loads((ROOT / 'reports/baseline/scheduled-clock-repair-deploy-2026-10-16/operating-evidence/root-plan.json').read_bytes())
configs = {}
for path, op in plan['inner']['operations'].items():
    p = Path(path)
    expected = op['after']
    s = p.lstat()
    actual = {'kind': 'file' if stat.S_ISREG(s.st_mode) else 'other', 'mode': stat.S_IMODE(s.st_mode),
              'uid': s.st_uid, 'gid': s.st_gid, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    assert actual['kind'] == expected['kind'] == 'file'
    assert actual['sha256'] == expected['sha256'] and actual['mode'] == expected['mode']
    assert actual['uid'] == actual['gid'] == 0
    configs[path] = actual
services = [f'marketdata-{j}.service' for j in JOBS]
timers = [f'marketdata-{j}.timer' for j in JOBS]
protected = ['chrony.service', 'marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service']
units = show(services + timers + protected + ['marketdata-jobs-clock-ready.service', 'marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer'])
for u in services:
    assert RELEASE in units[u]['ExecStart'] and units[u]['User'] == 'yoonhc'
    assert 'marketdata-jobs-clock-ready.service' in units[u]['Requires']
    assert 'marketdata-clock-ready.service' not in units[u]['Requires']
for u in timers:
    assert units[u]['ActiveState'] == 'active' and units[u]['UnitFileState'] == 'enabled'
users = show(services + timers + ['marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer'], user=True)
assert all(v['LoadState'] == 'masked' and v['ActiveState'] == 'inactive' for v in users.values())
identities = {
    'chrony.service': ('1245', '19420480ab3249529a075ca7cb2bd3c1'),
    'marketdata-collector@depth.service': ('224785', '41b1747221894331a5e0b4fdf3f9751e'),
    'marketdata-collector@aggtrade.service': ('224784', '1aa42e55ff804c1f9e5ec68b31116c6b'),
}
assert all((units[u]['MainPID'], units[u]['InvocationID']) == ident and units[u]['ActiveState'] == 'active' for u, ident in identities.items())
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == plan['boot_id']
ledger_path = Path(plan['inner']['ledger_path'])
ledger_raw = ledger_path.read_bytes()
ledger = json.loads(ledger_raw)
(OUT / 'current-boot-ledger.json').write_bytes(ledger_raw)
for label, start, end in [('noon', '2026-10-17 12:19:30', '2026-10-17 12:26:00'), ('evening', '2026-10-17 17:59:30', '2026-10-17 18:06:00')]:
    raw = run(['journalctl', '--no-pager', '-u', 'marketdata-daily.service', '--since', start, '--until', end, '-o', 'json'])
    (OUT / f'daily-{label}-system-journal.jsonl').write_text(raw)

t05 = {}
for name in ('manual.json', 'recurring.json'):
    p = Path('/etc/marketdata/checkpoint-clock-retirement') / name
    raw = p.read_bytes()
    t05[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'content': json.loads(raw)}
assert t05['recurring.json']['sha256'] == '3b436c4f0803582c6a64414a009abf2ab212945584f6ba3211ec1efbaff8035d'
assert t05['recurring.json']['content']['enabled'] is True
assert t05['manual.json']['content']['enabled'] is False
assert units['marketdata-checkpoint-retirement.timer']['ActiveState'] == 'active'
assert units['marketdata-checkpoint-retirement.timer']['NextElapseUSecRealtime'] == 'Mon 2026-10-19 15:40:00 KST'
result = {'started_at': started, 'finished_at': now(), 'passed': True, 'operational_mutations': 0,
          'configuration_after_matches': configs, 'units': units, 'user_units': users,
          'protected_processes_match': True, 'boot_id': plan['boot_id'],
          'ledger_sha256': hashlib.sha256(ledger_raw).hexdigest(), 't05': t05,
          'source_plan': str(ROOT / 'reports/baseline/scheduled-clock-repair-deploy-2026-10-16/operating-evidence/root-plan.json')}
(OUT / 'host-review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
(OUT / 'host-commands.json').write_text(json.dumps(calls, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'passed': True, 'configuration_after_matches': len(configs), 'system_job_timers': len(timers),
                  'user_masks': len(users), 'protected_processes_match': True,
                  'T05_next': units['marketdata-checkpoint-retirement.timer']['NextElapseUSecRealtime'],
                  'journal_reads': 2, 'operational_mutations': 0}, ensure_ascii=False))
