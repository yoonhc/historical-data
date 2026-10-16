"""Explicit edits against pinned installation copies; refuses unexpected source text."""
from pathlib import Path
import shutil
BASE=Path(__file__).resolve().parent/'candidate'

def change(path, old, new, count=1):
    data=path.read_text()
    assert data.count(old)==count, (str(path),old[:80],data.count(old))
    path.write_text(data.replace(old,new))

for variant in ('base','daily','preserve'):
    pkg=BASE/variant/'packages/marketdata/src/marketdata'
    shutil.copyfile(BASE/'policy/job_clock.py',pkg/'job_clock.py')
    for name in ('packing','maintenance','operations','oi_probe','oi_quality','binance','cli'):
        p=pkg/(name+'.py')
        data=p.read_text(); at=data.index('\nimport ')
        p.write_text(data[:at]+'\nfrom . import job_clock as jc\n'+data[at:])
    change(pkg/'packing.py', '    cutoff = utc(before) if before else now() - timedelta(hours=1)',
        "    observation = jc.active().point('pack_selection') if jc.active() else None\n    cutoff = utc(before) if before else (observation.lower if observation else now()) - timedelta(hours=1)\n    if observation and cutoff > observation.lower - timedelta(hours=1):\n        raise jc.ClockDeferred('pack_cutoff_not_proven_old')")
    change(pkg/'packing.py', '        "before": cutoff,', '        "before": cutoff,\n        **({"clock_observation": observation.evidence} if observation else {}),')
    # Preserve the selected, proven day through long validation and deferred retries.
    change(pkg/'maintenance.py', '        started = now()\n        report = {',
        "        point = jc.active().point('maintenance_after_lock') if jc.active() else None\n        started = now()\n        work_day = (jc.active().pin_day('backup_day', point) if mode == 'daily' and point\n                    else started.date().isoformat())\n        report = {")
    change(pkg/'maintenance.py', '            "mode": mode,',
        '            "mode": mode,\n            "work_day": work_day,\n            **({"clock_observation": point.evidence} if point else {}),')
    change(pkg/'maintenance.py', '                phase("backup")',
        "                if jc.active():\n                    report['backup_decision_observation'] = jc.active().wait_point('backup_after_validation').evidence\n                phase(\"backup\")")
    change(pkg/'maintenance.py', '                    started.date().isoformat(),','                    work_day,')
    change(pkg/'maintenance.py', '        except (Exception, KeyboardInterrupt) as exc:',
        "        except jc.ClockDeferred as exc:\n            report.update(state='deferred_clock', error=str(exc))\n            raise\n        except (Exception, KeyboardInterrupt) as exc:")
    change(pkg/'maintenance.py','        modes[mode] = {',
        "        time_status = (jc.freshness(bool(report and report['ok']), utc(report['started_at']), hours)\n                       if report and jc.active() else {})\n        modes[mode] = {")
    change(pkg/'maintenance.py', '            "report": report,',
        '            "report": report,\n            **time_status,')
    change(pkg/'maintenance.py','    space = capacity(store.root, min_free_gib)',
        "    if jc.active():\n        for value in modes.values():\n            value['healthy'] = (value.get('execution_succeeded', False) and value.get('fresh') is True)\n    space = capacity(store.root, min_free_gib)")
    # OI update: choose the completed grid after taking the existing writer lock.
    change(pkg/'operations.py','    store, output = collector.store, []',
        "    observation = jc.active().point('oi_update_range') if jc.active() else None\n    if observation:\n        observation.grid(300, slack=45)\n        observation.day()\n        current = observation.lower\n    store, output = collector.store, []")
    change(pkg/'operations.py','            "run_id": identity,','            "run_id": identity,\n            **({"clock_observation": observation.evidence} if observation else {}),')
    change(pkg/'operations.py','        except KeyboardInterrupt:',
        "        except jc.ClockDeferred as exc:\n            report.update(state='deferred_clock', complete=False, clock_reason=str(exc))\n            _record_run(store, identity, 'deferred_clock', report)\n            raise\n        except KeyboardInterrupt:")
    # HTTP rechecks occur after sleeps, before requests; invalid time spends no HTTP budget.
    change(pkg/'binance.py','            window.append((time.monotonic(), weight))',
        "            if jc.active():\n                jc.active().point('http_request')\n            window.append((time.monotonic(), weight))")
    change(pkg/'binance.py','                        delay = (parsedate_to_datetime(retry) - now()).total_seconds()',
        "                        basis = jc.active().point('http_absolute_retry_after').lower if jc.active() else now()\n                        delay = (parsedate_to_datetime(retry) - basis).total_seconds()")
    # OI observer: lower bound governs target/cooldown, received raw wall time remains raw.
    change(pkg/'oi_probe.py','        current = utc(clock())',
        "        observation = jc.active().point('oi_probe_target') if jc.active() else None\n        if observation:\n            observation.grid(300)\n        current = observation.lower if observation else utc(clock())")
    change(pkg/'oi_probe.py','            "version": PROBE_VERSION,',
        '            "version": PROBE_VERSION,\n            **({"clock_observation": observation.evidence} if observation else {}),')
    change(pkg/'oi_probe.py','                cooldown_until=received + timedelta(seconds=retry_delay(exc, received)),',
        "                cooldown_until=received + timedelta(seconds=retry_delay(exc, received)),\n                **({'pending_clock_cooldown': {'status': exc.status, 'headers': dict(exc.headers)}} if jc.active() else {}),")
    # Persist the raw limit before any clock re-evaluation, so a crash cannot lose it.
    change(pkg/'oi_probe.py','            until = previous.get("cooldown_until")',
        "            pending = previous.get('pending_clock_cooldown')\n            if pending and observation:\n                error = SourceError('retained cooldown', pending['status'], headers=pending['headers'])\n                previous['cooldown_until'] = observation.upper + timedelta(seconds=retry_delay(error, observation.upper))\n                previous.pop('pending_clock_cooldown')\n                previous['cooldown_clock_observation'] = observation.evidence\n                atomic_write(latest, json_bytes(previous))\n            until = previous.get(\"cooldown_until\")")
    # OI quality uses an explicit fixed scope; current-as-of remains separate.
    change(pkg/'cli.py', '            end = (\n                utc(args.end)',
        "            time_point = jc.active().point('oi_quality_scope') if jc.active() else None\n            selected_day = (jc.active().pin_day('oi_quality_day', time_point) if time_point else None)\n            end = (\n                utc(args.end)")
    change(pkg/'cli.py','                else now().replace(hour=0, minute=0, second=0, microsecond=0)',
        '                else utc(selected_day) if selected_day else now().replace(hour=0, minute=0, second=0, microsecond=0)')
    change(pkg/'oi_quality.py','    list(full_days(start, end))',
        "    point = jc.active().point('oi_quality_as_of') if jc.active() else None\n    if point:\n        as_of = point.lower\n    list(full_days(start, end))")
    change(pkg/'oi_quality.py','        "format_version": 1,',
        '        "format_version": 1,\n        **({"clock_observation": point.evidence} if point else {}),')

