"""Persisted monitoring scopes, gap recovery, and independently recorded update runs."""

from . import job_clock as jc

import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from uuid import uuid4

from .binance import SourceError
from .domain import DAY, EPOCH, DataError, Key, day_ranges, from_epoch, json_bytes, micros, now, utc


@dataclass(frozen=True)
class SyncPolicy:
    start: str | None = None
    bootstrap_days: int = 3
    refresh_days: int = 3
    overlap_minutes: int = 60
    max_days_per_run: int = 32
    stale_after_seconds: int | None = None

    def __post_init__(self):
        for field in ("bootstrap_days", "max_days_per_run"):
            if type(getattr(self, field)) is not int or not 1 <= getattr(self, field) <= 366:
                raise DataError(f"{field} must be an integer between 1 and 366")
        if type(self.refresh_days) is not int or not 0 <= self.refresh_days < self.max_days_per_run:
            raise DataError("refresh_days must be nonnegative and below max_days_per_run")
        if type(self.overlap_minutes) is not int or not 5 <= self.overlap_minutes <= 1440:
            raise DataError("overlap_minutes must be between 5 and 1440")
        if self.stale_after_seconds is not None and (
            type(self.stale_after_seconds) is not int or self.stale_after_seconds < 1
        ):
            raise DataError("stale_after_seconds must be positive")
        if self.start is not None:
            value = utc(self.start)
            if value != midnight(value):
                raise DataError("operations.start must be a UTC midnight date")


def midnight(value):
    return utc(value).replace(hour=0, minute=0, second=0, microsecond=0)


