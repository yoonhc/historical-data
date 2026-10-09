"""Read-only incident analysis of saved runtime clock records and this boot's journal."""

import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
OUT = Path(__file__).resolve().parent
KST = ZoneInfo('Asia/Seoul')


def time_text(seconds):
    return datetime.fromtimestamp(seconds, KST).isoformat()


def stamp(row):
    return row['sample']['wall_ns']/1e9


def write(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def journal(name, args):
    command = ['journalctl', '-b', '--no-pager', '-o', 'json', *args]
    proc = subprocess.run(command, text=True, capture_output=True, timeout=60)
    if proc.returncode:
        raise RuntimeError(proc.stderr)
    rows = [json.loads(line) for line in proc.stdout.splitlines()]
    selected = []
    for r in rows:
        selected.append({'at_kst':time_text(int(r['__REALTIME_TIMESTAMP'])/1e6),
                         'monotonic_us':int(r['__MONOTONIC_TIMESTAMP']),
                         'boot_id':r.get('_BOOT_ID'), 'unit':r.get('UNIT') or r.get('USER_UNIT')
                             or r.get('_SYSTEMD_USER_UNIT') or r.get('_SYSTEMD_UNIT'),
                         'pid':r.get('_PID'), 'message':r.get('MESSAGE')})
    write(name, {'command':command, 'stderr':proc.stderr, 'records':selected})
    return selected


def main():
    OUT.mkdir(exist_ok=True)
    manifests, sessions, probes, chosen = [], [], {}, []
    overall_reasons = Counter()
    sample_maxima = {'abs_wall_minus_boottime_seconds':0, 'abs_boottime_minus_monotonic_seconds':0}
    for stream in ('aggtrade','depth'):
        for path in sorted((ROOT/'state/runtime-clock'/stream).glob('*.jsonl')):
            blob = path.read_bytes()
            lines = blob.splitlines(keepends=True)
            if lines and not lines[-1].endswith(b'\n'):
                lines.pop()
            rows = [json.loads(line) for line in lines]
            manifests.append({'path':str(path.relative_to(ROOT)), 'read_bytes':len(blob),
                              'read_sha256':hashlib.sha256(blob).hexdigest(), 'complete_records':len(rows),
                              'last_state':rows[-1].get('state') if rows else None,
                              'note':'Closed logs immutable in normal operation; active log hash identifies only this read prefix.'})
            if not rows:
                continue
            running = [r for r in rows if r['state']=='running']
            counts = Counter(x for r in running for x in r.get('reasons',[]))
            overall_reasons.update(counts)
            changes, previous = [], None
            for r in rows:
                c = r.get('clock')
                if c and c.get('sample') and c.get('tracking'):
                    key = (r['boot_id'],c['sample']['monotonic_ns'])
                    probes[key] = {'at':c['sample']['wall_ns']/1e9,'clock':c,
                                   'stream':stream,'run_id':r['run_id']}
                for key, field in [('abs_wall_minus_boottime_seconds','wall_minus_boottime_seconds'),
                                   ('abs_boottime_minus_monotonic_seconds','boottime_minus_monotonic_seconds')]:
                    sample_maxima[key] = max(sample_maxima[key],abs(r.get(field,0)))
                state = (r['state'],tuple(r.get('reasons',[])),
                         tuple(c.get('reasons',[])) if c else None,
                         c.get('ready') if c else None,r.get('clock_usable'))
                if state != previous:
                    previous = state
                    changes.append({'at_kst':time_text(stamp(r)),'state':r['state'],
                                    'clock_usable':r.get('clock_usable'),'monitor_reasons':r.get('reasons'),
                                    'provider_ready':c.get('ready') if c else None,
                                    'provider_reasons':c.get('reasons') if c else None})
                    chosen.append({'input_path':str(path.relative_to(ROOT)),'record':r})
            sessions.append({'stream':stream,'run_id':rows[0]['run_id'],
                             'supervisor_pid':rows[0]['supervisor_pid'],
                             'first_at_kst':time_text(stamp(rows[0])),
                             'last_at_kst':time_text(stamp(rows[-1])),
                             'first_running_at_kst':time_text(stamp(running[0])) if running else None,
                             'last_running_at_kst':time_text(stamp(running[-1])) if running else None,
                             'last_state':rows[-1]['state'],
                             'session_clock_clean':rows[-1].get('session_clock_clean'),
                             'reason_record_counts':dict(counts), 'transitions':changes,
                             'running_records':len(running)})
    observations = sorted(probes.values(),key=lambda r:r['at'])
    periods, updates, previous = [], [], None
    previous_key, current = None, None
    for o in observations:
        c,t = o['clock'],o['clock']['tracking']
        state = (c['ready'],tuple(c['reasons']))
        if state != previous_key:
            if current:
                current['next_state_first_observed_kst'] = time_text(o['at'])
                periods.append(current)
            current = {'first_observed_kst':time_text(o['at']),'ready':state[0],
                       'reasons':list(state[1]),'max_age_seconds':0,'max_correction_seconds':0,
                       'max_error_bound_seconds':0,'reference_addresses':[]}
            previous_key = state
        current['last_observed_kst'] = time_text(o['at'])
        current['max_age_seconds'] = max(current['max_age_seconds'],c['reference_age_seconds'])
        current['max_correction_seconds'] = max(current['max_correction_seconds'],abs(t['correction']))
        current['max_error_bound_seconds'] = max(current['max_error_bound_seconds'],c['conditional_error_bound_seconds'])
        if t['reference_address'] not in current['reference_addresses']:
            current['reference_addresses'].append(t['reference_address'])
        ref = (t['reference_address'],t['reference_time'])
        if ref != previous:
            updates.append({'first_observed_kst':time_text(o['at']),
                            'reference_at_kst':time_text(t['reference_time']),
                            'reference_address':t['reference_address'],
                            'interval_from_previous_reference_seconds':t['reference_time']-previous[1] if previous else None,
                            'reported_update_interval_seconds':float(t['raw_csv'].split(',')[12]),
                            'correction_seconds':t['correction'],'leap':t['leap'],
                            'clock_sample':c})
            previous = ref
    if current:
        periods.append(current)
    # Two collectors sample independently; close boundary samples may interleave.
    result = {'as_of_utc':datetime.now(timezone.utc).isoformat(), 'files_read':len(manifests),
              'clock_probe_observations':len(observations), 'monitor_reason_records':dict(overall_reasons),
              'sample_maxima':sample_maxima,'provider_state_periods':periods,
              'reference_update_count':len(updates),
              'sessions':sorted(sessions,key=lambda r:r['first_at_kst'])}
    write('runtime-analysis.json',result)
    write('input-manifest.json',manifests)
    write('reference-updates.json',updates)
    write('runtime-transition-evidence.json',chosen)
    journal('chrony-journal.json',['-u','chrony.service'])
    journal('collector-gate-journal.json',['-u','marketdata-collector@aggtrade.service',
        '-u','marketdata-collector@depth.service','--grep=ready|timed|Stopped|Started|Main process|Failed|Deactivated'])
    waits = journal('job-wait-journal.json',['--user','-u','marketdata-clock-ready.service','--grep=phase|Failed|Stopped'])
    long_waits=[]
    for r in waits:
        try:
            payload=json.loads(r['message'])
        except (json.JSONDecodeError,TypeError):
            continue
        if payload.get('wait_seconds',0)>1 or payload.get('ready') is False:
            long_waits.append({**r,'parsed':payload})
    write('job-wait-events.json',long_waits)
    print(json.dumps({k:v for k,v in result.items() if k!='sessions'},ensure_ascii=False,indent=2))
    print('long_wait_events',len(long_waits))


if __name__=='__main__':
    main()
