"""Audit the one actually partial post-activation capture without altering its verdict."""

import os
import runpy
from pathlib import Path

from marketdata.domain import json_bytes, now, sha256

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
IDENTITY = "2a26a4f6aec34730b2603a388633387a"


def main():
    os.nice(10)
    source = ROOT / "reports/baseline/depth-followup-2026-09-29/analyze.py"
    audit = runpy.run_path(str(source))["audit"]
    audit.__globals__["OUT"] = OUT
    reader_type = runpy.run_path(
        str(ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
    )["ReadOnlyStore"]
    reader = reader_type(ROOT)
    metadata = {
        "started_at": now(),
        "collection_id": IDENTITY,
        "analyzer_sha256": sha256(source.read_bytes()),
        "catalog_mode": "ro with live WAL",
    }
    try:
        result = audit(reader, IDENTITY)
        metadata.update(
            finished_at=now(),
            result={
                k: result[k]
                for k in (
                    "collection_id",
                    "state",
                    "rows_checked",
                    "price_quantity_pairs_checked",
                    "raw_to_canonical_equal",
                    "replay_matches_live",
                    "unreconstructed_messages",
                    "same_socket_chain_errors",
                )
            },
        )
        (OUT / "operating-failure-verification.json").write_bytes(json_bytes(metadata))
        print(json_bytes(metadata).decode(), flush=True)
    finally:
        reader.db.close()
        reader._files_db.close()


if __name__ == "__main__":
    main()
