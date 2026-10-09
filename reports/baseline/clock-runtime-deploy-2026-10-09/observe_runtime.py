"""Read-only operating observation; writes evidence only beside this script."""

import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PLAN_ID = '22706e571f3a46a8745803b18bca2efacc3318c3e05b2309df6f3c7c37c621e6'
OPS = Path('/var/lib/marketdata-clock/runtime-approvals') / PLAN_ID
RELEASE = Path('/opt/marketdata/releases/clock-evidence-policy-20261009-65296619a374c325')
PROPERTIES = 'Id,ActiveState,SubState,MainPID,ControlPID,InvocationID,Result,ExecMainCode,ExecMainStatus,ExecMainStartTimestamp,FragmentPath,DropInPaths,ExecStart,ExecStartPre,User,Group,RuntimeMaxUSec,Restart,RestartUSec,KillMode,KillSignal,TimeoutStopUSec'


def read_json(path):
    return json.loads(Path(path).read_text())


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def command(argv):
    try:
        p = subprocess.run([str(x) for x in argv], capture_output=True, text=True,
                           timeout=25, env={**os.environ, 'SYSTEMD_COLORS': '0'})
        return {'argv': [str(x) for x in argv], 'code': p.returncode,
                'stdout': p.stdout, 'stderr': p.stderr}
    except subprocess.TimeoutExpired as exc:
        return {'argv': [str(x) for x in argv], 'error': str(exc)}


def process(pid):
    if not pid:
        return None
    try:
        p = Path('/proc') / str(pid)
        return {'pid': pid, 'argv': (p / 'cmdline').read_bytes().decode().split('\0'),
                'status': [x for x in (p / 'status').read_text().splitlines()
                           if x.startswith(('Name:', 'State:', 'PPid:', 'Uid:', 'Gid:'))]}
    except OSError as exc:
        return {'pid': pid, 'error': str(exc)}


def file_proof(relative):
    p = ROOT / relative
    try:
        with p.open('rb') as f:
            a = os.fstat(f.fileno())
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
            b = os.fstat(f.fileno())
        return {'path': relative, 'size': b.st_size, 'sha256': digest,
                'stable_during_read': (a.st_ino, a.st_size, a.st_mtime_ns) ==
                                     (b.st_ino, b.st_size, b.st_mtime_ns)}
    except OSError as exc:
        return {'path': relative, 'error': str(exc), 'note': 'May be packed; no missing-data conclusion'}


