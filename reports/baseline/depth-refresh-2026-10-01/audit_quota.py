"""Infer the exhausted extra-request budget from original request/response records."""

import csv
import json
import runpy
from pathlib import Path

from marketdata.domain import json_bytes, now, sha256, utc

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
IDENTITY = "2a26a4f6aec34730b2603a388633387a"


def main():
    reader_type = runpy.run_path(
        str(ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
    )["ReadOnlyStore"]
    reader = reader_type(ROOT)
    try:
        failure = json.loads((OUT / f"{IDENTITY}.json").read_bytes())["failures"][0]
        assert failure["failure_type"] == "CoverageExhausted"
        with (OUT / f"{IDENTITY}-epoch-0-trace.csv").open() as handle:
            trace = list(csv.DictReader(handle))
        last_snapshot = failure["last_refreshed_snapshot"]
        low = next(
            r
            for r in trace
            if int(r["sequence"]) > last_snapshot["sequence"]
            and r["kind"] == "update"
            and min(int(r["bid_levels"]), int(r["ask_levels"])) < 750
        )
        at = utc(low["received_at"])
        checks, previous = [], None
        for row in trace:
            if row["kind"] != "check":
                continue
            seq = int(row["sequence"])
            segment = reader.db.execute(
                "SELECT * FROM event_segments WHERE collection_id=? AND first_id=? AND last_id=?",
                (IDENTITY, seq, seq),
            ).fetchone()
            snapshot = reader._revision_table(segment).to_pylist()[0]
            metadata = next(
                json.loads(r[0])
                for r in reader.db.execute(
                    "SELECT metadata FROM fetches WHERE artifact_id=?", (snapshot["source_id"],)
                )
                if json.loads(r[0]).get("collection_id") == IDENTITY
                and json.loads(r[0]).get("sequence") == seq
            )
            request, response = utc(metadata["request_started_at"]), utc(metadata["received_at"])
            assert response == utc(row["received_at"])
            elapsed = (request - previous).total_seconds() if previous else None
            if 0 <= (at - request).total_seconds() < 60 and elapsed is not None and elapsed < 10:
                checks.append(
                    {
                        "sequence": seq,
                        "source_id": snapshot["source_id"],
                        "request_at": request,
                        "response_at": response,
                        "request_after_previous_response_seconds": elapsed,
                    }
                )
            previous = response
        assert len(checks) == 12 and not low["pending_check_sequence"]
        result = {
            "checked_at": now(),
            "collection_id": IDENTITY,
            "case": "coverage_exhausted epoch 0",
            "method": "Request metadata within the 60 seconds before the low-level signal; each request started less than 10 seconds after the preceding response, so it could not be a regular 10-second check. Inferred quota use, not a directly persisted deferred-request counter; assumes no intervening wall-clock step.",
            "low_level_signal": low,
            "additional_checks_in_previous_60_seconds": checks,
            "inferred_extra_budget_used": 12,
            "policy_extra_budget": 12,
            "seconds_from_low_signal_to_exhaustion": (
                utc(failure["failure"]["received_at"]) - at
            ).total_seconds(),
            "seconds_from_last_adopted_response_to_exhaustion": failure[
                "seconds_since_last_refresh_response"
            ],
            "first_extra_request_age_at_signal_seconds": (
                at - utc(checks[0]["request_at"])
            ).total_seconds(),
            "failure_audit_sha256": sha256((OUT / f"{IDENTITY}.json").read_bytes()),
            "conclusion": "All 12 extra slots were still occupied at the new low-level signal. The implemented cap therefore deferred a further early check; the finite price range then emptied. This does not establish that an extra REST response could have arrived/aligned in time.",
        }
        (OUT / "operating-quota-evidence.json").write_bytes(json_bytes(result))
        print(
            json_bytes(
                {
                    k: result[k]
                    for k in (
                        "inferred_extra_budget_used",
                        "seconds_from_low_signal_to_exhaustion",
                        "seconds_from_last_adopted_response_to_exhaustion",
                    )
                }
            ).decode()
        )
    finally:
        reader.db.close()
        reader._files_db.close()


if __name__ == "__main__":
    main()
