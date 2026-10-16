"""Reproducible OI parity, response-version and observed-availability reports."""

import csv
import io
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path

from .domain import DAY, DataError, Key, full_days, json_bytes, now, sha256, utc
from .normalize import normalize_rest
from .oi_probe import REST_OI
from .store import atomic_write

ARCHIVE_OI = Key("open_interest", interval="5m", series="archive")
SHIFTS = (-300, 0, 300)
STEP = timedelta(minutes=5)


def reference(row):
    return {
        k: row[k] for k in ("timestamp", "quantity", "notional_value", "source_id", "source_row")
    }


def parity_report(store, start, end, reference_shift=300):
    if reference_shift not in SHIFTS:
        raise DataError("Reference shift must be -300, 0 or 300 seconds")
    archive = {r["timestamp"]: r for r in store.read(ARCHIVE_OI, start, end).to_pylist()}
    rest = {r["timestamp"]: r for r in store.read(REST_OI, start - STEP, end + STEP).to_pylist()}
    issues = [
        dict(r)
        for r in store.db.execute(
            """SELECT key_id,day,kind,source_id,details FROM issues WHERE resolved=0 AND blocking=1
        AND key_id IN (?,?) AND day>=? AND day<=? ORDER BY day,id""",
            (
                ARCHIVE_OI.id,
                REST_OI.id,
                (start - STEP).date().isoformat(),
                (end + STEP).date().isoformat(),
            ),
        )
    ]
    blocked = {(r["key_id"], r["day"]) for r in issues}
    daily, anomalies = [], []
    for begin, stop in full_days(start, end):
        for shift in SHIFTS:
            counts = Counter(
                expected=288,
                comparable=0,
                exact=0,
                quantity_exact=0,
                value_exact=0,
                mismatch=0,
                missing_archive=0,
                missing_rest=0,
                blocked=0,
            )
            label = begin
            while label < stop:
                other = label + timedelta(seconds=shift)
                a, b = archive.get(label), rest.get(other)
                reason = None
                if (ARCHIVE_OI.id, label.date().isoformat()) in blocked or (
                    REST_OI.id,
                    other.date().isoformat(),
                ) in blocked:
                    reason = "blocked"
                elif a is None:
                    reason = "missing_archive"
                elif b is None:
                    reason = "missing_rest"
                else:
                    counts["comparable"] += 1
                    quantity, value = (
                        a["quantity"] == b["quantity"],
                        a["notional_value"] == b["notional_value"],
                    )
                    counts["quantity_exact"] += quantity
                    counts["value_exact"] += value
                    if quantity and value:
                        counts["exact"] += 1
                    else:
                        reason = "mismatch"
                if reason:
                    counts[reason] += 1
                    if shift == reference_shift:
                        anomalies.append(
                            {
                                "kind": f"parity_{reason}",
                                "archive_label": label,
                                "rest_label": other,
                                "shift_seconds": shift,
                                "archive": reference(a) if a else None,
                                "rest": reference(b) if b else None,
                            }
                        )
                label += STEP
            daily.append(
                {
                    "day": begin.date().isoformat(),
                    "shift_seconds": shift,
                    **counts,
                    "exact_rate": counts["exact"] / counts["comparable"]
                    if counts["comparable"]
                    else None,
                }
            )
    totals = []
    for shift in SHIFTS:
        items = [r for r in daily if r["shift_seconds"] == shift]
        summed = {
            k: sum(r[k] for r in items)
            for k in items[0]
            if k not in {"day", "shift_seconds", "exact_rate"}
        }
        totals.append(
            {
                "shift_seconds": shift,
                **summed,
                "exact_rate": summed["exact"] / summed["comparable"]
                if summed["comparable"]
                else None,
            }
        )
    revisions = [
        {k: r[k] for k in ("id", "key_id", "day", "file_sha")}
        for key, a, b in ((ARCHIVE_OI, start, end), (REST_OI, start - STEP, end + STEP))
        for r in store.revision_list(key, a, b)
    ]
    return {
        "daily": daily,
        "totals": totals,
        "anomalies": anomalies,
        "blocking_issues": issues,
        "input_revisions": revisions,
        "reference_shift_seconds": reference_shift,
        "reference_shift_semantics": "Audit hypothesis only; no timestamp is shifted in storage",
    }