def main():
    now = datetime.now(timezone.utc)
    result = {'observed_at': now.isoformat(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    plan = read_json(HERE.parent / 'clock-runtime-transition-2026-10-09/operating-plan-final.json')
    result['plan_id'] = plan['plan_id']
    result['files'] = {}
    for role, item in plan['files'].items():
        p = Path(item['path'])
        digest = sha(p.read_bytes())
        result['files'][role] = {'path': str(p), 'sha256': digest, 'uid': p.stat().st_uid,
                                'mode': oct(p.stat().st_mode & 0o777),
                                'before': digest == item['before_sha256'],
                                'after': digest == item['after_sha256']}
    ledger = read_json('/var/lib/marketdata-clock/boot-state.json')
    result['ledger'] = ledger
    result['original_boot_and_step_evidence_preserved'] = all(
        ledger.get(k) == v for k, v in plan['ledger_before'].items()
        if k not in ('profile_sha256', 'configuration_history'))
    before = read_json(OPS / 'before.json')
    changed = {f['path'] for f in plan['files'].values()} | {'/var/lib/marketdata-clock/boot-state.json'}
    result['non_target_configuration_hashes'] = {p: sha(Path(p).read_bytes()) for p in before['files'] if p not in changed}
    result['non_target_configuration_unchanged'] = all(before['files'][p] == h for p, h in result['non_target_configuration_hashes'].items())
    result['collector_process_inventory'] = []
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():
            continue
        try:
            argv = (directory / 'cmdline').read_bytes().decode().split('\0')
            supervisor = any(x.endswith(('/integrations/runtime/capture_runtime.py', '/integrations/runtime/marketdata_runtime.py')) for x in argv) and '--stream' in argv
            child = 'from marketdata.cli import main; raise SystemExit(main())' in argv and any(x in ('depth-stream', 'aggtrade-stream') for x in argv)
            if supervisor or child:
                result['collector_process_inventory'].append({'role': 'supervisor' if supervisor else 'child', **process(int(directory.name))})
        except (OSError, UnicodeError):
            continue
    result['chrony'] = command(['/usr/bin/systemctl', 'show', 'chrony.service', '--all', '--property=' + PROPERTIES])
    result['collectors'] = command(['/usr/bin/systemctl', 'show',
        'marketdata-collector@depth.service', 'marketdata-collector@aggtrade.service', '--all', '--property=' + PROPERTIES])
    result['timers'] = command(['/usr/bin/systemctl', '--user', 'list-timers', '--all', '--no-pager'])
    result['masks'] = {}
    for name in ('marketdata-depth.service', 'marketdata-aggtrade.service',
                 'marketdata-checkpoint-retirement.service', 'marketdata-checkpoint-retirement.timer'):
        p = Path('/home/yoonhc/.config/systemd/user') / name
        result['masks'][name] = os.readlink(p) if p.is_symlink() else None
    if (OPS / 'result.json').is_file():
        deployed = read_json(OPS / 'result.json')
        result['deployment_result'] = {k: v for k, v in deployed.items() if k not in ('events', 'after', 'traceback')}
    result['recovery_results'] = [read_json(p) for p in OPS.glob('reader-recovery-*/result.json')]
    result['streams'] = {}
    for stream in ('depth', 'aggtrade'):
        latest = ROOT / 'state/runtime-clock/v2' / stream / 'latest.json'
        if latest.is_file():
            row = read_json(latest)
            result['streams'][stream] = {'latest': row,
                'supervisor': process(row.get('supervisor_pid')), 'child': process(row.get('child_pid')),
                'status': command([RELEASE / '.venv/bin/python', '-B', '-I',
                    RELEASE / 'integrations/runtime/capture_runtime.py', '--profile', '/etc/marketdata/runtime.json',
                    '--bootstrap-profile', '/etc/marketdata/clock-bootstrap.json', '--stream', stream, '--status'])}
    with sqlite3.connect((ROOT / 'state/catalog.sqlite').as_uri() + '?mode=ro', uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT * FROM event_collections ORDER BY rowid DESC LIMIT 50').fetchall()
        collections = []
        for row in rows:
            report = json.loads(row['report'])
            link = report.get('runtime_clock')
            if not link:
                continue
            segments = [dict(s) for s in db.execute('SELECT * FROM event_segments WHERE collection_id=? ORDER BY rowid DESC', (row['id'],))]
            item = {k: row[k] for k in row.keys() if k != 'report'}
            item.update(report=report, segments=len(segments), stored_rows=sum(s['row_count'] for s in segments))
            if segments:
                s = segments[0]
                artifact = db.execute('SELECT * FROM artifacts WHERE id=?', (s['source_id'],)).fetchone()
                item['last_segment'] = s
                item['last_raw_artifact'] = dict(artifact) if artifact else None
            collections.append(item)
        result['collections'] = collections
        result['clock_artifacts'] = [dict(r) for r in db.execute(
            'SELECT a.*,f.url,f.metadata FROM artifacts a JOIN fetches f ON f.artifact_id=a.id '
            'WHERE f.url>=? AND f.url<? ORDER BY f.id DESC LIMIT 30', ('runtime-clock://', 'runtime-clock://\uffff'))]
    for item in result['collections'][:2]:
        if item.get('last_segment'):
            item['standard_file_proof'] = file_proof(item['last_segment']['path'])
        if item.get('last_raw_artifact'):
            item['raw_file_proof'] = file_proof(item['last_raw_artifact']['path'])
    target = HERE / ('operating-observation-' + now.strftime('%Y%m%dT%H%M%SZ') + '.json')
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'report': str(target), 'files': result['files'], 'streams': list(result['streams']),
        'collections': [{'id': x['id'], 'state': x['state'], 'stored_rows': x['stored_rows']} for x in result['collections']],
        'clock_artifacts': len(result['clock_artifacts']), 'recovery_results': result['recovery_results']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
