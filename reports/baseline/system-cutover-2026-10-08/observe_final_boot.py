"""Read-only host observation; writes only new evidence under this report directory.

Run outside the tool's UID namespace as yoonhc, without sudo. No service control,
network market-data requests, clock changes, ledger writes, or cleanup commands.
"""

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
RELEASE = Path('/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee')
PYTHON = str(RELEASE / '.venv/bin/python')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('name')
    args = parser.parse_args()
    if not args.name.startswith('post-reboot-') or Path(args.name).name != args.name:
        raise ValueError('Evidence subdirectory name required')
    dest = BASE / args.name
    dest.mkdir(exist_ok=False)

    def save(name, data):
        (dest / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')

    results = {}

    def command(name, argv):
        start = datetime.now().astimezone().isoformat()
        p = subprocess.run(argv, capture_output=True, text=True, timeout=35,
                           env={**os.environ, 'LC_ALL': 'C'}, cwd=ROOT)
        (dest / (name + '.stdout')).write_text(p.stdout)
        (dest / (name + '.stderr')).write_text(p.stderr)
        results[name] = {'argv': argv, 'started_at': start,
                         'finished_at': datetime.now().astimezone().isoformat(),
                         'returncode': p.returncode}
        return p

    boot_profile = json.loads(Path('/etc/marketdata/clock-bootstrap.json').read_text())
    sys_units = ['chrony.service', 'chrony-wait.service', 'marketdata-clock-bootstrap.service',
                 *boot_profile['system_collectors'], 'systemd-timesyncd.service']
    props = ['Id', 'LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'Result',
             'MainPID', 'ControlPID', 'User', 'InvocationID', 'NRestarts', 'ExecMainStatus',
             'ExecMainStartTimestamp', 'ExecMainStartTimestampMonotonic', 'ExecMainExitTimestamp',
             'ActiveEnterTimestamp', 'ActiveEnterTimestampMonotonic', 'ControlGroup',
             'ExecStart', 'DropInPaths', 'FragmentPath']
    flags = [arg for prop in props for arg in ('-p', prop)]
    command('system-units', ['systemctl', 'show', *sys_units, *flags])
    command('user-units', ['systemctl', '--user', 'show', *boot_profile['user_units'], *flags])
    command('timers', ['systemctl', '--user', 'list-timers', 'marketdata-*', '--all', '--no-pager'])
    command('user-jobs', ['systemctl', '--user', 'list-jobs', '--no-pager'])
    command('linger', ['loginctl', 'show-user', 'yoonhc', '-p', 'Linger'])
    command('timedate', ['timedatectl', 'show', '-p', 'Timezone', '-p', 'LocalRTC',
                          '-p', 'NTP', '-p', 'NTPSynchronized'])
    command('tracking', ['chronyc', '-c', 'tracking'])
    command('sources', ['chronyc', '-n', 'sources'])
    for name, units, user in [('system-journal', sys_units[:-1], False),
                             ('user-journal', list(boot_profile['user_units']), True)]:
        command(name, ['journalctl', *(['--user'] if user else []), '-b', '--no-pager', '--all',
                       '-o', 'json', *[a for u in units for a in ('-u', u)]])
    for action in ('status', 'check'):
        command('gate-' + action, [PYTHON, '-I', str(RELEASE / 'integrations/runtime/clock_gate.py'),
                                  action, '--profile', '/etc/marketdata/clock-bootstrap.json'])
    for stream in ('depth', 'aggtrade'):
        command(stream + '-clock-status', [PYTHON, '-I',
                str(RELEASE / 'integrations/runtime/marketdata_runtime.py'), 'status',
                '--profile', '/etc/marketdata/runtime.json', '--stream', stream])
        command(stream + '-data-status', [PYTHON, '-I', '-c',
                'from marketdata.cli import main; raise SystemExit(main())',
                '--root', str(ROOT), stream + '-status'])

    files = ['/var/lib/marketdata-clock/boot-state.json', '/var/lib/marketdata-clock/events.jsonl',
             '/etc/adjtime', '/etc/marketdata/runtime.json', '/etc/marketdata/clock-bootstrap.json']
    files += [str(ROOT / 'state' / (x + '-latest.json')) for x in
              ('depth', 'aggtrade', 'daily', 'oi', 'oi-probe', 'maintenance-hourly', 'maintenance-daily')]
    for stream in ('depth', 'aggtrade'):
        sroot = ROOT / 'state/runtime-clock' / stream
        latest = json.loads((sroot / 'latest.json').read_text())
        files.extend([str(sroot / 'latest.json'), str(sroot / (latest['run_id'] + '.jsonl'))])
    copied = {}
    for i, path in enumerate(files):
        blob = Path(path).read_bytes()
        name = f'file-{i:02d}-{Path(path).name}'
        (dest / name).write_bytes(blob)
        copied[path] = {'evidence_file': name, 'sha256': hashlib.sha256(blob).hexdigest()}

    before = json.loads((BASE / 'before.json').read_text())
    checks = {path: sha(path) == record['sha256']
              for path, record in before['config_hashes'].items()}
    runtime_receipt = json.loads((BASE / 'installed-runtime-manifest.json').read_text())
    runtime_hashes = {p: sha(RELEASE / p) == digest
                      for p, digest in runtime_receipt['runtime_files_sha256'].items()}
    guards = {p: sha(p) == digest for p, digest in boot_profile['guard_files'].items()}
    ownership = {str(p): {'uid': p.stat().st_uid, 'gid': p.stat().st_gid,
                          'mode': oct(p.stat().st_mode & 0o7777)}
                 for p in [RELEASE, RELEASE / '.venv', Path('/etc/marketdata/runtime.json'),
                           Path('/var/lib/marketdata-clock/boot-state.json')]}
    processes = []
    for p in Path('/proc').iterdir():
        if not p.name.isdecimal():
            continue
        try:
            argv = (p / 'cmdline').read_bytes().decode().split('\0')
            if not argv or not any('marketdata_runtime.py' in a or a in
                                   ('depth-stream', 'aggtrade-stream', '/usr/sbin/chronyd') for a in argv):
                continue
            processes.append({'pid': int(p.name), 'argv': argv, 'uid': p.stat().st_uid,
                              'cgroup': (p / 'cgroup').read_text()})
        except (OSError, UnicodeError):
            continue
    command('data-mount', ['findmnt', '-T', str(ROOT), '-o', 'TARGET,SOURCE,UUID,OPTIONS', '--json'])
    command('backup-mount', ['findmnt', '-T', '/mnt/backup', '-o', 'TARGET,SOURCE,UUID,OPTIONS', '--json'])

    c = sqlite3.connect(f'file:{ROOT}/state/catalog.sqlite?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    c.execute('BEGIN')
    collections = {}
    for stream, dataset in [('depth', 'book_depth'), ('aggtrade', 'agg_trades')]:
        row = c.execute("SELECT * FROM event_collections WHERE dataset=? AND kind='websocket' "
                        'ORDER BY started_at DESC LIMIT 1', (dataset,)).fetchone()
        rec = dict(row)
        rec['report'] = json.loads(rec['report'])
        segments = [dict(x) for x in c.execute('SELECT * FROM event_segments WHERE collection_id=? '
                                              'ORDER BY first_id', (rec['id'],))]
        rec['segments'] = segments
        rec['segment_rows'] = sum(x['row_count'] for x in segments)
        rec['artifacts'] = [dict(x) for x in c.execute('SELECT DISTINCT a.* FROM artifacts a JOIN '
                           'event_segments s ON s.source_id=a.id WHERE s.collection_id=?', (rec['id'],))]
        rec['recoveries'] = []
        for entry in rec['report'].get('recent_recoveries', []):
            if entry.get('collection_id'):
                r = c.execute('SELECT * FROM event_collections WHERE id=?', (entry['collection_id'],)).fetchone()
                if r:
                    recovery = dict(r)
                    recovery['report'] = json.loads(recovery['report'])
                    rec['recoveries'].append(recovery)
        collections[stream] = rec
    c.close()
    save('collections.json', collections)
    runtime_events = {}
    for stream in ('depth', 'aggtrade'):
        latest = json.loads((ROOT / f'state/runtime-clock/{stream}/latest.json').read_text())
        lines = (ROOT / f"state/runtime-clock/{stream}/{latest['run_id']}.jsonl").read_text().splitlines()
        rows = [json.loads(line) for line in lines]
        runtime_events[stream] = {'events': len(rows), 'states': dict(Counter(r.get('state') for r in rows)),
                                  'first': rows[0], 'last': rows[-1],
                                  'max_violations': max(r.get('violations', 0) for r in rows)}
    save('runtime-events.json', runtime_events)
    audit = {'captured_at': datetime.now().astimezone().isoformat(), 'uid': os.getuid(),
             'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
             'uid_map': Path('/proc/self/uid_map').read_text(),
             'rtc_date': Path('/sys/class/rtc/rtc0/date').read_text().strip(),
             'rtc_time': Path('/sys/class/rtc/rtc0/time').read_text().strip(),
             'system_utc': datetime.now().astimezone().astimezone(__import__('datetime').timezone.utc).isoformat(),
             'runtime_hash_checks': runtime_hashes, 'guard_hash_checks': guards,
             'original_config_checks': checks, 'ownership': ownership, 'processes': processes,
             'copied_files': copied, 'commands': results,
             'scope': 'Observation only; no service, time, ledger, policy or archive data mutation requested'}
    save('observation.json', audit)
    compact = {'at': audit['captured_at'], 'boot_id': audit['boot_id'], 'output': str(dest),
               'unchanged_business_config_match': all(v for p, v in checks.items() if p != '/etc/adjtime'),
               'rtc_basis_is_utc': Path('/etc/adjtime').read_text().splitlines()[-1] == 'UTC',
               'guard_hashes_match': all(guards.values()),
               'runtime_hashes_match': all(runtime_hashes.values()),
               'command_codes': {k: v['returncode'] for k, v in results.items()},
               'collections': {k: {'id': v['id'], 'segment_rows': v['segment_rows'],
                                    'messages': v['report'].get('messages'),
                                    'persisted_messages': v['report'].get('persisted_messages'),
                                    'unrepaired_gaps': v['report'].get('unrepaired_gaps'),
                                    'sync_losses': v['report'].get('sync_losses')}
                               for k, v in collections.items()}}
    print(json.dumps(compact, ensure_ascii=False))


if __name__ == '__main__':
    main()