def observation_report(receipts, label_start, as_of, *, late_seconds=60):
    """Each item supplies immutable raw rows and its original HTTP receipt.

    Brackets describe this endpoint/client's sampled responses. The conservative
    lower edge uses request START, never response end. They are not proof of a
    globally monotone publication schedule or a guarantee of live availability.
    """
    states, versions, events, first_seen = {}, {}, [], {}
    counts = Counter(
        receipts=0,
        valid_probes=0,
        invalid_probes=0,
        clock_rejected=0,
        collector_fetches=0,
        timing_eligible_probes=0,
        boundary_policy_excluded=0,
    )
    probe_times, timing_times, receipt_ids = [], [], []
    for item in receipts:
        stamp = utc(item["observed_at"])
        rows, probe = item["rows"], item.get("probe")
        pointer = {
            "fetch_id": item["fetch_id"],
            "source_id": item["source_id"],
            "observed_at": stamp,
        }
        receipt_ids.append(item["fetch_id"])
        counts["receipts"] += 1
        usable = rows is not None and (
            probe is None or (probe["state"] == "ok" and not probe.get("clock_flags"))
        )
        if usable:
            for row in rows:
                label = row["timestamp"]
                if not label_start <= label < as_of:
                    continue
                signature = (row["quantity"], row["notional_value"])
                previous = versions.get(label)
                evidence = {**pointer, **reference(row)}
                if previous is not None and previous[0] != signature:
                    events.append(
                        {
                            "kind": "rest_value_changed",
                            "label": label,
                            "before": previous[1],
                            "after": evidence,
                        }
                    )
                versions[label] = (signature, evidence)
                positive = {
                    **pointer,
                    "requested_at": utc(probe["requested_at"]) if probe else None,
                    "evidence_type": "probe" if probe else "collector",
                }
                first_seen.setdefault(label, positive)
                if label in states:
                    state = states[label]
                    if state["first"] is None:
                        state["first"] = positive
                        state["preceding_absent"] = state["last_absent"]
                    elif state["was_present"] is False:
                        events.append({"kind": "rest_reappeared", "label": label, **positive})
                    state["was_present"] = True
        if probe is None:
            counts["collector_fetches"] += 1
            if item.get("error"):
                events.append(
                    {"kind": "invalid_legacy_response", **pointer, "error": item["error"]}
                )
            continue
        requested = utc(probe["requested_at"])
        probe_times.append(requested)
        if probe["state"] != "ok" or rows is None:
            counts["invalid_probes"] += 1
            events.append(
                {
                    "kind": "probe_failure",
                    **pointer,
                    "state": probe["state"],
                    "error": probe.get("error", item.get("error")),
                    "status": probe.get("status"),
                }
            )
            continue
        if probe.get("clock_flags"):
            counts["clock_rejected"] += 1
            events.append(
                {"kind": "probe_clock_rejected", **pointer, "flags": probe["clock_flags"]}
            )
            continue
        counts["valid_probes"] += 1
        if probe.get("version") != 2:
            # A returned row is presence evidence, but v1's endTime equalled the
            # newest label. Its absence must not establish an availability bound.
            counts["boundary_policy_excluded"] += 1
            continue
        counts["timing_eligible_probes"] += 1
        timing_times.append(requested)
        by_label = {r["timestamp"]: r for r in rows}
        label = utc(probe["query_start"])
        stop = min(utc(probe["query_end"]), as_of)
        while label < stop:
            if label < label_start or label > requested:
                label += STEP
                continue
            state = states.setdefault(
                label,
                {
                    "first": first_seen.get(label),
                    "last_absent": None,
                    "was_present": True if label in first_seen else None,
                },
            )
            present = label in by_label
            evidence = {**pointer, "requested_at": requested}
            if not present:
                if state["was_present"] is True:
                    events.append({"kind": "rest_disappeared", "label": label, **evidence})
                state["last_absent"] = evidence
            state["was_present"] = present
            label += STEP
    availability = []
    disappeared_labels = {e["label"] for e in events if e["kind"] == "rest_disappeared"}
    for label, state in sorted(states.items()):
        first = state["first"]
        absent = state.get("preceding_absent") if first else state["last_absent"]
        lower = absent["requested_at"] if absent else None
        upper = first["observed_at"] if first else None
        lower_delay = (lower - label).total_seconds() if lower else None
        upper_delay = (upper - label).total_seconds() if upper else None
        if first is None:
            status = "still_absent"
        elif absent is None:
            status = "already_present_at_first_probe"
        else:
            status = "bracketed"
        row = {
            "label": label,
            "state": status,
            "last_absent_request_at": lower,
            "first_present_received_at": upper,
            "lower_delay_seconds": lower_delay,
            "upper_delay_seconds": upper_delay,
            "bracket_width_seconds": (upper - lower).total_seconds() if upper and lower else None,
            "last_absent_fetch_id": absent["fetch_id"] if absent else None,
            "first_present_fetch_id": first["fetch_id"] if first else None,
            "last_absent_source_id": absent["source_id"] if absent else None,
            "first_present_source_id": first["source_id"] if first else None,
            "first_present_evidence_type": first["evidence_type"] if first else None,
            "subsequent_absence_detected": label in disappeared_labels,
            "observed_after_late_threshold": lower_delay is not None and lower_delay > late_seconds,
        }
        availability.append(row)
        if row["observed_after_late_threshold"]:
            events.append({"kind": "oi_late_or_still_absent", **row})
    probe_times.sort()
    gaps = [
        {"start": a, "end": b, "seconds": (b - a).total_seconds()}
        for a, b in zip(probe_times, probe_times[1:])
        if b - a > timedelta(seconds=45)
    ]
    age = (as_of - probe_times[-1]).total_seconds() if probe_times else None
    if age is not None and age > 60:
        gaps.append({"start": probe_times[-1], "end": as_of, "seconds": age})
    return {
        "counts": dict(counts),
        "availability": availability,
        "anomalies": events,
        "probe_start": probe_times[0] if probe_times else None,
        "probe_end": probe_times[-1] if probe_times else None,
        "timing_probe_start": min(timing_times) if timing_times else None,
        "timing_probe_end": max(timing_times) if timing_times else None,
        "observation_status": "not_started"
        if not timing_times
        else "stale"
        if age > 60
        else "active",
        "latest_probe_age_seconds": age,
        "probe_gaps_over_45_seconds": gaps,
        "late_threshold_seconds": late_seconds,
        "availability_states": dict(Counter(r["state"] for r in availability)),
        "input_fetch_ids": receipt_ids,
        "semantics": "Sampled response transitions, not global publication bounds. First presence without prior absence is left-censored. Successful collector receipts can prove presence, but only v2 timed probes provide absence evidence. Network errors are not absence. Local clock uncertainty and inconsistent or cached responses remain limitations.",
    }


