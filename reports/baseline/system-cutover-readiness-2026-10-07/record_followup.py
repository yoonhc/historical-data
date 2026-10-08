"""Capture the completed update and read-only host checks; never manage services."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
HERE = Path(__file__).resolve().parent
BASE = ROOT / 'reports/baseline/system-runtime-root-preflight-2026-10-07'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def record(path):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'exists': False}
    result = {'exists': True, 'uid': info.st_uid, 'gid': info.st_gid,
              'mode': oct(stat.S_IMODE(info.st_mode))}
    if path.is_symlink():
        result['link'] = os.readlink(path)
    if path.is_file():
        result['sha256'] = sha(path)
    if path.is_dir() and not path.is_symlink():
        result['children'] = {str(p.relative_to(path)): record(p) for p in sorted(path.iterdir())}
    return result


def command(argv):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=30,
                       env={**os.environ, 'LC_ALL': 'C', 'SYSTEMD_COLORS': '0'})
    return {'argv': argv, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}


def services(output):
    result = {}
    for block in output.strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if 'Id' in values:
            result[values['Id']] = values
    return result


def main():
    inputs = json.loads((BASE / 'inputs.json').read_text())
    baseline = json.loads((BASE / 'result-1f383e4dff67457d.json').read_text())['after']
    before = services(json.loads((HERE / 'before-update-services.json').read_text())['commands'][0]['stdout'])
    units = sorted({Path(p).name for p in inputs['installed_user_units']})
    properties = ['Id', 'ActiveState', 'SubState', 'Result', 'MainPID', 'InvocationID',
                  'NRestarts', 'ExecMainStartTimestamp', 'ExecMainExitTimestamp',
                  'ExecMainStatus', 'UnitFileState', 'NextElapseUSecRealtime']
    query = command(['/usr/bin/systemctl', '--user', 'show', *units,
                     *[f'--property={p}' for p in properties]])
    assert query['returncode'] == 0, query
    current = services(query['stdout'])
    daily = current['marketdata-daily.service']
    assert daily['InvocationID'] == 'd2f9726d44944831bb651708dbc3032b'
    assert daily['Result'] == 'success' and daily['ExecMainStatus'] == '0'
    assert daily['ActiveState'] == 'inactive'
    source = ROOT / 'state/daily-latest.json'
    copied = HERE / 'daily-after-update.json'
    copied.write_bytes(source.read_bytes())
    updated = json.loads(copied.read_text())
    assert len(updated) == 8 and all(r['complete'] and r['state'] == 'complete' and not r['errors'] for r in updated)
    old = json.loads((HERE / 'daily-latest.json').read_text())
    missing_keys = {r['key'] for r in old if not r['complete']}
    resolved = []
    for row in updated:
        if row['key'] in missing_keys:
            day = next(r for r in row['results'] if r.get('date') == '2026-10-06')
            assert row['coverage']['missing_rows'] == 0
            resolved.append({'key': row['key'], **day, 'coverage': row['coverage'],
                             'coverage_before': row['coverage_before']})
    assert len(resolved) == 4 and sum(r['rows'] for r in resolved) == 4608
    journal = command(['/usr/bin/journalctl', '--user',
                       '_SYSTEMD_INVOCATION_ID=' + daily['InvocationID'],
                       '--no-pager', '--all', '-o', 'json'])
    assert journal['returncode'] == 0
    (HERE / 'update-journal.json').write_text(json.dumps(journal, ensure_ascii=False, indent=2) + '\n')
    journal_reports = []
    for line in journal['stdout'].splitlines():
        msg = json.loads(line).get('MESSAGE')
        if isinstance(msg, str) and msg.lstrip().startswith('['):
            journal_reports.append(json.loads(msg))
    assert updated in journal_reports, 'daily report does not match invocation journal'
    hash_groups = {}
    for group in ('pinned_files', 'operating_files', 'installed_user_units'):
        expected = inputs[group]
        changed = [p for p, digest in expected.items() if sha(p) != digest]
        hash_groups[group] = {'checked': len(expected), 'changed': changed, 'unchanged': not changed}
    host_files = {p: record(p) for p in inputs['host_paths']}
    host_changes = [p for p, value in host_files.items() if baseline['files'][p] != value]
    system = command(baseline['system_services']['argv'])
    packages = command(baseline['packages']['argv'])
    user_files = command(['/usr/bin/systemctl', '--user', 'list-unit-files', 'marketdata-*', '--no-pager', '--no-legend'])
    clock = command(['/usr/bin/timedatectl', 'show', '-p', 'NTPSynchronized', '-p', 'LocalRTC', '-p', 'Timezone'])
    collectors = {name: {field: before[name][field] == current[name][field]
                        for field in ('MainPID', 'InvocationID', 'ExecMainStartTimestamp')}
                  for name in ('marketdata-depth.service', 'marketdata-aggtrade.service')}
    checks = {
        'pinned_candidate_unchanged': hash_groups['pinned_files']['unchanged'],
        'operating_source_unchanged': hash_groups['operating_files']['unchanged'],
        'installed_user_units_unchanged': hash_groups['installed_user_units']['unchanged'],
        'host_configuration_unchanged': not host_changes,
        'system_time_services_unchanged': system['stdout'] == baseline['system_services']['stdout'],
        'packages_unchanged': packages['stdout'] == baseline['packages']['stdout'],
        'unit_enablement_unchanged': user_files['stdout'] == baseline['user_unit_files']['stdout'],
        'collector_processes_unchanged': all(all(v.values()) for v in collectors.values()),
        'same_boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip() == baseline['boot_id'],
        'daily_complete': True, 'daily_journal_matches_report': True,
    }
    report = {'checked_at': datetime.now().astimezone().isoformat(), 'checks': checks,
              'passed': all(checks.values()), 'services': query, 'service_map': current,
              'system_services': system, 'packages': packages, 'user_unit_files': user_files,
              'clock': clock, 'hash_groups': hash_groups,
              'host_files': host_files, 'host_changes': host_changes,
              'collector_comparison': collectors, 'resolved': resolved,
              'daily_report_sha256': sha(copied), 'new_backup_created': False,
              'reset_failed_performed': False, 'operating_transition_applied': False}
    (HERE / 'followup-validation.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'passed': report['passed'], 'checks': checks, 'clock': clock['stdout'],
                      'daily': daily, 'resolved_rows': sum(r['rows'] for r in resolved)}, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