def floor_grid(value, seconds):
    return EPOCH + timedelta(seconds=micros(value) // (seconds * 1_000_000) * seconds)


def is_live_oi(key):
    return key.dataset == "open_interest" and key.series == "rest"


def window_end(key, current):
    if key.dataset == "instrument_metadata":
        return current
    if is_live_oi(key):
        # REST OI labels are period ENDS. After the publication grace, include
        # the just-ended period using an exclusive label-grid bound.
        return floor_grid(current - timedelta(seconds=45), 300) + timedelta(minutes=5)
    return midnight(current)


def merge_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def scope_start(store, key, policy, end):
    if policy.start is not None:
        return utc(policy.start)
    saved = store.db.execute("SELECT start FROM sync_targets WHERE key_id=?", (key.id,)).fetchone()
    if saved:
        return utc(saved["start"])
    start = end - timedelta(days=policy.bootstrap_days)
    first = store.db.execute("SELECT MIN(day) FROM active WHERE key_id=?", (key.id,)).fetchone()[0]
    return min(start, utc(first)) if first else start


def register_target(store, key, policy, start, current):
    with store.db:
        store.db.execute(
            """INSERT INTO sync_targets VALUES(?,?,?,?,?,?)
            ON CONFLICT(key_id) DO UPDATE SET start=excluded.start, policy=excluded.policy,
            updated_at=excluded.updated_at""",
            (
                key.id,
                json_bytes(key.asdict()).decode(),
                start.isoformat(),
                json_bytes(asdict(policy)).decode(),
                current.isoformat(),
                current.isoformat(),
            ),
        )


def query_gaps(store, key, start, end):
    intervals = [
        (utc(r["start"]), utc(r["end"]))
        for r in store.db.execute("SELECT start,end FROM queries WHERE key_id=?", (key.id,))
    ]
    cursor, gaps = start, []
    for begin, stop in merge_ranges(intervals):
        if stop <= cursor or begin >= end:
            continue
        if begin > cursor:
            gaps.append([cursor, min(begin, end)])
        cursor = max(cursor, min(stop, end))
    if cursor < end:
        gaps.append([cursor, end])
    return gaps


def inventory(store, key, start, end, *, cache=False):
    """Coverage is cached by immutable revision. Blocking issues are always read fresh."""
    start, end = utc(start), utc(end)
    if start >= end:
        raise DataError("Operational start must precede the eligible end")
    records = {r["day"]: r for r in store.revision_list(key, start, end)}
    blocking = {
        r["day"]
        for r in store.db.execute(
            """SELECT DISTINCT day FROM issues
        WHERE key_id=? AND blocking=1 AND resolved=0 AND day>=? AND day<=?""",
            (
                key.id,
                start.date().isoformat(),
                (end - timedelta(microseconds=1)).date().isoformat(),
            ),
        )
    }
    gaps, days = [], []
    event_gaps = query_gaps(store, key, start, end) if not key.step else []
    for begin, stop in day_ranges(start, end):
        day = begin.date().isoformat()
        record = records.get(day)
        if key.step:
            if record is None:
                missing = [[begin, stop]]
            else:
                cached = store.db.execute(
                    "SELECT coverage FROM sync_coverage WHERE revision_id=?", (record["id"],)
                ).fetchone()
                if cached:
                    coverage = json.loads(cached["coverage"])
                else:
                    coverage = store.coverage(key, utc(day), utc(day) + DAY)
                    if cache:
                        with store.db:
                            store.db.execute(
                                "INSERT OR IGNORE INTO sync_coverage VALUES(?,?)",
                                (
                                    record["id"],
                                    json_bytes(
                                        {"gap_ranges_us": coverage["gap_ranges_us"]}
                                    ).decode(),
                                ),
                            )
                missing = [
                    [max(begin, from_epoch(a, "us")), min(stop, from_epoch(b, "us"))]
                    for a, b in coverage["gap_ranges_us"]
                    if from_epoch(a, "us") < stop and from_epoch(b, "us") > begin
                ]
        else:
            missing = [
                [max(begin, a), min(stop, b)] for a, b in event_gaps if a < stop and b > begin
            ]
        gaps.extend(missing)
        days.append(
            {
                "day": day,
                "start": begin,
                "end": stop,
                "missing_ranges": missing,
                "blocked": day in blocking,
            }
        )
    gaps = merge_ranges(gaps)
    missing_rows = (
        sum(micros(b) - micros(a) for a, b in gaps) // (key.step * 1_000_000) if key.step else None
    )
    return {
        "start": start,
        "end": end,
        "missing_rows": missing_rows,
        "missing_ranges": gaps,
        "missing_days": sum(bool(d["missing_ranges"]) for d in days),
        "blocked_days": sorted(blocking),
        "complete": not gaps and not blocking,
        "days": days,
        "semantics": "source_label_grid_only"
        if key.dataset == "open_interest"
        else "bar_grid"
        if key.step
        else "source_query_coverage_not_proof_of_event_completeness",
    }


def public_inventory(coverage):
    return {k: v for k, v in coverage.items() if k != "days"}


def plan_tasks(key, policy, coverage, current):
    end = coverage["end"]
    live = is_live_oi(key)
    # Keep a full period of slack against the collector's moving 29-day cutoff.
    eligible = (
        floor_grid(current - timedelta(days=29), 300) + timedelta(minutes=10)
        if live
        else coverage["start"]
    )
    recent = end - (
        timedelta(minutes=policy.overlap_minutes) if live else timedelta(days=policy.refresh_days)
    )
    candidates, expired = [], []
    for day in coverage["days"]:
        if live:
            expired.extend([a, min(b, eligible)] for a, b in day["missing_ranges"] if a < eligible)
        if day["blocked"]:
            continue
        ranges = list(day["missing_ranges"])
        if day["end"] > recent:
            ranges.append([max(day["start"], recent), day["end"]])
        if live:
            ranges = merge_ranges([[max(a, eligible), b] for a, b in ranges])
        elif ranges:
            ranges = [[day["start"], day["end"]]]
        if ranges:
            candidates.append((day["end"] <= recent, day["start"], ranges))
    # Maintain fresh data while draining older backlog in bounded daily batches.
    candidates.sort(key=lambda item: (item[0], item[1]))
    selected = candidates[: policy.max_days_per_run]
    return {
        "ranges": [r for _, _, group in selected for r in group],
        "deferred_days": max(0, len(candidates) - len(selected)),
        "expired_missing_ranges": merge_ranges(expired),
        "retention_floor": eligible if live else None,
    }


def latest_data(store, key):
    summary = store.db.execute(
        """SELECT COUNT(*) AS partitions, COALESCE(SUM(r.row_count),0) AS rows,
        MIN(a.day) AS first_day, MAX(a.day) AS last_day FROM active a JOIN revisions r
        ON a.revision_id=r.id WHERE a.key_id=?""",
        (key.id,),
    ).fetchone()
    record = store.db.execute(
        """SELECT r.* FROM active a JOIN revisions r ON a.revision_id=r.id
        WHERE a.key_id=? AND r.row_count>0 ORDER BY a.day DESC LIMIT 1""",
        (key.id,),
    ).fetchone()
    rows = store._revision_rows(record) if record else []
    latest = rows[-1] if rows else None
    return {
        **dict(summary),
        "latest_timestamp": latest["timestamp"] if latest else None,
        "latest_first_observed_at": latest["first_observed_at"] if latest else None,
    }


def _record_run(store, identity, state, report, *, finished=True):
    stamp = now().isoformat()
    with store.db:
        store.db.execute(
            "UPDATE sync_runs SET state=?,report=?,updated_at=?,finished_at=? WHERE id=?",
            (state, json_bytes(report).decode(), stamp, stamp if finished else None, identity),
        )


def update(collector, keys, policy=None, *, current=None, progress=None):
    """Caller must hold store.writer(); every restart replans from published coverage."""
    policy, current = policy or SyncPolicy(), utc(current) if current else now()
    observation = jc.active().point('oi_update_range') if jc.active() else None
    if observation:
        observation.grid(300, slack=45)
        observation.day()
        current = observation.lower
    store, output = collector.store, []
    with store.db:
        store.db.execute(
            """UPDATE sync_runs SET state='interrupted',finished_at=?,updated_at=?
            WHERE state='running'""",
            (current.isoformat(), current.isoformat()),
        )
    for key in keys:
        end = window_end(key, current)
        start = scope_start(store, key, policy, end)
        if start >= end:
            raise DataError("Operational start must precede the eligible end")
        register_target(store, key, policy, start, current)
        identity = uuid4().hex
        report = {
            "run_id": identity,
            **({"clock_observation": observation.evidence} if observation else {}),
            "key": key.id,
            "start": start,
            "end": end,
            "state": "running",
            "results": [],
            "errors": [],
            "complete": False,
        }
        with store.db:
            store.db.execute(
                "INSERT INTO sync_runs VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    key.id,
                    start.isoformat(),
                    end.isoformat(),
                    current.isoformat(),
                    current.isoformat(),
                    None,
                    "running",
                    json_bytes(report).decode(),
                ),
            )
        try:
            if key.dataset == "instrument_metadata":
                report["results"] = collector.rest(key, current - timedelta(seconds=1), current)
                head = latest_data(store, key)
                report["complete"] = bool(
                    head["latest_timestamp"] and head["latest_timestamp"] >= current
                )
                report["semantics"] = "current_observation_only"
            else:
                before = inventory(store, key, start, end, cache=True)
                plan = plan_tasks(key, policy, before, current)
                report["plan"] = plan
                report["coverage_before"] = public_inventory(before)
                for begin, stop in plan["ranges"]:
                    try:
                        if is_live_oi(key) or key.dataset == "funding_events":
                            # A grid bound can be after wall time; the HTTP query cannot.
                            result = collector.rest(key, begin, min(stop, current))
                        else:
                            result = collector.archive(
                                key, begin, stop, monthly=False, refresh=True
                            )
                        report["results"].extend(result)
                    except (DataError, SourceError, OSError, KeyError, ValueError) as exc:
                        report["errors"].append(
                            {
                                "start": begin,
                                "end": stop,
                                "type": type(exc).__name__,
                                "message": str(exc),
                            }
                        )
                        if isinstance(exc, SourceError) and exc.status != 404:
                            break
                    _record_run(store, identity, "running", report, finished=False)
                    if progress:
                        progress({"run_id": identity, "key": key.id, "through": stop})
                after = inventory(store, key, start, end, cache=True)
                report["coverage"] = public_inventory(after)
                report["complete"] = (
                    after["complete"]
                    and not report["errors"]
                    and not any(
                        r["state"] in {"quarantined", "waiting_source", "failed"}
                        for r in report["results"]
                    )
                )
            report["state"] = (
                "complete" if report["complete"] else "failed" if report["errors"] else "partial"
            )
        except jc.ClockFailure as exc:
            report.update(state="failed", complete=False)
            report["errors"].append({"type": type(exc).__name__, "message": str(exc)})
            _record_run(store, identity, "failed", report)
            raise
        except jc.ClockDeferred as exc:
            report.update(state='deferred_clock', complete=False, clock_reason=str(exc))
            _record_run(store, identity, 'deferred_clock', report)
            raise
        except KeyboardInterrupt:
            report.update(state="interrupted", complete=False)
            _record_run(store, identity, "interrupted", report)
            raise
        except Exception as exc:
            report.update(state="failed", complete=False)
            report["errors"].append({"type": type(exc).__name__, "message": str(exc)})
        _record_run(store, identity, report["state"], report)
        output.append(report)
        if progress:
            progress({"run_id": identity, "key": key.id, "state": report["state"]})
    return output


