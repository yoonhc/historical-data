"""Replay the three new book failures and compare every selected row with raw data.

Reuse the previously reviewed independent failure analyzer, directing its output
to this observation. Live catalogs are opened read-only and live WAL is honored.
No capture, checkpoint, quality verdict, or production source is changed.
"""

import hashlib
import json
import os
import runpy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
HELPER = ROOT / "reports/baseline/depth-followup-2026-09-29/analyze.py"
READER = ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py"
COLLECTIONS = [
    "079691756a6e496c909e9019ef4d34d8",
    "517f5709097f4383889425c292347475",
    "ac31dd2ebf954619911b26fd004e3309",
]


def main():
    os.nice(10)
    analyze = runpy.run_path(str(HELPER))["audit"]
    analyze.__globals__["OUT"] = OUT
    read_only_store = runpy.run_path(str(READER))["ReadOnlyStore"]
    reader = read_only_store(ROOT)
    result = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "collection_ids": COLLECTIONS,
        "selection": "all closed sessions with new lagged anchors or exhausted price coverage since the previous audit",
        "catalog_mode": "ro; live WAL honored; normal layout-lock file reads",
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [HELPER, READER, ROOT / "packages/marketdata/src/marketdata/depth.py"]
        },
        "complete": False,
        "completed_collections": [],
    }
    target = OUT / "depth-detail-verification.json"
    target.write_text(json.dumps(result, indent=2) + "\n")
    try:
        for identity in COLLECTIONS:
            audit = analyze(reader, identity)
            result["completed_collections"].append(
                {
                    k: audit[k]
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
                }
            )
            target.write_text(json.dumps(result, indent=2) + "\n")
        result.update(complete=True, finished_at=datetime.now(timezone.utc).isoformat())
        target.write_text(json.dumps(result, indent=2) + "\n")
    finally:
        reader.db.close()
        reader._files_db.close()


if __name__ == "__main__":
    main()
