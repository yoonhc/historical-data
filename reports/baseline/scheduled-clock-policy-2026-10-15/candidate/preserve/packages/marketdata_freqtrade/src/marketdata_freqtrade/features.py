"""Explicit causal alignment for additional strategy inputs (no pandas dependency)."""

from bisect import bisect_right
from datetime import timedelta

from marketdata.domain import DataError, utc


def align_open_interest(
    rows,
    decision_times,
    *,
    mode,
    max_age_seconds,
    archive_shift_seconds=None,
    publication_lag_seconds=None,
):
    """Return the latest available OI for each decision time, or None.

    Historical mode models availability with an explicitly supplied lag; it does
    not prove when the provider published a record. Observed mode uses our actual
    first receipt as well. A shift changes the source reference assumption only.
    Archive and REST series must be used separately. No unit conversion is made.
    """
    if mode not in {"historical_assumption", "observed"} or max_age_seconds < 0:
        raise DataError("Explicit availability mode and nonnegative maximum age required")
    if publication_lag_seconds is None or publication_lag_seconds < 0:
        raise DataError("An explicit nonnegative publication lag assumption is required")
    semantics = {row["time_semantics"] for row in rows}
    if len(semantics) > 1:
        raise DataError("Do not mix archive and REST OI")
    if semantics - {"archive_label_unverified", "rest_period_end"}:
        raise DataError("Unsupported OI time semantics")
    if "archive_label_unverified" in semantics and (
        archive_shift_seconds is None or archive_shift_seconds < 0
    ):
        raise DataError("Archive OI needs an explicit nonnegative clock-shift assumption")
    candidates = []
    for row in rows:
        if row["quantity"] <= 0 or row["notional_value"] <= 0:
            raise DataError("Zero/negative OI cannot be aligned")
        reference = (
            utc(row["timestamp"]) + timedelta(seconds=archive_shift_seconds or 0)
            if row["time_semantics"] == "archive_label_unverified"
            else utc(row["reference_time"])
        )
        available = reference + timedelta(seconds=publication_lag_seconds)
        if mode == "observed":
            available = max(available, utc(row["first_observed_at"]))
        candidates.append((available, reference, row))
    # Late arrival of an older record must not displace a newer reference time.
    candidates.sort(key=lambda item: (item[0], item[1]))
    times, latest, selected = [], [], None
    for available, reference, row in candidates:
        if selected is None or reference >= selected[0]:
            selected = (reference, row)
        times.append(available)
        latest.append(selected)
    output = []
    for decision in decision_times:
        decision = utc(decision)
        index = bisect_right(times, decision) - 1
        if index < 0:
            output.append(None)
            continue
        reference, row = latest[index]
        age = (decision - reference).total_seconds()
        output.append(
            {**row, "reference_time_assumed": reference, "age_seconds": age}
            if 0 <= age <= max_age_seconds
            else None
        )
    return output
