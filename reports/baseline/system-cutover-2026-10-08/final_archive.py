"""Manually archive the final completed checkpoint via the unchanged archive runner."""
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import time

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def main():
    backup = json.loads((HERE / 'final-backup.json').read_text())
    assert backup['ok'] and (Path(backup['path']) / 'backup.json').is_file()
    assert not (HERE / 'final-archive-receipt.json').exists()
    args = [str(ROOT / '.venv/bin/python'), '-B', '-m', 'scripts.restic_schedule',
            '--profile', '/home/yoonhc/.config/marketdata/restic-archive.toml', 'run']
    started = datetime.now().astimezone().isoformat()
    write(HERE / 'final-archive-plan.json', {'started_at':started,'command':args,
          'backup_id':backup['backup_id'],'scheduled':False,'count_as_new_scheduled_date':False})
    env = {k:v for k,v in os.environ.items() if k not in ('INVOCATION_ID','TRIGGER_UNIT',
           'TRIGGER_TIMER_REALTIME_USEC','TRIGGER_TIMER_MONOTONIC_USEC')}
    began = time.monotonic()
    print(json.dumps({'phase':'final_archive_started','backup_id':backup['backup_id'],'at':started}),flush=True)
    with (HERE / 'final-archive.stdout').open('xb') as out, (HERE / 'final-archive.stderr').open('xb') as err:
        p = subprocess.run(args, cwd=ROOT, env=env, stdout=out, stderr=err)
    receipt = {'started_at':started,'finished_at':datetime.now().astimezone().isoformat(),
               'wall_seconds':time.monotonic()-began,'returncode':p.returncode,'command':args}
    write(HERE / 'final-archive-receipt.json', receipt)
    assert p.returncode == 0, receipt
    rows = [json.loads(line) for line in (HERE / 'final-archive.stdout').read_text().splitlines() if line.strip()]
    result = rows[-1]
    assert result['ok'] and not result['context']['scheduled'] and not result['counted_dates']
    assert result['archive']['result']['processed'] == 1
    write(HERE / 'final-archive.json', result)
    print(json.dumps({'phase':'final_archive_complete','at':receipt['finished_at'],
                      'run_id':result['archive']['run_id'],'processed':1,'scheduled':False}),flush=True)


if __name__ == '__main__':
    main()
