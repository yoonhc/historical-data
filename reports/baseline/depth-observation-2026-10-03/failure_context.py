"""Infer only provable gate constraints from persisted REST times; no invented requests."""

import csv
import json

from audit import OUT, ROOT, ReadOnlyStore, save
from marketdata.domain import json_bytes, now, utc


def main():
    selected = json.loads((OUT / "audit.json").read_bytes())
    reader = ReadOnlyStore(ROOT)
    results = []
    try:
        for cid in selected["new_partial_collection_ids"]:
            report = json.loads((OUT / f"{cid}.json").read_bytes())
            rows = []
            for row in reader.db.execute(
                "SELECT artifact_id,metadata FROM fetches WHERE json_extract(metadata,'$.collection_id')=?",
                (cid,),
            ):
                meta = json.loads(row["metadata"])
                if meta.get("kind") in ("anchor", "check"):
                    rows.append({"artifact_id": row["artifact_id"], **meta})
            rows.sort(key=lambda r: r["sequence"])
            previous, certain_extras = None, []
            for row in rows:
                if previous is not None and row["kind"] == "check":
                    elapsed = (
                        utc(row["request_started_at"]) - utc(previous["received_at"])
                    ).total_seconds()
                    if 0 <= elapsed < 10:
                        certain_extras.append(
                            {
                                "sequence": row["sequence"],
                                "epoch": row["epoch"],
                                "source_id": row["artifact_id"],
                                "request_at": row["request_started_at"],
                                "response_at": row["received_at"],
                                "since_previous_response_seconds": elapsed,
                            }
                        )
                previous = row
            for case in report["failures"]:
                if case["failure_type"] not in ("CoverageExhausted", "IncompleteTop"):
                    continue
                failure_time = utc(case["failure"]["received_at"])
                last = case["last_refreshed_snapshot"]
                trace = list(csv.DictReader((OUT / case["trace"]).open()))
                signals = [
                    r
                    for r in trace
                    if r["kind"] == "update"
                    and int(r["sequence"]) > last["sequence"]
                    and int(r["refreshes"]) == case["before"]["refreshes"]
                    and min(int(r["bid_levels"]), int(r["ask_levels"])) < 750
                ]
                signal = signals[0] if signals else None
                at = utc(signal["received_at"]) if signal else failure_time
                extra_at_signal = [
                    r
                    for r in certain_extras
                    if 0 <= (at - utc(r["request_at"])).total_seconds() < 60
                ]
                extra_at_failure = [
                    r
                    for r in certain_extras
                    if 0 <= (failure_time - utc(r["request_at"])).total_seconds() < 60
                ]
                assert len(extra_at_failure) <= 12 and len(extra_at_signal) <= 12
                since_response = (failure_time - utc(last["received_at"])).total_seconds()
                results.append(
                    {
                        "collection_id": cid,
                        "epoch": case["epoch"],
                        "type": case["failure_type"],
                        "failure_received_at": failure_time,
                        "known_level_change": case["independent_raw_change"],
                        "last_adopted_snapshot": last,
                        "response_to_failure_seconds": since_response,
                        "minimum_interval_not_elapsed": 0 <= since_response < 1,
                        "first_level_warning_after_last_adoption": signal,
                        "warning_to_failure_seconds": (failure_time - at).total_seconds()
                        if signal
                        else None,
                        "certain_extra_requests_in_60s_at_warning": extra_at_signal,
                        "certain_extra_requests_in_60s_at_failure": extra_at_failure,
                        "extra_cap_definitely_used_at_warning": len(extra_at_signal) == 12,
                        "extra_cap_definitely_used_at_failure": len(extra_at_failure) == 12,
                        "snapshot_response_minus_transaction_time_seconds": (
                            utc(last["received_at"]) - utc(last["timestamp"])
                        ).total_seconds(),
                        "snapshot_response_minus_event_time_seconds": (
                            utc(last["received_at"]) - utc(last["event_time"])
                        ).total_seconds(),
                        "failure_receive_minus_event_time_seconds": (
                            failure_time - utc(case["failure"]["event_time"])
                        ).total_seconds(),
                    }
                )
    finally:
        reader.close()
    result = {
        "checked_at": now(),
        "cases": results,
        "inference_not_direct_gate_log": True,
        "method": "Requests starting less than 10 seconds after the previous REST response cannot be periodic checks because processing completion occurs after that response. Counts are lower bounds on extra requests, assume no wall-clock step, and are not exact quota totals when below 12. A failure less than 1 second after the last adopted response is necessarily inside the minimum interval measured from processing completion. These constraints do not prove a hypothetical extra request would avoid failure.",
        "timestamp_scope": "T, E and local received_at have different meanings; their deltas do not isolate network or exchange latency",
    }
    save("failure-gate-evidence.json", result)
    print(
        json_bytes(
            {
                "cases": [
                    {
                        k: c[k]
                        for k in (
                            "collection_id",
                            "epoch",
                            "type",
                            "response_to_failure_seconds",
                            "minimum_interval_not_elapsed",
                            "extra_cap_definitely_used_at_warning",
                            "extra_cap_definitely_used_at_failure",
                            "warning_to_failure_seconds",
                        )
                    }
                    for c in results
                ]
            }
        ).decode(),
        flush=True,
    )


if __name__ == "__main__":
    main()
