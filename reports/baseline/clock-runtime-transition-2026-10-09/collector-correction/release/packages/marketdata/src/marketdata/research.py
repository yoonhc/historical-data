"""Coverage ledgers and explicit contiguous ranges for reproducible research."""

from math import lcm

from .binance import next_month
from .domain import DAY, DataError, from_epoch, micros, now, utc
from .operations import merge_ranges, query_gaps


def available_ranges(start, end, unavailable, step=60):
    start, end = utc(start), utc(end)
    cursor, ranges = start, []
    for begin, stop in merge_ranges(unavailable):
        begin, stop = max(start, begin), min(end, stop)
        if begin >= stop:
            continue
        if cursor < begin:
            ranges.append([cursor, begin])
        cursor = max(cursor, stop)
    if cursor < end:
        ranges.append([cursor, end])
    quantum = step * 1_000_000
    aligned = []
    for begin, stop in ranges:
        a = from_epoch(((micros(begin) + quantum - 1) // quantum) * quantum, "us")
        b = from_epoch((micros(stop) // quantum) * quantum, "us")
        if a < b:
            aligned.append([a, b])
    return aligned


def dataset_ledger(store, key, start, end):
    months, gaps, issues, rows, expected, missing = [], [], {}, 0, 0, 0
    cursor = start
    while cursor < end:
        stop = min(
            next_month(cursor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)), end
        )
        coverage = store.coverage(key, cursor, stop)
        rows += coverage["rows"]
        expected += coverage.get("expected", 0)
        missing += coverage.get("missing", 0)
        gaps.extend(
            [from_epoch(a, "us"), from_epoch(b, "us")] for a, b in coverage.get("gap_ranges_us", [])
        )
        issues.update({issue["id"]: issue for issue in coverage["blocking_issues"]})
        months.append(
            {
                "start": cursor,
                "end": stop,
                "rows": coverage["rows"],
                "missing_rows": coverage.get("missing"),
                "complete": coverage["complete"],
                "blocked_days": len({i["day"] for i in coverage["blocking_issues"]}),
            }
        )
        cursor = stop
    if not key.step:
        gaps = query_gaps(store, key, start, end)
    gaps = merge_ranges(gaps)
    blocked = sorted({i["day"] for i in issues.values()})
    unavailable = merge_ranges(
        gaps + [[max(start, utc(d)), min(end, utc(d) + DAY)] for d in blocked]
    )
    records = store.revision_list(key, start, end)
    return {
        "key": key.asdict(),
        "start": start,
        "end": end,
        "rows": rows,
        "expected_rows": expected if key.step else None,
        "missing_rows": missing if key.step else None,
        "missing_ranges": gaps,
        "blocked_days": blocked,
        "blocking_issues": list(issues.values()),
        "unavailable_ranges": unavailable,
        "complete": not unavailable,
        "contiguous_ranges": available_ranges(start, end, unavailable, key.step or 1),
        "monthly": months,
        "active_partitions": len(records),
        "first_partition": records[0]["day"] if records else None,
        "last_partition": records[-1]["day"] if records else None,
        "semantics": coverage["semantics"],
    }


def research_report(store, keys, start, end):
    start, end = utc(start), utc(end)
    if start >= end or not keys or any(k.dataset == "instrument_metadata" for k in keys):
        raise DataError("Research report requires a historical range and non-metadata datasets")
    # One catalog view, while immutable files can be read without blocking the live writer.
    with store.db:
        store.db.execute("BEGIN")
        ledgers = {key.id: dataset_ledger(store, key, start, end) for key in keys}
    core = [k for k in keys if k.dataset != "open_interest"]
    groups = {
        "spot": [k for k in core if k.market == "spot"],
        "price_funding": [k for k in core if k.dataset in {"trade_bars", "funding_events"}],
        "futures": [k for k in core if k.market == "linear_perpetual"],
        "core": core,
        "core_with_archive_oi": core + [k for k in keys if k.series == "archive"],
    }
    profiles = {}
    for name, members in groups.items():
        if not members or (name == "core_with_archive_oi" and len(members) == len(core)):
            continue
        blocked = [r for k in members for r in ledgers[k.id]["unavailable_ranges"]]
        grid = lcm(*(k.step or 1 for k in members))
        ranges = available_ranges(start, end, blocked, grid)
        oi = any(k.dataset == "open_interest" for k in members)
        profiles[name] = {
            "keys": [k.id for k in members],
            "complete": ranges == [[start, end]],
            "contiguous_ranges": ranges,
            "covered_days": sum((b - a).total_seconds() for a, b in ranges) / 86400,
            "longest_range": max(ranges, key=lambda r: r[1] - r[0]) if ranges else None,
            "availability_mode": "historical_assumption",
            "oi_clock_and_units_verified": False if oi else None,
            "interpretation": "Label coverage only; archive OI clock, units and publication timing remain unverified"
            if oi
            else "Complete bar grids and funding source-query coverage; not point-in-time vintage reconstruction",
        }
    return {
        "checked_at": now(),
        "start": start,
        "end": end,
        "datasets": list(ledgers.values()),
        "profiles": profiles,
    }
