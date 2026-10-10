"""Enable only the approved daily timer after verifying the first saved result."""
import importlib.util
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('continuation',HERE/'continue_activation.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
act=mod.act


def main():
    if (HERE/'timer-enable-result.json').exists():
        raise RuntimeError('Timer result already recorded')
    before=json.loads((HERE/'operating-before.json').read_text())
    act.check_protected(mod.unchanged_scope(before))
    record=json.loads((act.ROOT/'state/daily-scheduler-latest.json').read_text())
    with sqlite3.connect((act.ROOT/'state/catalog.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        saved=json.loads(db.execute('SELECT record FROM daily_runs WHERE id=?',(record['id'],)).fetchone()[0])
        counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['daily_tasks','daily_runs','daily_attempts','daily_reviews']}
    if any(record.get(k)!=saved.get(k) for k in saved):
        raise RuntimeError('DB and JSON report differ')
    if not record['runner_ok'] or record['processed'] or record['run_outcome']!='no_due_tasks':
        raise RuntimeError('First run requires review')
    if record['publication_observation']!={'state':'disabled','requests':0}:
        raise RuntimeError('Observer is not disabled')
    if counts!={'daily_tasks':176,'daily_runs':1,'daily_attempts':0,'daily_reviews':0}:
        raise RuntimeError('Unexpected first ledger state')
    state=act.show(act.SERVICE)
    if act.one(state,'Result')!='success' or act.one(state,'ExecMainStatus')!='0' or act.one(state,'MainPID')!='0':
        raise RuntimeError('First invocation not successfully closed')
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'first_invocation':state,'first_report':record,
            'ledger_counts':counts,'collector_before_enable':{}}
    for unit in ['chrony.service','marketdata-collector@depth.service','marketdata-collector@aggtrade.service']:
        p=subprocess.run(['/usr/bin/systemctl','show',unit,'--property=MainPID,InvocationID,ActiveState'],text=True,capture_output=True,check=True)
        props=act.properties(p.stdout)
        old=act.properties(before['system_units'][unit]['stdout'])
        if any(act.one(props,k)!=act.one(old,k) for k in ['MainPID','InvocationID','ActiveState']):
            raise RuntimeError('Protected service changed: '+unit)
        report['collector_before_enable'][unit]=props
    act.command(['/usr/bin/systemctl','--user','enable','--now',act.TIMER])
    timer=act.show(act.TIMER)
    report['timer_after']=timer
    report['passed']=act.one(timer,'ActiveState')=='active' and act.one(timer,'UnitFileState')=='enabled'
    (HERE/'timer-enable-result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':report['passed'],'first_run':record['id'],'counts':counts,
                      'timer':{k:timer[k] for k in ['ActiveState','UnitFileState','NextElapseUSecRealtime']}}))
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
