"""Bounded read-only T02 observation; run with the deployed release's Python.

No Store initialization, exchange requests, service control, backup or clock writes.
The first 24 hours are fixed, while terminal collection reports can extend beyond
the upper boundary. Output files are exclusive-create; raw inputs stay in reports/.
"""

from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path('/home/yoonhc/projects/algoTrade/historical_data')
OUT = Path(__file__).resolve().parent
RAW = ROOT / 'reports/t02-first-24h-2026-10-10'
START = '2026-10-08T15:11:18+00:00'
END = '2026-10-09T15:11:18+00:00'
BOOT = 'eb163bf2-6642-4336-b4d6-6cd58771b13d'
KST = ZoneInfo('Asia/Seoul')
LOW, HIGH = [int(datetime.fromisoformat(x).timestamp()*1e9) for x in (START, END)]


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def dump(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, default=str)+'\n').encode()


def save(name, value):
    with (OUT/name).open('xb') as f:
        f.write(dump(value))


def retain(name, blob):
    RAW.mkdir(exist_ok=True)
    path = RAW/name
    with path.open('xb') as f:
        f.write(blob)
    return {'path':str(path.relative_to(ROOT)), 'bytes':len(blob), 'sha256':sha(blob)}


def kst(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value).astimezone(KST).isoformat()
    return datetime.fromtimestamp(value/1e9, KST).isoformat()