def load_receipts(store, start, as_of, max_fetch_id):
    cache = {}
    query = store.db.execute(
        """SELECT id,artifact_id,observed_at,metadata FROM fetches
        WHERE observed_at>=? AND observed_at<=? AND id<=?
        AND url LIKE 'https://fapi.binance.com/futures/data/openInterestHist?%'
        ORDER BY observed_at,id""",
        ((start - STEP).isoformat(), as_of.isoformat(), max_fetch_id),
    )
    for record in query:
        metadata = json.loads(record["metadata"])
        probe = metadata.get("oi_probe")
        identity = record["artifact_id"]
        error, rows = None, None
        if (probe and probe["state"] == "ok") or (not probe and metadata.get("status") == 200):
            if identity not in cache:
                body, _ = store.raw(identity)
                try:
                    batch = normalize_rest(body, REST_OI, identity, utc(record["observed_at"]))
                    if batch.quarantine:
                        raise DataError("Zero OI in raw response")
                    cache[identity] = (body, batch.rows, None)
                except (DataError, ValueError, KeyError, TypeError) as exc:
                    cache[identity] = (body, None, str(exc))
            body, rows, error = cache[identity]
            if probe and rows is not None:
                # Recheck query bounds against the immutable response, even on repeated bodies.
                begin, end = utc(probe["query_start"]), utc(probe["query_end"])
                if any(not begin <= r["timestamp"] < end for r in rows):
                    rows, error = None, "Probe response outside query interval"
        elif not probe:
            continue
        yield {
            "fetch_id": record["id"],
            "source_id": identity,
            "observed_at": record["observed_at"],
            "probe": probe,
            "rows": rows,
            "error": error,
        }


