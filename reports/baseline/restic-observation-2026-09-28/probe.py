import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
sys.path.insert(0, str(ROOT))
from scripts.restic_schedule import service_context

if len(sys.argv) > 1:
    name, target = sys.argv[1:]
    result = service_context(name + '.service', name + '.timer')
    path = Path(target) / (os.environ['INVOCATION_ID'] + '.json')
    path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
else:
    name = 'marketdata-restic-provenance-probe-' + uuid.uuid4().hex
    target = ROOT / 'reports/restic-observation-2026-09-28' / name
    target.mkdir()
    command = [
        'systemd-run', '--user', '--unit=' + name,
        '--on-active=1s', '--on-unit-active=5s', '--timer-property=AccuracySec=100ms',
        '--property=Type=oneshot', '--property=TimeoutStartSec=30s',
        str(ROOT / '.venv/bin/python'), __file__, name, str(target),
    ]
    try:
        subprocess.run(command, check=True)
        deadline = time.monotonic() + 25
        while len(list(target.glob('*.json'))) < 2:
            if time.monotonic() > deadline:
                raise TimeoutError('Two timer activations were not observed')
            time.sleep(.1)
        # Keep the timer loaded so any stale unit activation details survive into this manual start.
        subprocess.run(['systemctl', '--user', 'start', name + '.service'], check=True)
    finally:
        subprocess.run(['systemctl', '--user', 'stop', name + '.timer'], check=True)
    rows = [json.loads(p.read_text()) for p in target.glob('*.json')]
    scheduled = [r for r in rows if r['scheduled']]
    manual = [r for r in rows if r['classification'] == 'manual_service_or_unknown']
    assert len(rows) == 3 and len(scheduled) == 2 and len(manual) == 1, rows
    assert scheduled[0]['start_job']['id'] != scheduled[1]['start_job']['id']
    assert scheduled[0]['trigger_at'] != scheduled[1]['trigger_at']
    assert manual[0]['environment']['TRIGGER_UNIT'] == name + '.timer', 'stale env not reproduced'
    result = {
        'ok': True, 'evidence_dir': str(target), 'unit': name,
        'scheduled': len(scheduled), 'manual': len(manual),
        'stale_environment_reproduced': any(not r['environment_matches_job'] for r in scheduled),
        'test_timer_stopped': True, 'production_data_accessed': False,
    }
    (target / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
