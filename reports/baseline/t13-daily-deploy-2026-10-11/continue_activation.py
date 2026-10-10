"""Continue the recorded timer-held phase, preserving all unchanged guards."""
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('activation', HERE/'activate_daily.py')
act = importlib.util.module_from_spec(spec)
spec.loader.exec_module(act)


def unchanged_scope(before):
    # These exact two base paths are the approved transaction's mutable files.
    # All drop-ins, clock/ledger, other jobs and original source files stay pinned.
    return {**before, 'protected_hashes': {
        p:h for p,h in before['protected_hashes'].items()
        if p not in {str(act.UNITS/act.SERVICE), str(act.UNITS/act.TIMER)}}}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=1000:
        raise RuntimeError('Run as yoonhc')
    if (HERE/'continuation-result.json').exists():
        raise RuntimeError('Continuation already recorded; review first')
    old=json.loads((HERE/'activation-result.json').read_text())
    if old['passed'] or old['phase']!='daily_timer_held' or 'marketdata-daily.timer' not in old['error']:
        raise RuntimeError('Different interrupted phase')
    before=json.loads((HERE/'operating-before.json').read_text())
    act.check_protected(unchanged_scope(before))
    for path,expected in before['source_hashes'].items():
        if act.sha(act.ROOT/path)!=expected:
            raise RuntimeError('Operating source changed: '+path)
    previous=before['daily_files'][str(act.UNITS/act.SERVICE)]
    if act.sha(act.UNITS/act.SERVICE)!=previous['sha256'] or os.readlink(act.UNITS/act.SERVICE)!=previous['symlink']:
        raise RuntimeError('Unexpected service at interrupted phase')
    if (act.UNITS/act.TIMER).exists() or (act.UNITS/act.TIMER).is_symlink():
        raise RuntimeError('Timer path no longer matches recorded disable')
    if (act.UNITS/'timers.target.wants'/act.TIMER).exists():
        raise RuntimeError('Timer enable link unexpectedly restored')
    installed=json.loads((act.OPS/'finish-result.json').read_text())
    if not installed['passed'] or installed['release_id']!=act.RELEASE_ID:
        raise RuntimeError('Installation not complete')
    manifest=json.loads((act.CANDIDATE/'manifest.json').read_text())
    for path,digest in manifest['source_sha256'].items():
        if act.sha(act.RELEASE/path)!=digest:
            raise RuntimeError('Installed source changed')
    for name,source in [('daily-scheduler.toml','configs/daily-scheduler.toml'),
                         ('daily-publication-probe.toml','configs/daily-publication-probe.example.toml')]:
        if act.sha(Path('/etc/marketdata')/name)!=manifest['source_sha256'][source]:
            raise RuntimeError('Installed configuration changed')
    service=(act.RELEASE/'integrations/daily_scheduler/marketdata-daily.service.in').read_text().replace('@RELEASE@',act.RELEASE_ID)
    timer=(act.RELEASE/'integrations/daily_scheduler/marketdata-daily.timer').read_text()
    expected=next(x.removeprefix('ExecStart=') for x in service.splitlines() if x.startswith('ExecStart='))
    if not args.run:
        print(json.dumps({'continuation_valid':True,'scope':'exact two daily base paths'}))
        return
    result={'release_id':act.RELEASE_ID,'passed':False,'original_result_preserved':True,
            'continuation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'started_at':datetime.now(timezone.utc).isoformat(),'phase':'held_phase_checks'}
    try:
        current=act.show(act.SERVICE)
        if act.one(current,'ActiveState') not in ('inactive','failed') or act.one(current,'MainPID')!='0' or act.one(current,'ControlPID')!='0':
            raise RuntimeError('Daily not idle; no forced stop')
        act.verify_service(current,before,act.commands(act.properties(before['user_units'][act.SERVICE]['stdout']),'ExecStart')[0])
        gate=json.loads(act.command(before['strict_gate']['argv']))
        if not gate.get('ready'):
            raise RuntimeError('Current clock not ready')
        result['phase']='switch_daily_units'
        act.replace_link_with_file(act.UNITS/act.SERVICE,service.encode())
        act.replace_link_with_file(act.UNITS/act.TIMER,timer.encode())
        act.command(['/usr/bin/systemctl','--user','daemon-reload'])
        effective=act.show(act.SERVICE)
        act.verify_service(effective,before,expected)
        timer_effective=act.show(act.TIMER)
        for key,value in {'Unit':act.SERVICE,'Persistent':'yes','RandomizedDelayUSec':'30s','AccuracyUSec':'1s'}.items():
            if act.one(timer_effective,key)!=value:
                raise RuntimeError('Effective timer differs: '+key)
        if '*:00/5:00' not in act.one(timer_effective,'TimersCalendar'):
            raise RuntimeError('Calendar differs')
        act.check_protected(unchanged_scope(before))
        (HERE/'effective-units.json').write_text(json.dumps({'service':effective,'timer':timer_effective},indent=2)+'\n')
        result['phase']='first_run_requested_timer_held'
        act.command(['/usr/bin/systemctl','--user','start','--no-block',act.SERVICE])
        result['passed']=True
    except Exception as exc:
        result['error']=f'{type(exc).__name__}: {exc}'
    result['finished_at']=datetime.now(timezone.utc).isoformat()
    (HERE/'continuation-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)
    if not result['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
