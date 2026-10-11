"""Prepared rollback only: restore the old two commands, keep all new evidence."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import deploy_common as c
from activate_evidence import remove_own_dropins, run, verify_timers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    c.require(os.geteuid() == 1000, 'Run as yoonhc')
    c.require(not (HERE / 'rollback-result.json').exists(), 'Prior rollback requires review')
    for name in ('deploy_common.py','activate_evidence.py','rollback_evidence.py'):
        c.require((HERE / name).read_bytes() == (c.OPS / name).read_bytes(), 'Deployment tool changed')
    installed = json.loads((c.OPS / 'result.json').read_text())
    c.require(installed['passed'], 'Installation incomplete')
    activated = json.loads((HERE / 'activation-result.json').read_text())
    c.require(activated['passed'], 'Successful activation required')
    before = json.loads((c.OPS / 'host-before.json').read_text())
    c.verify_protected(before, installed=True)
    current = {n:c.show(n, run) for n in c.SERVICES}
    c.require(all(c.idle(v) for v in current.values()), 'Wait for running backup/archiver; never force stop')
    expected = activated['installed_dropins']
    allowed = {str(c.USER_UNITS / (n+'.d') / c.DROPIN) for n in c.SERVICES}
    c.require(set(expected) == allowed, 'Rollback paths differ')
    for path, digest in expected.items():
        c.require(not Path(path).is_symlink() and c.sha(Path(path).read_bytes()) == digest, 'Modified drop-in requires review')
    if not args.run:
        print(json.dumps({'rollback_valid': True, 'evidence_retained': True, 'automatic_retirement': 'masked'}))
        return
    result = {'passed': False, 'started_at': datetime.now(timezone.utc).isoformat(),
              'evidence_retained': True, 'data_retained': True, 'automatic_retirement': 'masked'}
    try:
        remove_own_dropins([Path(p) for p in sorted(expected)], expected)
        run(['/usr/bin/systemctl','--user','daemon-reload'])
        for name in c.SERVICES:
            c.verify_service(c.show(name, run), c.properties(before['user_units'][name]))
        verify_timers(before)
        c.verify_protected(before, installed=True)
        result['passed'] = True
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    c.write_new(HERE/'rollback-result.json', c.blob(result))
    print(json.dumps(result,indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
