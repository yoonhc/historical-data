"""Read-only host inspection; writes only evidence into this workspace folder.

No service control, live preview, registration, archive operation or deletion.
"""
import hashlib
import json
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = Path('/etc/marketdata/checkpoint-clock-retirement')
RELEASE = Path('/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7')
NAMES = ('marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer')
EXPECTED_SCOPE = '84a0b92ae6f8bf0e38ac10ecb4a2615a32af72aebf1c8c28d591c02087cf4983'


def run(argv):
    env = {**os.environ, 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'}
    return subprocess.run(list(map(str, argv)), capture_output=True, text=True,
                          env=env, timeout=45, check=True).stdout


def fingerprint(path):
    info = path.lstat()
    result = {'path': str(path), 'uid': info.st_uid, 'gid': info.st_gid,
              'mode': f'{stat.S_IMODE(info.st_mode):04o}'}
    if path.is_symlink():
        result.update(kind='symlink', target=os.readlink(path))
    else:
        assert stat.S_ISREG(info.st_mode)
        result.update(kind='file', sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def units(manager, names):
    raw = run(['/usr/bin/systemctl', '--' + manager, 'show', *names,
               '--property=Id,LoadState,ActiveState,SubState,MainPID,ControlPID,InvocationID,Job,FragmentPath,DropInPaths,NeedDaemonReload'])
    return {row['Id']: row for block in raw.strip().split('\n\n')
            if (row := dict(line.split('=', 1) for line in block.splitlines()))}


def main():
    started = datetime.now(timezone.utc).isoformat()
    before_boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    assert os.getuid() == 1000, 'Run as the service account'
    paths = [CONFIG / name for name in ('settings.toml', 'policy.toml', 'clock-binding.json',
                                       'recurring.json', 'manual.json')]
    paths += sorted((CONFIG / 'units').glob('*.service')) + sorted((CONFIG / 'units').glob('*.timer'))
    before = {str(p): fingerprint(p) for p in paths}
    assert all(v['kind'] == 'file' and v['uid'] == v['gid'] == 0 and v['mode'] == '0644'
               for v in before.values()), 'Canonical files must remain root:root/0644'
    proposal = json.loads(run([RELEASE / '.venv/bin/python', '-B', '-I',
                              RELEASE / 'scripts/run_clock_retirement.py',
                              '--settings', CONFIG / 'settings.toml', 'propose']))
    grant = json.loads((CONFIG / 'recurring.json').read_bytes())
    assert grant == proposal and grant['scope_id'] == EXPECTED_SCOPE
    assert grant['enabled'] is False and grant['approved_at'] is None and grant['approval_reference'] is None
    assert 'scope_transition' not in grant
    assert json.loads((CONFIG / 'manual.json').read_bytes())['enabled'] is False
    masks = [fingerprint(base / n) for base in (Path('/etc/systemd/system'),
                                               Path('/home/yoonhc/.config/systemd/user')) for n in NAMES]
    assert all(m['kind'] == 'symlink' and m['target'] == '/dev/null' for m in masks)
    managers = {m: units(m, NAMES) for m in ('system', 'user')}
    assert all(r['LoadState'] == 'masked' and r['ActiveState'] == 'inactive' and r['Job'] == ''
               and (r['Id'].endswith('.timer') or r['MainPID'] == r['ControlPID'] == '0')
               for rows in managers.values() for r in rows.values())
    processes = units('system', ('chrony.service', 'marketdata-collector@depth.service',
                                'marketdata-collector@aggtrade.service'))
    previous = json.loads((HERE.parent / 't05-mount-recovery-2026-10-17' /
                           'completion-evidence/root-result.json').read_bytes())
    # The completed root operation embeds the process invariants in its frozen plan;
    # compare against the original deployment identities without requiring no new child.
    expected = {'chrony.service': ('1245', '19420480ab3249529a075ca7cb2bd3c1'),
                'marketdata-collector@depth.service': ('224785', '41b1747221894331a5e0b4fdf3f9751e'),
                'marketdata-collector@aggtrade.service': ('224784', '1aa42e55ff804c1f9e5ec68b31116c6b')}
    assert previous['protected_inputs_and_processes_unchanged']
    assert all((processes[n]['MainPID'], processes[n]['InvocationID']) == ids
               and processes[n]['ActiveState'] == 'active' for n, ids in expected.items())
    after = {str(p): fingerprint(p) for p in paths}
    assert before == after and before_boot == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    sources = HERE / 'canonical'
    sources.mkdir(exist_ok=True)
    for name in NAMES:
        raw = (CONFIG / 'units' / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == before[str(CONFIG / 'units' / name)]['sha256']
        (sources / name).write_bytes(raw)
    result = {'passed': True, 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
              'boot_id': before_boot, 'files': after, 'proposal_scope_id': proposal['scope_id'],
              'proposal_matches_disabled_grant': True, 'masks': masks, 'managers': managers,
              'protected_processes': processes, 'operating_writes': False,
              'actual_automatic_fragment': 'masked; not installed or executed by this inspection',
              'limits': 'No preview, archive read-data check, checkpoint inventory or whole-data verification.'}
    (HERE / 'configuration-inspection.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': True, 'scope_id': proposal['scope_id'], 'automatic_retirement': 'masked',
                      'report': str(HERE / 'configuration-inspection.json')}, indent=2))


if __name__ == '__main__':
    main()
