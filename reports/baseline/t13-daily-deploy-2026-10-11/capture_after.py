"""Read deployment outcome and current collector state; no service control."""
import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def query(args):
    p=subprocess.run(args,text=True,capture_output=True,timeout=30,check=True)
    rows={}
    for line in p.stdout.splitlines():
        key,value=line.split('=',1)
        rows.setdefault(key,[]).append(value)
    return rows


def main():
    before=json.loads((HERE/'operating-before.json').read_text())
    mutable=set(before['daily_files'])
    hashes={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in before['protected_hashes'] if p not in mutable}
    source={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in before['source_hashes']}
    props='Id,ActiveState,SubState,Result,MainPID,ControlPID,InvocationID,ExecMainStatus,ExecMainCode,ExecMainStartTimestampMonotonic,ExecMainExitTimestampMonotonic,CPUUsageNSec,MemoryPeak,IOReadBytes,IOWriteBytes,UnitFileState,ExecStart,ExecStartPre,NextElapseUSecRealtime,NextElapseUSecMonotonic,LastTriggerUSec,LastTriggerUSecMonotonic,TimersCalendar,TimersMonotonic,Triggers'
    units={u:query(['/usr/bin/systemctl','--user','show',u,'--property='+props]) for u in ['marketdata-daily.service','marketdata-daily.timer']}
    system={u:query(['/usr/bin/systemctl','show',u,'--property=MainPID,InvocationID,ActiveState']) for u in before['system_units']}
    unchanged={u:all(v[k]==[next(l.split('=',1)[1] for l in before['system_units'][u]['stdout'].splitlines() if l.startswith(k+'='))] for k in ['MainPID','InvocationID','ActiveState']) for u,v in system.items()}
    with sqlite3.connect((ROOT/'state/catalog.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        ledger={t:[json.loads(r[0]) for r in db.execute('SELECT record FROM '+t)] for t in ['daily_runs','daily_attempts','daily_cooldowns','daily_reviews']}
        tasks=[json.loads(r[0]) for r in db.execute('SELECT record FROM daily_tasks')]
        streams={}
        for f in ['depth-latest.json','aggtrade-latest.json']:
            current=json.loads((ROOT/'state'/f).read_text())
            row=db.execute('SELECT report FROM event_collections WHERE id=?',(current['id'],)).fetchone()
            if row:
                current=json.loads(row[0])
            streams[f]=current
    latest=json.loads((ROOT/'state/daily-scheduler-latest.json').read_text())
    counts={}
    for t in tasks:
        counts[t['state']]=counts.get(t['state'],0)+1
    result={'as_of':datetime.now(timezone.utc).isoformat(),'daily_units':units,'protected_services':system,
            'protected_services_same_invocations':unchanged,'source_unchanged':source==before['source_hashes'],
            'protected_hashes_unchanged':all(h==before['protected_hashes'][p] for p,h in hashes.items()),
            'retirement_masks':{p:os.readlink(p) for p in before['retirement_masks']},
            'daily_runs':ledger['daily_runs'],'ledger_counts':{k:len(v) for k,v in ledger.items()},
            'task_count':len(tasks),'task_state_counts':counts,'latest_report':latest,'current_collections':streams}
    (HERE/'operating-after.json').write_text(json.dumps(result,indent=2)+'\n')
    (HERE/'ledger-sample.json').write_text(json.dumps({'tasks':tasks,**ledger},indent=2)+'\n')
    journal=subprocess.run(['/usr/bin/journalctl','--user','-u','marketdata-daily.service','-u','marketdata-daily.timer','--since','2026-10-11 00:35:00','--no-pager','-o','short-iso'],text=True,capture_output=True,check=True).stdout
    (HERE/'daily-journal.txt').write_text(journal)
    print(json.dumps({'as_of':result['as_of'],'runs':len(ledger['daily_runs']),'attempts':len(ledger['daily_attempts']),
                      'states':counts,'latest_processed':len(latest['processed']),'runner_ok':latest['runner_ok'],
                      'timer':units['marketdata-daily.timer'],'services_unchanged':unchanged,
                      'source_unchanged':result['source_unchanged'],'protected_files_unchanged':result['protected_hashes_unchanged']},indent=2))


if __name__=='__main__':
    main()