def connect():
    db = sqlite3.connect((ROOT/'state/catalog.sqlite').as_uri()+'?mode=ro',
                         uri=True, isolation_level=None, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def data():
    import pyarrow as pa
    import pyarrow.parquet as pq
    from marketdata.fileio import read_file
    db = connect()
    # Only snapshot metadata briefly. File reads use current pack metadata and the
    # existing shared layout lock independently; no long pinned SQLite reader.
    db.execute('BEGIN')
    items = [dict(r) for r in db.execute(
        'SELECT * FROM event_collections WHERE started_at>=? AND started_at<? ORDER BY started_at',
        (START, END))]
    for item in items:
        item['report'] = json.loads(item['report'])
        item['segments'] = [dict(s) for s in db.execute(
            'SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id', (item['id'],))]
    db.execute('COMMIT')
    source = retain('catalog-cohort.json.gz', gzip.compress(dump(items), mtime=0))
    fields = ('messages','persisted_messages','snapshots','complete','sync_losses','coverage_resets',
              'coverage_top_shortfalls','synchronized','unreconstructed_messages','connection_errors',
              'connections','last_id','last_update_id','last_received_at','previous_persisted_id',
              'detected_gaps','repaired_gaps','unrepaired_gaps','failure_notice','runtime_clock','errors',
              'rows','first_id','first_requested_id','last_requested_id','gaps')
    summaries, proofs, issues = [], [], []
    for item in items:
        r, segments = item['report'], item['segments']
        stream = 'depth' if item['dataset']=='book_depth' else 'aggtrade'
        total = sum(s['row_count'] for s in segments)
        expected = (r['persisted_messages']+r.get('snapshots',0)
                    if item['kind']=='websocket' else r['rows'])
        summary = {k:v for k,v in item.items() if k not in ('report','segments')}
        summary.update({k:r[k] for k in fields if k in r})
        summary.update(segments=len(segments), stored_rows=total, expected_rows=expected,
                       extends_past_window=item['updated_at']>=END,
                       report_sha256=sha(dump(r)))
        summary['segment_metadata_contiguous'] = all(
            b['first_id']==a['last_id']+1 for a,b in zip(segments,segments[1:]))
        summary['segment_sizes_match_id_ranges'] = all(
            s['row_count']==s['last_id']-s['first_id']+1 for s in segments)
        if total!=expected or not summary['segment_metadata_contiguous'] or not summary['segment_sizes_match_id_ranges']:
            issues.append({'collection':item['id'],'kind':'catalog/report or ID metadata mismatch'})
        if item['kind']=='websocket' and r['messages']!=r['persisted_messages']:
            issues.append({'collection':item['id'],'kind':'received/persisted mismatch'})
        epochs = r.get('epochs', [])
        summary['epochs'] = [{k:e.get(k) for k in
            ('epoch','started_at','ended_at','end_reason','error','connection','bootstrap_reason','first_sequence','last_sequence')}
            for e in epochs]
        # First/last files of every cohort, plus each recorded failure file.
        selected = segments[:1]+segments[-1:]
        notice = r.get('failure_notice')
        if notice:
            selected += [s for s in segments if s['first_id']<=notice['sequence']<=s['last_id']]
        selected = list({s['id']:s for s in selected}.values())
        sampled = []
        for s in selected:
            blob = read_file(ROOT, db, s['path'])
            identity = 'sequence' if stream=='depth' else 'agg_trade_id'
            cols = (['sequence','kind','epoch','received_at','final_update_id','previous_update_id']
                    if stream=='depth' else ['agg_trade_id','received_at'])
            rows = pq.ParquetFile(pa.BufferReader(blob)).read(columns=cols).to_pylist()
            ids = [row[identity] for row in rows]
            artifact = dict(db.execute('SELECT * FROM artifacts WHERE id=?',(s['source_id'],)).fetchone())
            raw = read_file(ROOT, db, artifact['path'])
            received = [row['received_at'] for row in rows if row['received_at'] is not None]
            verified = (sha(blob)==s['file_sha'] and sha(raw)==artifact['id']
                        and len(rows)==s['row_count'] and ids==list(range(s['first_id'],s['last_id']+1)))
            reversals = sum(b<a for a,b in zip(received,received[1:]))
            p = {'collection':item['id'],'segment':s,'artifact':artifact,'verified':verified,
                 'received_at_reversals_in_sample':reversals,'rows_checked':len(rows),
                 'first':rows[0],'last':rows[-1]}
            if notice:
                p['failure_neighborhood'] = [row for row in rows if abs(row[identity]-notice['sequence'])<=2]
            proofs.append(p)
            sampled.append(p)
            if not verified or reversals:
                issues.append({'collection':item['id'],'segment':s['id'],'kind':'sample verification'})
        if sampled:
            summary['first_stored_sample'] = sampled[0]['first']
            summary['last_stored_sample'] = next(p['last'] for p in sampled if p['segment']['id']==segments[-1]['id'])
        summaries.append(summary)
    db.close()
    boundaries = []
    rest_intervals = [(s['first_id'],s['last_id'],i['id']) for i in items if i['kind']=='rest' for s in i['segments']]
    for dataset in ('book_depth','agg_trades'):
        collections = [r for r in summaries if r['dataset']==dataset and r['kind']=='websocket']
        for previous, following in zip(collections,collections[1:]):
            end = previous['last_stored_sample']['received_at']
            start = following['first_stored_sample']['received_at']
            b = {'dataset':dataset,'previous':previous['id'],'following':following['id'],
                 'previous_last_received_at':end,'next_first_stored_received_at':start,
                 'receipt_gap_seconds':(start-end).total_seconds(),
                 'next_collection_started_at':following['started_at'],
                 'next_policy':'v2' if following.get('runtime_clock') else 'v1'}
            if dataset=='agg_trades':
                lo = previous['last_stored_sample']['agg_trade_id']+1
                hi = following['first_stored_sample']['agg_trade_id']-1
                cursor, covering = lo, []
                for first,last,cid in sorted(rest_intervals):
                    if first<=cursor<=last and cursor<=hi:
                        cursor = last+1
                        covering.append(cid)
                b.update(missing_first=lo,missing_last=hi,missing_count=max(0,hi-lo+1),
                         rest_catalog_coverage_complete=cursor>hi,rest_collections=sorted(set(covering)))
                if cursor<=hi:
                    issues.append({'kind':'uncovered aggTrade session gap','boundary':b})
            boundaries.append(b)
    groups = {}
    for dataset in ('book_depth','agg_trades'):
        rows = [r for r in summaries if r['dataset']==dataset and r['kind']=='websocket']
        groups[dataset] = {'collections_started_in_window':len(rows),
            'terminal_states_full_cohort':dict(Counter(r['state'] for r in rows)),
            'closed_before_cutoff':sum(not r['extends_past_window'] for r in rows),
            'full_cohort_rows_including_post_cutoff_tail':sum(r['stored_rows'] for r in rows),
            'connection_errors':sum(r.get('connection_errors',0) for r in rows),
            'sync_losses':sum(r.get('sync_losses',0) for r in rows)}
    result = {'window':[START,END],'count_contract':'Collections started in window; terminal totals include explicit post-cutoff tails. File content checks are selected samples, not a full replay.',
              'catalog_source':source,'groups':groups,'collections':summaries,'boundaries':boundaries,
              'canonical_samples':len(proofs),'canonical_rows_sampled':sum(p['rows_checked'] for p in proofs),
              'issues':issues}
    save('collections-and-storage.json',result)
    save('stored-samples.json',proofs)
    print(json.dumps({'groups':groups,'samples':len(proofs),'issues':issues},default=str))


def clocks():
    from marketdata.fileio import read_file
    inputs, observations, v1, v2 = [], {}, [], {}
    all_reason_counts = Counter()
    def observe_clock(row, stream, version):
        c = row.get('clock')
        if not c or not c.get('tracking') or not c.get('sample'):
            return
        at = c['sample']['wall_ns']
        if not LOW<=at<HIGH:
            return
        observations[(row['boot_id'],c['sample']['monotonic_ns'])] = {
            'stream':stream,'version':version,'clock':c}
    for stream in ('depth','aggtrade'):
        for path in sorted((ROOT/'state/runtime-clock'/stream).glob('*.jsonl')):
            blob = path.read_bytes()
            complete = blob[:blob.rfind(b'\n')+1]
            rows = [json.loads(line) for line in complete.splitlines()]
            chosen = [r for r in rows if r['boot_id']==BOOT and LOW<=r['sample']['wall_ns']<HIGH]
            if not chosen:
                continue
            inputs.append({'source':str(path.relative_to(ROOT)),'source_bytes':len(blob),'source_sha256':sha(blob),
                           'retained':retain('v1-'+stream+'-'+path.name+'.gz',gzip.compress(blob,mtime=0))})
            active = [r for r in chosen if r['state']=='running']
            counts = Counter(reason for r in active for reason in r.get('reasons',[]))
            all_reason_counts.update(counts)
            for row in chosen:
                observe_clock(row,stream,'v1')
            v1.append({'stream':stream,'run_id':chosen[0]['run_id'],'first_at':kst(chosen[0]['sample']['wall_ns']),
                       'last_at':kst(chosen[-1]['sample']['wall_ns']),'running_records':len(active),
                       'last_state':chosen[-1]['state'],'session_clock_clean':chosen[-1].get('session_clock_clean'),
                       'monitor_reason_samples':dict(counts),
                       'max_abs_wall_boottime_delta':max(abs(r.get('wall_minus_boottime_seconds',0)) for r in chosen),
                       'max_abs_boot_monotonic_delta':max(abs(r.get('boottime_minus_monotonic_seconds',0)) for r in chosen)})
        db = connect()
        run_id = {'depth':'9415ca491ca34ddfb75b1ec7cfeb4e8e','aggtrade':'87dddac11b184080b5b5d75a4f167db4'}[stream]
        prefix = 'runtime-clock://'+run_id+'/'
        artifacts = []
        for r in db.execute('SELECT a.*,f.url,f.metadata FROM artifacts a JOIN fetches f ON f.artifact_id=a.id WHERE f.url>=? AND f.url<?',(prefix,prefix+'\uffff')):
            a = dict(r); a['metadata']=json.loads(a['metadata'])
            if a['metadata']['first_sample']['wall_ns']<HIGH:
                artifacts.append(a)
        artifacts.sort(key=lambda a:a['metadata']['chunk'])
        rows, previous, index = [], None, 0
        for number,a in enumerate(artifacts):
            blob = read_file(ROOT,db,a['path'])
            decoded = gzip.decompress(blob)
            header,*records = [json.loads(line) for line in decoded.splitlines()]
            m = a['metadata']
            assert sha(blob)==a['id'] and sha(decoded)==m['uncompressed_sha256']
            assert number==m['chunk']==header['chunk'] and previous==m['previous']==header['previous']
            assert header['run_id']==run_id and len(records)==m['records']
            assert records[0]['sample']==m['first_sample'] and records[-1]['sample']==m['last_sample']
            for row in records:
                assert row['record']==index and row['boot_id']==BOOT and row['run_id']==run_id
                index+=1
                if LOW<=row['sample']['wall_ns']<HIGH:
                    rows.append(row)
                    observe_clock(row,stream,'v2')
            inputs.append({'artifact':a,'retained':retain('v2-'+stream+'-'+str(number)+'.jsonl.gz',blob)})
            previous=a['id']
        db.close()
        starts = [r for r in rows if r['state']=='starting_child']
        decisions = [r for r in rows if r.get('decision')]
        samples = [r['sample']['boottime_ns'] for r in rows]
        v2[stream] = {'run_id':run_id,'artifacts':len(artifacts),'records_inside_window':len(rows),
            'first_at':kst(rows[0]['sample']['wall_ns']),'last_at':kst(rows[-1]['sample']['wall_ns']),
            'chain_and_hashes_verified':True,'max_persisted_sample_gap_seconds':max(b-a for a,b in zip(samples,samples[1:]))/1e9,
            'reference_stale_samples':sum(r['decision']['reference_stale'] for r in decisions),
            'current_estimate_unusable_samples':sum(not r['decision']['current_estimate_usable'] for r in decisions),
            'raw_start_refused_samples':sum(not r['decision']['raw_start_allowed'] for r in decisions),
            'hard_fault_seen_samples':sum(r.get('history',{}).get('hard_fault_seen',False) for r in rows),
            'supervisor_pids':sorted({r['supervisor_pid'] for r in rows}),
            'start_decisions':starts,'child_finished':[r for r in rows if r['state']=='child_finished'],
            'monitor_reasons':dict(Counter(reason for r in rows for reason in r.get('reasons',[])))}
    probes = sorted(observations.values(),key=lambda r:r['clock']['sample']['monotonic_ns'])
    periods, updates, current, last_ref = [], [], None, None
    for p in probes:
        c = p['clock']; t = c['tracking']; stamp = c['sample']['wall_ns']
        key = tuple(c.get('reasons',[]))
        if current is None or tuple(current['reasons'])!=key:
            if current:periods.append(current)
            current={'first_at':kst(stamp),'last_at':kst(stamp),'reasons':list(key),'samples':0,'max_reference_age':0}
        current['last_at']=kst(stamp);current['samples']+=1
        current['max_reference_age']=max(current['max_reference_age'],c['reference_age_seconds'])
        ref = (t['reference_address'],t['reference_time'])
        if ref!=last_ref:
            updates.append({'first_observed_at':kst(stamp),'reference_at':kst(int(t['reference_time']*1e9)),
                'reference_time':t['reference_time'],'address':t['reference_address'],
                'change_from_prior_reference_seconds':t['reference_time']-last_ref[1] if last_ref else None})
            last_ref=ref
    if current:periods.append(current)
    result = {'window':[START,END],'scope':'Persisted process-clock observations; no per-message UTC certification. Sample counts are not incident counts. v1 terminal false is not a new clock failure.',
        'v1':v1,'v1_running_monitor_reason_samples':dict(all_reason_counts),'v2':v2,
        'provider_observations':len(probes),'provider_reason_samples':dict(Counter(reason for p in probes for reason in p['clock'].get('reasons',[]))),
        'provider_identities':sorted({(p['clock']['provider']['pid'],p['clock']['provider']['invocation_id']) for p in probes}),
        'min_reference_age_seconds':min(p['clock']['reference_age_seconds'] for p in probes),
        'max_reference_age_seconds':max(p['clock']['reference_age_seconds'] for p in probes),
        'max_correction_seconds':max(abs(p['clock']['tracking']['correction']) for p in probes),
        'max_conditional_error_seconds':max(p['clock']['conditional_error_bound_seconds'] for p in probes),
        'negative_reference_age_samples':sum(p['clock']['reference_age_seconds']<0 for p in probes),
        'reference_updates':updates,'provider_periods':periods}
    save('clock-inputs.json',inputs)
    save('clock-observation.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('v1','v2','provider_periods','reference_updates')},default=str))
    print(json.dumps({'v2':{s:{k:v for k,v in d.items() if k not in ('start_decisions','child_finished')} for s,d in v2.items()}}))


def host():
    # Reuse the already reviewed read-only host checks, changing only output path.
    spec = importlib.util.spec_from_file_location('boundary_observer',OUT.parent/'t11-first-child-boundary-2026-10-09/observe.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.OUT=OUT
    module.host()


def journals():
    tasks = {
        'collector':['-u','marketdata-collector@depth.service','-u','marketdata-collector@aggtrade.service'],
        'chrony':['-u','chrony.service','-u','marketdata-clock-bootstrap.service'],
        'jobs':['--user',*sum((['-u',u] for u in ('marketdata-clock-ready.service','marketdata-daily.service','marketdata-pack.service','marketdata-backup.service','marketdata-restic-archive.service','marketdata-network-observer.service','marketdata-oi.service','marketdata-oi-quality.service','marketdata-oi-probe.service')),[])]}
    summary={}
    for name,args in tasks.items():
        argv=['/usr/bin/journalctl','-b',BOOT.replace('-',''),'--all','--no-pager','-o','json',
              '--since','2026-10-09 00:11:18','--until','2026-10-10 00:11:17.999999',*args]
        # Include initial daemon/prepare messages before collector window.
        if name=='chrony':argv[argv.index('--since')+1]='2026-10-09 00:10:00'
        p=subprocess.run(argv,capture_output=True,timeout=60)
        assert p.returncode==0,p.stderr.decode()
        original=retain(name+'-journal.jsonl.gz',gzip.compress(p.stdout,mtime=0))
        selected=[];event_counts=Counter();failed_payloads=[];waits=[]
        for line in p.stdout.splitlines():
            r=json.loads(line);message=r.get('MESSAGE','')
            row={'at':kst(int(r['__REALTIME_TIMESTAMP'])*1000),'monotonic_us':int(r['__MONOTONIC_TIMESTAMP']),
                 'unit':r.get('UNIT') or r.get('USER_UNIT') or r.get('_SYSTEMD_USER_UNIT') or r.get('_SYSTEMD_UNIT'),
                 'invocation_id':r.get('_SYSTEMD_INVOCATION_ID'),'pid':r.get('_PID')}
            try:payload=json.loads(message)
            except (json.JSONDecodeError,TypeError):payload=None
            if isinstance(payload,dict):
                row['payload']={k:payload[k] for k in ('ready','phase','wait_seconds','error','state','complete','processed','counted_dates','reasons') if k in payload}
                if payload.get('ready') is False or payload.get('wait_seconds',0)>1:
                    waits.append(row)
                if payload.get('error'):failed_payloads.append(row)
                event_counts['structured_payload']+=1
            else:
                row['message']=message
                event_counts['message']+=1
            selected.append(row)
        parsed=retain(name+'-journal-parsed.json.gz',gzip.compress(dump(selected),mtime=0))
        summary[name]={'argv':argv,'stderr':p.stderr.decode(),'input':original,'parsed':parsed,'events':len(selected),
                       'event_counts':dict(event_counts),'wait_count':len(waits),'failed_payload_count':len(failed_payloads)}
    save('journal-inputs.json',summary)
    print(json.dumps(summary))


def summarize():
    stored=json.loads((OUT/'collections-and-storage.json').read_text())
    clock=json.loads((OUT/'clock-observation.json').read_text())
    host_state=json.loads((OUT/'host-state.json').read_text())
    journals={}
    for name in ('collector','chrony','jobs'):
        rows=[json.loads(line) for line in gzip.decompress((RAW/(name+'-journal.jsonl.gz')).read_bytes()).splitlines()]
        events=[];manager_counts=Counter();gate_counts=Counter();phases=Counter();daily=[];releases=[]
        for row in rows:
            message=row.get('MESSAGE','')
            unit=row.get('UNIT') or row.get('USER_UNIT') or row.get('_SYSTEMD_USER_UNIT') or row.get('_SYSTEMD_UNIT')
            at=kst(int(row['__REALTIME_TIMESTAMP'])*1000)
            try:payload=json.loads(message)
            except (ValueError,TypeError):payload=None
            base={'at':at,'unit':unit,'pid':row.get('_PID'),'invocation_id':row.get('_SYSTEMD_INVOCATION_ID')}
            if row.get('_PID') in ('1','1201'):
                # Preserve lifecycle and real failure messages, not repetitive
                # scheduled-task starts/CPU accounting or fragmented CLI reports.
                if any(s in message for s in ('Main process exited','Control process exited','Failed with result',
                                              'Start request repeated','timed out','Killing process')):
                    manager_counts[(unit,message)]+=1
                if name=='collector' and any(s in message for s in ('Started ','Stopped ','Main process exited','Start request repeated','timed out','Killing process')):
                    events.append(base|{'message':message})
                if name=='jobs' and (unit=='marketdata-daily.service' or 'Failed' in message or 'Start request repeated' in message):
                    events.append(base|{'message':message})
            if isinstance(payload,dict):
                if payload.get('phase'):phases[(unit,payload['phase'])]+=1
                if name=='collector' and payload.get('ready') is False:
                    hold=payload.get('hold',str(payload.get('reasons',[])))
                    gate_counts[(unit,hold)]+=1
                    events.append(base|{'gate_hold':hold})
                if name=='jobs' and payload.get('phase')=='released' and payload.get('wait_seconds',0)>1:
                    releases.append(base|{'wait_seconds':payload['wait_seconds']})
            if name=='jobs' and unit=='marketdata-daily.service' and isinstance(payload,list):
                daily.append(base|{'targets':[{'run_id':x.get('run_id'),'key':x.get('key'),
                    'complete':x.get('complete'),'state':x.get('state'),'errors':x.get('errors'),
                    'waiting':[v for v in x.get('results',[]) if v.get('state')=='waiting_source']}
                    for x in payload]})
        journals[name]={'raw_records':len(rows),'manager_event_counts':[{'unit':u,'message':m,'count':n} for (u,m),n in manager_counts.items()],
                        'gate_refusals':[{'unit':u,'hold':h,'count':n} for (u,h),n in gate_counts.items()],
                        'phase_counts':[{'unit':u,'phase':p,'count':n} for (u,p),n in phases.items()],
                        'selected_lifecycle_and_gates':events,'long_wait_releases':releases,'daily_results':daily}
    # Match every long pre-policy session boundary to the actual gate retries.
    classifications=[]
    for b in stored['boundaries']:
        lo=datetime.fromisoformat(b['previous_last_received_at']); hi=datetime.fromisoformat(b['next_collection_started_at'])
        stream='depth' if b['dataset']=='book_depth' else 'aggtrade'
        gates=[e for e in journals['collector']['selected_lifecycle_and_gates'] if e['unit']==f'marketdata-collector@{stream}.service'
               and 'gate_hold' in e and lo<=datetime.fromisoformat(e['at'])<=hi]
        if b['receipt_gap_seconds']<=60 and b['next_policy']=='v1' and not gates:continue
        if b['next_policy']=='v1':cause='strict_reference_age_gate' if gates else 'unclassified'
        elif b['previous'] in ('ee73406d4ab948e4bd5df2c15f67bbeb','e548bdfb967f40739e82bdcea9cf0ffb'):
            cause='controlled_cutover_extended_by_transition_parser_failure'
        else:cause='v2_child_rotation_with_continuous_supervisor'
        classifications.append(b|{'classification':cause,'gate_refusal_count':len(gates),
                                  'first_gate':gates[0] if gates else None,'last_gate':gates[-1] if gates else None})
    db=connect()
    latest=[]
    for r in db.execute('SELECT * FROM sync_runs r WHERE r.started_at=(SELECT max(s.started_at) FROM sync_runs s WHERE s.key_id=r.key_id)'):
        d=dict(r);d['report']=json.loads(d['report']);latest.append(d)
    db.close()
    latest_evidence=retain('latest-sync-runs.json.gz',gzip.compress(dump(latest),mtime=0))
    incomplete=[{'id':x['id'],'key':x['key_id'],'finished_at':x['finished_at'],'state':x['state'],
        'errors':x['report'].get('errors'),'waiting':[r for r in x['report'].get('results',[]) if r.get('state')=='waiting_source']}
        for x in latest if x['state']!='complete']
    complete = (not stored['issues'] and host_state['passed']
                and not any(b['classification']=='unclassified' for b in classifications)
                and all(d['chain_and_hashes_verified'] for d in clock['v2'].values()))
    result={
        'observed_at':datetime.now(timezone.utc).isoformat(),'observation_complete':complete,
        'window_kst':[kst(START),kst(END)],'window_seconds':(HIGH-LOW)/1e9,
        'boot_id':BOOT,'not_a_single_release_or_uninterrupted_market_capture':True,
        'storage_issues':stored['issues'],'sample_contract':stored['count_contract'],
        'source_counts':{'collections':len(stored['collections']),'sample_pairs':stored['canonical_samples'],
                         'sample_rows':stored['canonical_rows_sampled'],'provider_observations':clock['provider_observations']},
        'window_states':{},'boundary_classification':classifications,
        'provider_reasons':clock['provider_reason_samples'],'negative_reference_age_samples':clock['negative_reference_age_samples'],
        'reference_age_max_seconds':clock['max_reference_age_seconds'],
        'correction_max_seconds':clock['max_correction_seconds'],
        'conditional_error_max_seconds':clock['max_conditional_error_seconds'],
        'v2':{},'current_host_checks_passed':host_state['passed'],
        'newly_recorded_followups':{'T07':'02:00:25 coverage_exhausted, f8cddef376fe42a9af10c052b254a375 sequence 28974; diagnosis not performed',
                                  'T13':incomplete},
        'current_sync_runs':latest_evidence,
        'remaining_scope_locations':{'T04':'new evidence operational checkpoint/restic/restore; old v1 evidence backup',
            'T05':'deletion clock gate and separate approval; mask maintained',
            'T07':'depth loss analysis decision','T13':'daily waiting_source completion follow-up',
            'conditional':'job-specific clock policy, continuous capture, unsynchronised boot, time-source review'}}
    for dataset in ('book_depth','agg_trades'):
        items=[x for x in stored['collections'] if x['dataset']==dataset and x['kind']=='websocket']
        result['window_states'][dataset]={'started':len(items),'terminal_before_cutoff':dict(Counter(x['state'] for x in items if not x['extends_past_window'])),
            'open_at_cutoff':[x['id'] for x in items if x['extends_past_window']],
            'loss_notices':[x['failure_notice'] for x in items if x.get('failure_notice')]}
    for stream,d in clock['v2'].items():
        result['v2'][stream]={k:d[k] for k in ('artifacts','records_inside_window','max_persisted_sample_gap_seconds',
            'reference_stale_samples','current_estimate_unusable_samples','raw_start_refused_samples','hard_fault_seen_samples')}
        result['v2'][stream]['child_starts']=[{'at':kst(r['sample']['wall_ns']), 'reference_age_seconds':r['decision']['reference_age_seconds'],
                'reference_stale':r['decision']['reference_stale'],'raw_start_allowed':r['decision']['raw_start_allowed']} for r in d['start_decisions']]
    save('journal-review.json',journals)
    save('result.json',result)
    print(json.dumps({'observation_complete':complete,'followup_keys':[x['key'] for x in incomplete],
                      'boundaries_classified':dict(Counter(x['classification'] for x in classifications))}))


if __name__=='__main__':
    {'data':data,'clocks':clocks,'host':host,'journals':journals,'summarize':summarize}[sys.argv[1]]()