def quality_report(store, start, end, *, as_of=None, reference_shift=300):
    start, end, as_of = utc(start), utc(end), utc(as_of) if as_of else now()
    list(full_days(start, end))
    if end > as_of.replace(hour=0, minute=0, second=0, microsecond=0) or end - start > 31 * DAY:
        raise DataError("OI comparison requires 1..31 completed UTC days")
    with store.db:
        store.db.execute("BEGIN")
        high = store.db.execute("SELECT COALESCE(MAX(id),0) FROM fetches").fetchone()[0]
        parity = parity_report(store, start, end, reference_shift)
        observed = observation_report(load_receipts(store, start, as_of, high), start, as_of)
    anomalies = parity["anomalies"] + observed["anomalies"]
    review = bool(
        anomalies
        or observed["probe_gaps_over_45_seconds"]
        or not observed["counts"]["timing_eligible_probes"]
    )
    report = {
        "format_version": 1,
        "generated_at": as_of,
        "comparison_start": start,
        "comparison_end": end,
        "observation_label_start": start,
        "observation_as_of": as_of,
        "catalog_max_fetch_id": high,
        "needs_review": review,
        "parity": parity,
        "observations": observed,
        "unit_status": "Quantity and value units remain unverified; comparison is exact source-number parity only",
    }
    return {"report_id": sha256(json_bytes(report)), **report}


def csv_bytes(rows):
    if not rows:
        return b""
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in row.items()}
        )
    return stream.getvalue().encode()


def write_report(report, directory):
    directory = Path(directory)
    name = utc(report["generated_at"]).strftime("%Y%m%dT%H%M%S") + "-" + report["report_id"][:12]
    output = directory / name
    files = {
        "report.json": json_bytes(report),
        "parity.csv": csv_bytes(report["parity"]["daily"]),
        "availability.csv": csv_bytes(report["observations"]["availability"]),
        "anomalies.jsonl": b"".join(
            json_bytes(r) + b"\n"
            for r in report["parity"]["anomalies"] + report["observations"]["anomalies"]
        ),
    }
    for name, body in files.items():
        path = output / name
        if path.exists() and path.read_bytes() != body:
            raise DataError("Existing OI report differs; refusing to overwrite evidence")
        if not path.exists():
            atomic_write(path, body)
    # Publish a pointer only after every artifact is complete.
    atomic_write(
        directory / "latest.json",
        json_bytes(
            {
                "report_id": report["report_id"],
                "path": str(output),
                "needs_review": report["needs_review"],
                "generated_at": report["generated_at"],
                "files": {name: sha256(body) for name, body in files.items()},
            }
        ),
    )
    return output