def status(store, keys=None, policy=None, *, current=None):
    current = utc(current) if current else now()
    time_point = None
    if jc.active():
        try:
            time_point = jc.active().point('operations_status')
            current = time_point.lower
        except jc.ClockDeferred as exc:
            rows = [dict(r) for r in store.db.execute('SELECT id,key_id,state,finished_at FROM sync_runs WHERE rowid IN (SELECT MAX(rowid) FROM sync_runs GROUP BY key_id)')]
            return {'healthy': False, 'freshness_known': False, 'reason': str(exc), 'recorded_runs': rows, 'data_reclassified': False}
    if keys is None:
        found = {}
        for table in ("revisions", "sync_targets"):
            for row in store.db.execute(f"SELECT DISTINCT key_id,key_json FROM {table}"):
                found[row["key_id"]] = Key(**json.loads(row["key_json"]))
        keys = [found[k] for k in sorted(found)]
    output = []
    for key in keys:
        saved = store.db.execute("SELECT * FROM sync_targets WHERE key_id=?", (key.id,)).fetchone()
        selected = policy or (SyncPolicy(**json.loads(saved["policy"])) if saved else SyncPolicy())
        end = window_end(key, current)
        start = scope_start(store, key, selected, end)
        head = latest_data(store, key)
        latest = head["latest_timestamp"]
        age = (current - latest).total_seconds() if latest else None
        limit = selected.stale_after_seconds or (900 if is_live_oi(key) else 129600)
        time_status = jc.freshness(None, latest, limit/3600) if latest and jc.active() else {}
        stale = (time_status.get('fresh') is not True if jc.active() else age is None or age > limit or age < 0)
        if key.dataset == "instrument_metadata":
            coverage = {
                "complete": not stale,
                "semantics": "observation_only_no_historical_inference",
            }
            plan = None
        else:
            coverage = inventory(store, key, start, end)
            plan = plan_tasks(key, selected, coverage, current)
        run = store.db.execute(
            "SELECT * FROM sync_runs WHERE key_id=? ORDER BY started_at DESC,rowid DESC LIMIT 1",
            (key.id,),
        ).fetchone()
        latest_run = (
            {k: run[k] for k in ("id", "state", "started_at", "updated_at", "finished_at")}
            if run
            else None
        )
        if latest_run:
            details = json.loads(run["report"])
            latest_run["errors"] = details.get("errors", [])
        historical = store.db.execute(
            """SELECT kind,COUNT(DISTINCT day) AS days FROM issues
            WHERE key_id=? AND resolved=0 AND blocking=1 GROUP BY kind""",
            (key.id,),
        ).fetchall()
        blocked_days = store.db.execute(
            """SELECT COUNT(DISTINCT day) FROM issues
            WHERE key_id=? AND resolved=0 AND blocking=1""",
            (key.id,),
        ).fetchone()[0]
        job = store.db.execute(
            "SELECT state,updated_at,error FROM jobs WHERE key_id=? ORDER BY updated_at DESC,rowid DESC LIMIT 1",
            (key.id,),
        ).fetchone()
        state = (
            "empty"
            if latest is None
            else "freshness_unknown"
            if jc.active() and not time_status.get("freshness_known", False)
            else "stale"
            if stale
            else "partial"
            if not coverage["complete"] or (latest_run and latest_run["state"] == "partial")
            else "failed"
            if latest_run and latest_run["state"] in {"failed", "interrupted"}
            else "running"
            if latest_run and latest_run["state"] == "running"
            else "unmanaged"
            if latest_run is None
            else "healthy"
        )
        output.append(
            {
                "key": key.asdict(),
                "state": state,
                "data": head,
                "age_seconds": age,
                **time_status,
                "stale_after_seconds": limit,
                "scope_registered": saved is not None,
                "coverage": public_inventory(coverage),
                "expired_missing_ranges": plan["expired_missing_ranges"] if plan else [],
                "historical_blocking_issues": [dict(r) for r in historical],
                "historical_blocked_days": blocked_days,
                "last_run": latest_run,
                "execution_succeeded": bool(latest_run and latest_run["state"] == "complete"),
                "latest_source_job": dict(job) if job else None,
            }
        )
    return {
        "checked_at": current,
        "healthy": bool(output) and all(r["state"] == "healthy" for r in output),
        "datasets": output,
        "coverage_note": "Operational scopes only; older imported history may have separate gaps",
        "integrity_note": "Cached immutable revision coverage; use validate for full file integrity checks",
    }
