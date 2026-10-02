"""Freeze actual post-activation evidence; verify the explicit first closed capture."""

import json
import os
import runpy
from pathlib import Path

from marketdata import depth
from marketdata.domain import json_bytes, now, sha256

from scripts.verify_depth_capture import verify

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def save(name, result):
    (OUT / name).write_bytes(json_bytes(result))


def main():
    os.nice(10)
    activation = json.loads((OUT / "activation.json").read_bytes())
    assert activation["status"] == "observed"
    first = activation["new_collection_id"]
    started = activation["last_sample"]["started_at"]
    reader_type = runpy.run_path(
        str(ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
    )["ReadOnlyStore"]
    reader = reader_type(ROOT)
    result = {
        "observed_at": now(),
        "activation_collection_id": first,
        "activation_started_at": started,
        "closed": [],
        "excluded_running": [],
        "observation_24h_complete": False,
    }
    try:
        rows = reader.db.execute(
            "SELECT id,state,report FROM event_collections WHERE dataset='book_depth' "
            "AND kind='websocket' AND started_at>=? ORDER BY started_at",
            (started,),
        ).fetchall()
        for row in rows:
            report = json.loads(row["report"])
            assert report.get("coverage_refresh", {}).get("policy") == "bounded_headroom_v1"
            if row["state"] == "running":
                result["excluded_running"].append(row["id"])
                continue
            checks = [c for e in report["epochs"] for c in e.get("final", {}).get("checks", [])]
            result["closed"].append(
                {
                    "id": row["id"],
                    "state": row["state"],
                    "report_sha256": sha256(row["report"].encode()),
                    **{
                        k: report.get(k)
                        for k in (
                            "started_at",
                            "updated_at",
                            "messages",
                            "persisted_messages",
                            "connections",
                            "connection_errors",
                            "sync_losses",
                            "coverage_check_requests",
                            "periodic_check_requests",
                            "coverage_top_shortfalls",
                            "coverage_resets",
                            "lagged_snapshots",
                            "lagged_retry_requests",
                            "unreconstructed_messages",
                        )
                    },
                    "verification_checks": len(checks),
                    "verification_equal": sum(c["equal"] for c in checks),
                    "verification_uncomparable": sum(not c["comparable"] for c in checks),
                    "verification_mismatch": sum(
                        c["comparable"] and not c["equal"] for c in checks
                    ),
                    "verification_pending": sum(
                        e.get("final", {}).get("pending_check_sequence") is not None
                        for e in report["epochs"]
                    ),
                }
            )
        assert any(r["id"] == first for r in result["closed"])
        save("operating-closed-sessions.json", result)
        replay = depth.replay(reader, first)
        save("first-operating-replay.json", replay)
        verification = verify(reader, first, replayed=replay)
        save("first-operating-verification.json", verification)
        report = json.loads(depth.collection_record(reader, first)["report"])
        save("first-operating-capture.json", report)
        print(
            json.dumps(
                {"closed_sessions": len(result["closed"]), "first_verification": verification},
                default=str,
            )
        )
    finally:
        reader.db.close()
        reader._files_db.close()


if __name__ == "__main__":
    main()
