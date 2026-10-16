from pathlib import Path
BASE=Path(__file__).resolve().parent/'candidate'
def edit(p,old,new):
 s=p.read_text(); assert s.count(old)==1,(str(p),old[:70],s.count(old)); p.write_text(s.replace(old,new))
for v in ('base','daily','preserve'):
 p=BASE/v/'packages/marketdata/src/marketdata/operations.py'
 edit(p,'    current = utc(current) if current else now()\n    if keys is None:',
  "    current = utc(current) if current else now()\n    time_point = None\n    if jc.active():\n        try:\n            time_point = jc.active().point('operations_status')\n            current = time_point.lower\n        except jc.ClockDeferred as exc:\n            rows = [dict(r) for r in store.db.execute('SELECT id,key_id,state,finished_at FROM sync_runs WHERE rowid IN (SELECT MAX(rowid) FROM sync_runs GROUP BY key_id)')]\n            return {'healthy': False, 'freshness_known': False, 'reason': str(exc), 'recorded_runs': rows, 'data_reclassified': False}\n    if keys is None:")
 edit(p,'        stale = age is None or age > limit or age < 0',
  "        time_status = jc.freshness(None, latest, limit/3600) if latest and jc.active() else {}\n        stale = (time_status.get('fresh') is not True if jc.active() else age is None or age > limit or age < 0)")
 edit(p,'                "age_seconds": age,','                "age_seconds": age,\n                **time_status,')
 edit(p,'                "last_run": latest_run,',
  '                "last_run": latest_run,\n                "execution_succeeded": bool(latest_run and latest_run["state"] == "complete"),')
 p=BASE/v/'packages/marketdata/src/marketdata/maintenance.py'
 edit(p,'        "modes": modes,',
  '        "modes": modes,\n        **({"freshness_known": all(m.get("freshness_known", False) for m in modes.values())} if jc.active() else {}),')
# Preserve the transport response if observation quality fails after a successful start.
p=BASE/'base/scripts/network_observer.py'
s=p.read_text(); at=s.index('\nimport '); p.write_text(s[:at]+'\nfrom marketdata import job_clock as jc\n'+s[at:])
edit(p,'    started = now()\n    selected = route(',
 "    observation = jc.active().point('network_observation_start') if jc.active() else None\n    started = now()\n    selected = route(")
edit(p,'    failed = [name for name, item in probes.items() if item["ok"] is False]',
 "    time_quality = {}\n    if observation:\n        try:\n            end_point = jc.active().point('network_observation_end')\n            time_quality = {'current_time_known': True, 'start': observation.evidence, 'end': end_point.evidence}\n        except jc.ClockDeferred as exc:\n            time_quality = {'current_time_known': False, 'start': observation.evidence, 'reason': str(exc)}\n    failed = [name for name, item in probes.items() if item[\"ok\"] is False]")
edit(p,'        "version": 1,','        "version": 1,\n        **({"local_time_quality": time_quality} if observation else {}),')
p=BASE/'preserve/scripts/restic_schedule.py'
s=p.read_text(); at=s.index('\nimport '); p.write_text(s[:at]+'\nfrom marketdata import job_clock as jc\n'+s[at:])
edit(p,'        context = service_context()',
 "        clock_start = jc.active().point('archive_preservation_start') if jc.active() else None\n        context = service_context()")
edit(p,'            "counted_dates": [],',
 '            "counted_dates": [],\n            **({"clock_observation": clock_start.evidence} if clock_start else {}),')
edit(p,'            candidates = eligible_records(observation, context, receipt, load_state(profile))',
 "            clock_known = True\n            if jc.active():\n                try:\n                    record['clock_at_qualification'] = jc.active().point('archive_qualification').evidence\n                except jc.ClockDeferred as exc:\n                    clock_known = False\n                    record['clock_qualification_reason'] = str(exc)\n            record['freshness_known'] = clock_known\n            candidates = eligible_records(observation, context, receipt, load_state(profile)) if clock_known else []")
# A committed publication followed by a time deferral must recover via its saved intent.
p=BASE/'daily/packages/marketdata/src/marketdata/daily_schedule.py'
edit(p,'            if attempt["state"] != "running":',
 '            if attempt["state"] not in {"running", "deferred_clock"}:')
edit(p,'            else:\n                attempt.update(state="interrupted", finished_at=self.clock())',
 "            else:\n                if attempt['state'] == 'deferred_clock':\n                    continue  # eligible next_at/identity and the original budgets are retained\n                attempt.update(state=\"interrupted\", finished_at=self.clock())")
# Keep all raw archive/transport time stamps; only decisions change.
print('Bounded adapters completed')
