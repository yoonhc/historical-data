"""Check all selected canonical hashes, update-ID chains and application receive gaps."""

import json
import os
from collections import Counter

from audit import OUT, ROOT, ReadOnlyStore, save
from marketdata import depth
from marketdata.domain import json_bytes, now


def main():
    os.nice(10)
    reports = json.loads((OUT / "book_depth-reports.json").read_bytes())
    reader = ReadOnlyStore(ROOT)
    columns = [
        "sequence",
        "epoch",
        "kind",
        "received_at",
        "first_update_id",
        "final_update_id",
        "previous_update_id",
    ]
    result = {
        "started_at": now(),
        "complete": False,
        "sessions": [],
        "boundaries": [],
        "gaps_over_10_seconds": [],
        "max_gap_by_scope": {},
        "interval_histogram": {},
        "same_socket_id_chain_errors": [],
        "receive_time_regressions": [],
        "duplicate_id_updates": 0,
        "rows_examined": 0,
        "updates_examined": 0,
        "same_socket_chain_links": 0,
    }
    previous, histogram = None, Counter()
    try:
        for report in reports:
            cid, seq, updates = report["id"], 0, 0
            segments = reader.db.execute(
                "SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id", (cid,)
            ).fetchall()
            for segment in segments:
                table = reader._revision_table(segment, columns=columns)
                assert table.num_rows == segment["row_count"]
                for row in table.to_pylist():
                    assert row["sequence"] == seq
                    seq += 1
                    if row["kind"] != "update":
                        continue
                    updates += 1
                    current = {"collection_id": cid, **row}
                    if previous is not None:
                        seconds = (current["received_at"] - previous["received_at"]).total_seconds()
                        if previous["collection_id"] != cid:
                            scope = "hourly_process_boundary"
                        elif previous["epoch"] == current["epoch"]:
                            scope = "same_epoch"
                        elif all(
                            report["epochs"][n]["reason"] in depth.SAME_SOCKET_REASONS
                            for n in range(previous["epoch"] + 1, current["epoch"] + 1)
                        ):
                            scope = "same_socket_recovery_boundary"
                        else:
                            scope = "physical_socket_boundary"
                        entry = {
                            "scope": scope,
                            "seconds": seconds,
                            "before": previous,
                            "after": current,
                        }
                        histogram[
                            next(
                                (
                                    str(limit)
                                    for limit in (0.2, 0.5, 1, 2, 5, 10)
                                    if seconds <= limit
                                ),
                                ">10",
                            )
                        ] += 1
                        if (
                            scope not in result["max_gap_by_scope"]
                            or seconds > result["max_gap_by_scope"][scope]["seconds"]
                        ):
                            result["max_gap_by_scope"][scope] = entry
                        if scope != "same_epoch":
                            result["boundaries"].append(entry)
                        if seconds > 10:
                            result["gaps_over_10_seconds"].append(entry)
                        if seconds < 0:
                            result["receive_time_regressions"].append(entry)
                        if scope in ("same_epoch", "same_socket_recovery_boundary"):
                            result["same_socket_chain_links"] += 1
                            keys = ("first_update_id", "final_update_id", "previous_update_id")
                            duplicate_ids = all(current[k] == previous[k] for k in keys)
                            if duplicate_ids:
                                result["duplicate_id_updates"] += 1
                            elif not (
                                current["final_update_id"] > previous["final_update_id"]
                                and current["previous_update_id"] == previous["final_update_id"]
                            ):
                                result["same_socket_id_chain_errors"].append(entry)
                    previous = current
            assert updates == report["messages"] == report["persisted_messages"]
            assert seq == report["persisted_messages"] + report["snapshots"]
            result["rows_examined"] += seq
            result["updates_examined"] += updates
            result["sessions"].append(
                {
                    "id": cid,
                    "rows": seq,
                    "updates": updates,
                    "canonical_segments_hashed": len(segments),
                }
            )
            print(
                json_bytes(
                    {
                        "id": cid,
                        "checked_sessions": len(result["sessions"]),
                        "updates_so_far": result["updates_examined"],
                    }
                ).decode(),
                flush=True,
            )
    finally:
        reader.close()
    result.update(
        complete=True,
        finished_at=now(),
        interval_histogram=dict(histogram),
        canonical_hashes_verified=True,
        scope="all 24 selected sessions; canonical timing/ID columns and file hashes, not all raw price/quantity pairs; receipt times are application recv returns, not wire arrivals; physical socket gaps are not repaired",
    )
    save("receipt-continuity.json", result)
    assert not result["same_socket_id_chain_errors"] and not result["receive_time_regressions"]
    print(
        json_bytes(
            {
                "complete": True,
                "updates": result["updates_examined"],
                "gaps_over_10": len(result["gaps_over_10_seconds"]),
                "maxima": {k: v["seconds"] for k, v in result["max_gap_by_scope"].items()},
            }
        ).decode(),
        flush=True,
    )


if __name__ == "__main__":
    main()
