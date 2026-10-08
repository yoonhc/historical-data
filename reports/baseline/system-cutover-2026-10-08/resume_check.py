"""Read-only first Linux return check. Never unmask units or change the clock/RTC."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent


def command(args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=30)
    return {'args': args, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}


def main():
    before = json.loads((HERE / 'before.json').read_text())
    units = sorted(before['units'])
    queried = command(['/usr/bin/systemctl', '--user', 'show', *units,
                       '-p', 'Id', '-p', 'LoadState', '-p', 'UnitFileState', '-p', 'ActiveState', '-p', 'MainPID'])
    masks = {}
    for block in queried['stdout'].strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if 'Id' in values:
            path = Path('/home/yoonhc/.config/systemd/user') / values['Id']
            masks[values['Id']] = {'properties': values,
                'persistent_mask': path.is_symlink() and os.readlink(path) == '/dev/null',
                'inactive': values.get('ActiveState') in ('inactive', 'failed') and values.get('MainPID', '0') == '0'}
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    values = {'checked_at': datetime.now().astimezone().isoformat(), 'boot_id': boot,
              'different_boot_from_hold': boot != before['boot_id'], 'units': masks,
              'all_twenty_held': len(masks) == 20 and all(x['persistent_mask'] and x['inactive'] and
                 x['properties'].get('LoadState') == 'masked' and x['properties'].get('UnitFileState') == 'masked' for x in masks.values()),
              'commands': [queried,
                  command(['/usr/bin/systemctl', '--user', 'list-jobs', '--no-pager']),
                  command(['/usr/bin/timedatectl', 'show']),
                  command(['/usr/bin/timedatectl', 'timesync-status']),
                  command(['/usr/bin/systemctl', 'show', 'systemd-timesyncd.service', 'chrony.service', '-p', 'Id', '-p', 'LoadState', '-p', 'ActiveState', '-p', 'InvocationID']),
                  command(['/usr/bin/findmnt', '-T', '/mnt/backup', '-o', 'TARGET,SOURCE,UUID,FSTYPE,OPTIONS']),
                  command(['/usr/bin/findmnt', '-T', '/home/yoonhc/projects/algoTrade/historical_data', '-o', 'TARGET,SOURCE,UUID,FSTYPE,OPTIONS'])],
              'adjtime': Path('/etc/adjtime').read_text(), 'changes_performed': False,
              'windows_configuration_verified_by_this_script': False}
    now_token = datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%f')
    path = HERE / f'resume-observation-{boot}-{now_token}.json'
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report': str(path), 'different_boot_from_hold': values['different_boot_from_hold'],
                      'all_twenty_held': values['all_twenty_held'], 'changes_performed': False}, ensure_ascii=False))
    return 0 if values['all_twenty_held'] and queried['returncode'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