pkg=BASE/'daily/packages/marketdata/src/marketdata'; p=pkg/'daily_schedule.py'
data=p.read_text(); at=data.index('\nimport '); p.write_text(data[:at]+'\nfrom . import job_clock as jc\n'+data[at:])
change(p, '        host = urlsplit(str(url)).hostname\n        for row in self.ledger.rows("daily_cooldowns"):',
    "        point = jc.active().point('daily_request_cooldown') if jc.active() else None\n        current = point.lower if point else self.clock()\n        host = urlsplit(str(url)).hostname\n        for row in self.ledger.rows(\"daily_cooldowns\"):\n            if row['id'] == host and row.get('pending_clock_cooldown'):\n                self.cooldown(url, row['headers'])\n                return self.check_cooldown(url)")
change(p,'utc(row["until"]) > self.clock():','utc(row["until"]) > current:')
change(p,'(utc(row["until"]) - self.clock()).total_seconds()', '(utc(row["until"]) - current).total_seconds()')
change(p,'    def cooldown(self, url, headers):\n        current = self.clock()',
    "    def cooldown(self, url, headers):\n        host = urlsplit(str(url)).hostname\n        if jc.active():\n            old = next((r for r in self.ledger.rows('daily_cooldowns') if r['id'] == host), {})\n            self.ledger.save('daily_cooldowns', {**old, 'id': host, 'until': old.get('until', '9999-01-01T00:00:00+00:00'),\n                'pending_clock_cooldown': True, 'headers': dict(headers)})\n        point = jc.active().point('daily_rate_limit_received') if jc.active() else None\n        current = point.upper if point else self.clock()")
change(p,'            if old["id"] == host:\n                until = max(until, utc(old["until"]))',
    "            if old[\"id\"] == host and old.get('until') != '9999-01-01T00:00:00+00:00':\n                until = max(until, utc(old[\"until\"]))")
change(p,'        key, current = Key(**task["key"]), self.clock()\n        if self.readiness:',
    "        key, current = Key(**task[\"key\"]), self.clock()\n        if jc.active():\n            point = jc.active().point('daily_task_eligibility')\n            if not all(point.reached(utc(task[n])) for n in ('first_eligible_at','next_at') if task.get(n)):\n                return\n            current = point.lower\n            task['clock_observation'] = point.evidence\n        if self.readiness:")
change(p,'        except (DataError, SourceError, OSError, ValueError, KeyError) as exc:\n            self.failure(task, attempt, exc)',
    "        except jc.ClockDeferred as exc:\n            task.update(state='deferred_clock', reason=str(exc))\n            attempt.update(state='deferred_clock', error={'type':'ClockDeferred','message':str(exc)}, finished_at=self.clock())\n            if grant and not attempt['requests']:\n                grant['remaining'] += 1\n                attempt['manual_budget_refunded_before_http'] = True\n            self.ledger.save_many([('daily_tasks', task), ('daily_attempts', attempt)])\n            raise\n        except (DataError, SourceError, OSError, ValueError, KeyError) as exc:\n            self.failure(task, attempt, exc)")
change(p,'            current = self.clock()\n            self.recover()',
    "            observation = jc.active().point('daily_plan_dates') if jc.active() else None\n            if observation:\n                observation.day(); observation.day(KST)\n            current = observation.center if observation else self.clock()\n            self.recover()")
change(p,'            report.update(\n                id=uuid.uuid4().hex,',
    "            if observation:\n                report['clock_observation'] = observation.evidence\n            report.update(\n                id=uuid.uuid4().hex,")
print('Patched three isolated application copies; original installations untouched')
