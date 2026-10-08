"""Read-only operating service/journal evidence for the scheduled backup follow-up."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')


def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                       env={**os.environ, 'LC_ALL': 'C', 'SYSTEMD_COLORS': '0'})
    return {'argv': argv, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}


def main():
    inputs = json.loads((ROOT / 'reports/baseline/system-runtime-root-preflight-2026-10-07/inputs.json').read_text())
    units = sorted({Path(p).name for p in inputs['installed_user_units']})
    properties = ['Id', 'ActiveState', 'SubState', 'Result', 'MainPID', 'InvocationID',
                  'NRestarts', 'ExecMainStartTimestamp', 'ExecMainExitTimestamp',
                  'ExecMainStatus', 'UnitFileState', 'NextElapseUSecRealtime']
    query = run(['/usr/bin/systemctl', '--user', 'show', *units,
                 *[f'--property={p}' for p in properties]])
    assert query['returncode'] == 0
    services = {}
    for block in query['stdout'].strip().split('\n\n'):
        fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if 'Id' in fields:
            services[fields['Id']] = fields
    journal = run(['/usr/bin/journalctl', '--user', '--since', '2026-10-08 13:00:00',
                   '--until', '2026-10-08 16:30:00', '-u', 'marketdata-backup.service',
                   '-u', 'marketdata-restic-archive.service', '-u', 'marketdata-checkpoint-retirement.service',
                   '--no-pager', '--all', '-o', 'json'])
    assert journal['returncode'] == 0
    (HERE / 'scheduled-journal.json').write_text(json.dumps(journal, ensure_ascii=False, indent=2) + '\n')
    hashes = {}
    for group in ('pinned_files', 'operating_files', 'installed_user_units'):
        changed = [p for p, digest in inputs[group].items()
                   if hashlib.sha256(Path(p).read_bytes()).hexdigest() != digest]
        hashes[group] = {'checked': len(inputs[group]), 'unchanged': not changed, 'changed': changed}
    clock = run(['/usr/bin/timedatectl', 'show', '-p', 'NTPSynchronized', '-p', 'LocalRTC', '-p', 'Timezone'])
    time_units = run(['/usr/bin/systemctl', 'show', 'systemd-timesyncd.service', 'chrony.service',
                      '-p', 'Id', '-p', 'LoadState', '-p', 'ActiveState', '-p', 'InvocationID'])
    report = {'checked_at': datetime.now().astimezone().isoformat(), 'read_only': True,
              'service_command': query, 'services': services, 'clock': clock,
              'time_services': time_units, 'hash_groups': hashes,
              'operating_transition_applied': False, 'services_started_stopped_or_reset': False}
    (HERE / 'service-observation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'checked_at': report['checked_at'], 'services': {
        name: services[name] for name in ('marketdata-backup.service', 'marketdata-restic-archive.service',
                                         'marketdata-daily.service', 'marketdata-checkpoint-retirement.service')},
        'hash_groups': hashes, 'clock': clock['stdout']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
