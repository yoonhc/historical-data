"""Read current operating state; write evidence only beside this script."""
import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CANDIDATE = HERE.parent / 't13-daily-review-2026-10-10'
PROPS = 'Id,ActiveState,SubState,Result,MainPID,ControlPID,InvocationID,FragmentPath,DropInPaths,UnitFileState,ExecStart,ExecStartPre,Requires,After,Restart,RestartUSec,TimeoutStartUSec,TimeoutStopUSec,NeedDaemonReload,NextElapseUSecRealtime,TimersCalendar,TimersMonotonic,Unit,RandomizedDelayUSec,AccuracyUSec,Persistent'


def command(args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=25)
    return {'argv': args, 'code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    before = json.loads((CANDIDATE / 'operating-before.json').read_text())
    bootstrap = json.loads(Path('/etc/marketdata/clock-bootstrap.json').read_text())
    protected = set(before['operating_file_hashes']) | set(bootstrap['guard_files']) | {
        '/var/lib/marketdata-clock/boot-state.json', '/etc/adjtime',
        '/etc/marketdata/clock-bootstrap.json', '/etc/marketdata/runtime.json'}
    units = [n for n in bootstrap['user_units'] if n.endswith(('.service', '.timer'))]
    user = {n: command(['/usr/bin/systemctl', '--user', 'show', n, '--property='+PROPS]) for n in units}
    system = {n: command(['/usr/bin/systemctl', 'show', n, '--property='+PROPS])
              for n in ['chrony.service', *bootstrap['system_collectors']]}
    files = {}
    unit_dir = Path('/home/yoonhc/.config/systemd/user')
    for name in ['marketdata-daily.service', 'marketdata-daily.timer']:
        p = unit_dir / name
        s = p.lstat()
        files[str(p)] = {'sha256': sha(p), 'text': p.read_text(),
                        'symlink': os.readlink(p) if p.is_symlink() else None,
                        'uid': s.st_uid, 'gid': s.st_gid, 'mode': s.st_mode & 0o777}
    with sqlite3.connect((ROOT/'state/catalog.sqlite').as_uri()+'?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'daily_%' ORDER BY name")]
    gate = command(['/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee/.venv/bin/python',
                    '-B', '-I', '/opt/marketdata/releases/system-bootstrap-20261007-f85eb8321a19c1ee/integrations/runtime/clock_gate.py',
                    'check', '--profile', '/etc/marketdata/clock-bootstrap.json'])
    result = {'as_of': datetime.now(timezone.utc).isoformat(),
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'protected_hashes': {p: sha(p) for p in sorted(protected)},
              'source_hashes': {p: sha(ROOT/p) for p in before['source_hashes']},
              'daily_files': files, 'retirement_masks': {p: os.readlink(p) for p in before['retirement_masks']},
              'daily_tables': tables, 'user_units': user, 'system_units': system, 'strict_gate': gate}
    path = HERE/'operating-before.json'
    with path.open('x') as f:
        json.dump(result, f, indent=2); f.write('\n')
    print(json.dumps({'report': str(path), 'daily_tables': tables, 'strict_gate': gate,
                      'unit_read_errors': [n for n,v in {**user,**system}.items() if v['code']]}, indent=2))


if __name__ == '__main__':
    main()
