"""Read-only installed baseline. Never open Store, credentials, or a control command."""
import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
NAMES = ('oi', 'oi-probe', 'oi-quality', 'daily', 'pack', 'backup',
         'restic-archive', 'network-observer')
USER = Path('/home/yoonhc/.config/systemd/user')
PROPS = ('Id,LoadState,ActiveState,SubState,MainPID,ControlPID,InvocationID,'
         'FragmentPath,DropInPaths,ExecStart,ExecStartPre,ExecStartPost,ExecStop,ExecStopPost,'
         'After,Requires,Wants,User,WorkingDirectory,Restart,RestartUSec,KillMode,KillSignal,'
         'TimeoutStartUSec,TimeoutStopUSec,RuntimeMaxUSec,StartLimitIntervalUSec,StartLimitBurst,'
         'UnitFileState,NeedDaemonReload,SuccessExitStatus,Unit,Persistent,TimersCalendar,'
         'TimersMonotonic,AccuracyUSec,RandomizedDelayUSec')

def digest(blob):
    return hashlib.sha256(blob).hexdigest()

def node(path):
    p = Path(path)
    if p.is_symlink():
        return {'kind': 'symlink', 'target': os.readlink(p)}
    if not p.exists():
        return {'kind': 'absent'}
    raw = p.read_bytes()
    return {'kind': 'file', 'mode': p.stat().st_mode & 0o777,
            'sha256': digest(raw), 'text': raw.decode()}

def command(manager, *args):
    assert args[0] in ('show', 'list-unit-files', 'list-jobs')
    return subprocess.check_output(['/usr/bin/systemctl', '--' + manager, *args],
                                   text=True, timeout=30)

def capture():
    files, effective = {}, {}
    for name in NAMES:
        for ext in ('service', 'timer'):
            unit = f'marketdata-{name}.{ext}'
            p = USER / unit
            files[str(p)] = node(p)
            # Unit source text is evidence; symlink itself is preserved for rollback.
            effective[unit] = {'base': p.read_text(), 'dropins': {},
                'show': command('user', 'show', unit, '--no-pager', '--property=' + PROPS)}
            for root in (Path('/etc/systemd/user'), USER):
                for drop in sorted((root / (unit + '.d')).glob('*.conf')):
                    files[str(drop)] = node(drop)
                    effective[unit]['dropins'][str(drop)] = drop.read_text()
            if ext == 'timer':
                link = USER / 'timers.target.wants' / unit
                files[str(link)] = node(link)
    pins = [Path('/etc/marketdata/runtime.json'), Path('/etc/chrony/marketdata.conf'),
            Path('/etc/systemd/system/marketdata-collector@.service'),
            Path('/etc/systemd/user/marketdata-clock-ready.service'),
            Path('/etc/marketdata/checkpoint-clock-retirement/clock-binding.json'),
            Path('/etc/marketdata/checkpoint-clock-retirement/settings.toml'),
            Path('/etc/marketdata/checkpoint-clock-retirement/recurring.json'),
            Path('/etc/marketdata/checkpoint-clock-retirement/manual.json'),
            Path('/etc/marketdata/clock-bootstrap.json'),
            Path('/etc/systemd/system/marketdata-clock-bootstrap.service')]
    for p in pins:
        files[str(p)] = node(p)
    masks = {}
    for manager, root in [('user', USER), ('system', Path('/etc/systemd/system'))]:
        for ext in ('service', 'timer'):
            p = root / f'marketdata-checkpoint-retirement.{ext}'
            masks[manager + ':' + p.name] = node(p)
    service_identity = command('system', 'show', 'chrony.service',
        'marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service',
        '--property=Id,MainPID,InvocationID,ActiveState')
    return {'captured_at': datetime.now(UTC).isoformat(), 'files': files,
        'effective_user_units': effective, 'retirement_masks': masks,
        'service_identity': service_identity,
        'user_inventory': command('user', 'list-unit-files', 'marketdata-*', '--no-legend', '--no-pager'),
        'system_inventory': command('system', 'list-unit-files', 'marketdata-*', '--no-legend', '--no-pager'),
        'user_jobs': command('user', 'list-jobs', '--no-legend', '--no-pager'),
        'system_jobs': command('system', 'list-jobs', '--no-legend', '--no-pager'),
        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'ledger': json.loads(Path('/var/lib/marketdata-clock/boot-state.json').read_text()),
        'operating_writes': False}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert args.output.resolve().parent == HERE and not args.output.exists()
    result = capture()
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'output': str(args.output), 'units': len(result['effective_user_units']),
                      'operating_writes': False}))
