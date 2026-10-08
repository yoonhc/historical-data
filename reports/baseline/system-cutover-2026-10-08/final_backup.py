"""One final checkpoint through the existing backup CLI after the approved collection hold."""
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
sys.path.insert(0, str(ROOT))
from scripts.archive_backups import Profile


def record(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def main():
    before = json.loads((HERE / 'before.json').read_text())
    assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == before['boot_id']
    units = sorted(before['units'])
    status = subprocess.check_output(['/usr/bin/systemctl', '--user', 'show', *units,
                                      '-p', 'Id', '-p', 'LoadState', '-p', 'ActiveState', '-p', 'MainPID'], text=True)
    for block in status.strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        assert values['LoadState'] == 'masked' and values['ActiveState'] in ('inactive', 'failed'), values
        assert values.get('MainPID', '0') == '0', values
    clock = subprocess.check_output(['/usr/bin/timedatectl', 'show', '-p', 'NTPSynchronized', '-p', 'LocalRTC'], text=True)
    assert 'NTPSynchronized=yes' in clock and 'LocalRTC=yes' in clock
    final_streams = {}
    for name in ('depth', 'aggtrade'):
        source = ROOT / 'state' / (name + '-latest.json')
        value = json.loads(source.read_bytes())
        assert value['state'] == 'interrupted' and value['messages'] == value['persisted_messages'], value
        assert value['error'].startswith('KeyboardInterrupt')
        (HERE / ('final-' + source.name)).write_bytes(source.read_bytes())
        final_streams[name] = {k:value.get(k) for k in ('id','state','messages','persisted_messages','last_received_at','updated_at','last_update_id','last_id','unrepaired_gaps','unreconstructed_messages')}
        with (ROOT / 'state' / (name + '-stream.lock')).open('r+') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    profile = Profile.load(Path('/home/yoonhc/.config/marketdata/restic-archive.toml'))
    profile.guard()
    previous = json.loads((ROOT / 'reports/baseline/system-cutover-backup-followup-2026-10-08/backup-readiness.json').read_text())['checkpoint']
    destination = profile.backup_dir / datetime.now().astimezone().strftime('%Y-%m-%d') / uuid.uuid4().hex
    assert not destination.exists()
    args = [str(ROOT / '.venv/bin/marketdata'), '--root', str(ROOT), 'backup',
            '--output', str(destination), '--reuse-from', previous['path'], '--min-free-gib', '10']
    plan = {'created_at': datetime.now().astimezone().isoformat(), 'command': args,
            'destination': str(destination), 'reuse_backup_id': previous['backup_id'],
            'service_state': status, 'clock': clock, 'final_streams': final_streams,
            'method': 'existing backup CLI with complete manifest/file/pack validation; no repeated source-wide maintenance'}
    record(HERE / 'final-backup-plan.json', plan)
    began = time.monotonic()
    started = datetime.now().astimezone().isoformat()
    print(json.dumps({'phase':'final_backup_started','at':started,'destination':str(destination),'final_streams':final_streams}), flush=True)
    with (HERE / 'final-backup.stdout').open('xb') as out, (HERE / 'final-backup.stderr').open('xb') as err:
        p = subprocess.run(args, cwd=ROOT, stdout=out, stderr=err)
    receipt = {'started_at':started,'finished_at':datetime.now().astimezone().isoformat(),
               'wall_seconds':time.monotonic()-began,'returncode':p.returncode,'command':args}
    record(HERE / 'final-backup-receipt.json', receipt)
    assert p.returncode == 0, receipt
    result = json.loads((HERE / 'final-backup.stdout').read_text())
    assert result['ok'] and result['path'] == str(destination)
    record(HERE / 'final-backup.json', result)
    print(json.dumps({'phase':'final_backup_complete','result':result},ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
