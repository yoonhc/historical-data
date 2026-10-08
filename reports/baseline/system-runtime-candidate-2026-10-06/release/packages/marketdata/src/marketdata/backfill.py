"""Resumable historical batches that share the store with scheduled collection."""

import time
from collections import deque
from contextlib import nullcontext
from datetime import timedelta

from .binance import SourceError, archive_units
from .domain import ConflictError, DataError, full_days, now, utc


def backfill_set(
    collector,
    keys,
    start,
    end,
    *,
    progress=None,
    checkpoint=None,
    pause=1,
    sleep=time.sleep,
    lock_timeout=60,
    source="auto",
):
    start, end = utc(start), utc(end)
    list(full_days(start, end))
    if end > now().replace(hour=0, minute=0, second=0, microsecond=0):
        raise DataError("Batch backfill requires completed UTC days")
    if pause < 0:
        raise DataError("Batch pause must be nonnegative")
    if source not in {"auto", "archive", "rest"}:
        raise DataError("Unknown historical source")
    if any(k.dataset == "instrument_metadata" for k in keys):
        raise DataError("Metadata observations are not historical backfills")
    if any(k.series == "rest" and start < now() - timedelta(days=29) for k in keys):
        raise DataError("Historical REST OI exceeds retention; select the archive series")
    store, output = collector.store, []
    for key in keys:
        report = {
            "key": key.asdict(),
            "start": start,
            "end": end,
            "state": "running",
            "batches": [],
            "complete": False,
            "source": source,
        }
        output.append(report)
        # REST ranges are day-bounded, including sparse pre-archive launch periods.
        units = deque(archive_units(key, start, end, monthly=source != "rest"))
        while units:
            begin, stop, monthly = units.popleft()
            batch = {"start": begin, "end": stop, "monthly": monthly}
            try:
                with (
                    nullcontext()
                    if getattr(collector, "cooperative", False)
                    else store.writer(timeout=lock_timeout)
                ):
                    coverage = store.coverage(key, begin, stop)
                    if coverage["complete"]:
                        batch["state"] = "already_complete"
                    elif coverage["blocking_issues"]:
                        if monthly:
                            units.extendleft(reversed(list(archive_units(key, begin, stop, False))))
                            batch["state"] = "split_blocked_month"
                        else:
                            batch.update(state="blocked", issues=coverage["blocking_issues"])
                    else:
                        if source == "rest" or (
                            source == "auto"
                            and (key.dataset == "funding_events" or key.series == "rest")
                        ):
                            result = collector.rest(key, begin, stop)
                        else:
                            result = collector.archive(
                                key,
                                begin,
                                stop,
                                monthly=monthly,
                                refresh=True,
                                fallback_daily=False,
                            )
                        waiting = any(r["state"] == "waiting_source" for r in result)
                        if monthly and waiting:
                            units.extendleft(reversed(list(archive_units(key, begin, stop, False))))
                            batch["state"] = "fallback_daily"
                        else:
                            batch.update(state="checked", results=result)
            except (DataError, SourceError, OSError) as exc:
                batch.update(state="failed", error_type=type(exc).__name__, error=str(exc))
                if isinstance(exc, ConflictError) and monthly:
                    # Earlier days may already be committed. Replan daily and skip the conflict.
                    units.extendleft(reversed(list(archive_units(key, begin, stop, False))))
            if getattr(collector, "cooperative", False):
                report["writer_seconds"] = collector.writer_seconds
                report["writer_max_seconds"] = collector.writer_max_seconds
            report["batches"].append(batch)
            # Reporting and the courtesy pause happen after the writer is released.
            if checkpoint:
                checkpoint(output)
            if progress:
                progress(
                    {
                        "key": key.id,
                        "through": stop,
                        "state": batch["state"],
                        "pending_batches": len(units),
                    }
                )
            if units:
                sleep(
                    min(pause, 0.01) if batch["state"] in {"already_complete", "blocked"} else pause
                )
        report["coverage"] = store.coverage(key, start, end)
        report["complete"] = report["coverage"]["complete"] and not any(
            b["state"] == "failed" for b in report["batches"]
        )
        report["state"] = "complete" if report["complete"] else "partial"
        if checkpoint:
            checkpoint(output)
    return output
