"""Check the five frozen activation request contexts against immutable original bytes."""

import hashlib
import json
import runpy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def main():
    reader_type = runpy.run_path(
        str(ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
    )["ReadOnlyStore"]
    reader = reader_type(ROOT)
    contexts = json.loads((OUT / "request-contexts.json").read_bytes())
    activation = json.loads((OUT / "activation.json").read_bytes())
    checked, pairs = [], 0
    try:
        for entry in contexts:
            assert entry["collection_id"] == activation["new_collection_id"]
            seq, cid, source = entry["sequence"], entry["collection_id"], entry["source_id"]
            metadata = [
                json.loads(r[0])
                for r in reader.db.execute(
                    "SELECT metadata FROM fetches WHERE artifact_id=?", (source,)
                )
            ]
            assert {k: v for k, v in entry.items() if k != "source_id"} in metadata
            segments = reader.db.execute(
                "SELECT * FROM event_segments WHERE collection_id=? AND first_id<=? AND last_id>=?",
                (cid, seq, seq),
            ).fetchall()
            assert len(segments) == 1
            rows = reader._revision_table(segments[0]).to_pylist()
            row = next(r for r in rows if r["sequence"] == seq)
            blob = reader.raw(source)[0]
            payload = json.loads(blob)
            assert hashlib.sha256(blob).hexdigest() == source
            assert row["source_id"] == source and row["kind"] == "check"
            assert row["final_update_id"] == payload["lastUpdateId"]
            assert row["epoch"] == entry["epoch"]
            for side in ("bids", "asks"):
                assert [(r["price"], r["quantity"]) for r in row[side]] == [
                    (Decimal(p), Decimal(q)) for p, q in payload[side]
                ]
                pairs += len(row[side])
            context = entry["refresh_context"]
            assert context["sequence"] == seq and context["epoch"] == row["epoch"]
            assert context["action"] == "request" and not context["blockers"]
            assert context["age_seconds"] >= 1
            assert context["budget_after_claim"]["extra_limit"] == 12
            assert 0 <= context["budget_after_claim"]["extra_used"] <= 12
            assert "reserved_for_critical" not in context["budget_after_claim"]
            checked.append({"sequence": seq, "source_id": source, "reason": context["reason"]})
        result = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "collection_id": activation["new_collection_id"],
            "requests_checked": len(checked),
            "price_quantity_pairs_checked": pairs,
            "frozen_metadata_matches_catalog": True,
            "original_sha256_and_canonical_values_equal": True,
            "requests": checked,
            "scope": "five frozen initial REST request contexts and snapshot rows; not full capture replay or later REST comparison verdicts",
            "catalog_mode": "read-only with live WAL; existing shared layout locks for files",
        }
        target = OUT / "operating-request-verification.json"
        with target.open("x") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        reader.db.close()
        reader._files_db.close()


if __name__ == "__main__":
    main()
