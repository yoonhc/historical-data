"""Read the previously missing UTC day through the normal, checksum-verified reader."""

import json
import runpy
from datetime import datetime, timezone
from pathlib import Path

from marketdata.domain import Key, json_bytes, utc

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def main():
    read_only_store = runpy.run_path(
        str(ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
    )["ReadOnlyStore"]
    reader = read_only_store(ROOT)
    daily = json.loads((OUT / "daily-update-latest.json").read_bytes())
    keys = [Key(dataset) for dataset in ["trade_bars", "mark_price_bars", "index_price_bars"]]
    keys.append(Key("open_interest", interval="5m", series="archive"))
    recovered = []
    try:
        for key in keys:
            start, end = utc("2026-09-29"), utc("2026-09-30")
            rows = reader.read(key, start, end).to_pylist()
            coverage = reader.coverage(key, start, end)
            revisions = reader.revision_list(key, start, end)
            report = next(r for r in daily if r["key"] == key.id)
            published = next(r for r in report["results"] if r.get("date") == "2026-09-29")
            assert len(rows) == (1440 if key.interval == "1m" else 288)
            assert coverage["complete"] and len(revisions) == 1
            assert revisions[0]["id"] == published["revision"]
            recovered.append(
                {
                    "key": key.id,
                    "rows_read": len(rows),
                    "first_timestamp": rows[0]["timestamp"],
                    "last_timestamp": rows[-1]["timestamp"],
                    "coverage": coverage,
                    "active_revision": revisions[0],
                    "daily_published_record": published,
                    "canonical_checksum_verified_by_reader": True,
                }
            )
        oi = reader.coverage(
            Key("open_interest", interval="5m", series="rest"),
            utc("2026-10-01T06:00:00+00:00"),
            utc("2026-10-01T08:00:00+00:00"),
        )
        assert oi["complete"]
    finally:
        reader.db.close()
        reader._files_db.close()
    result = {
        "recorded_at": datetime.now(timezone.utc),
        "recovered_utc_day": "2026-09-29",
        "recovered": recovered,
        "still_waiting_from_daily_run": [
            {
                "key": r["key"],
                "coverage": r.get("coverage"),
                "errors": r.get("errors"),
                "waiting": [x for x in r.get("results", []) if x.get("state") == "waiting_source"],
            }
            for r in daily
            if not r["complete"]
        ],
        "oi_rest_network_period_coverage": oi,
        "oi_scope": "5-minute source-label coverage; not proof of uninterrupted reception or availability time",
        "network_fetches_or_recollection": False,
    }
    (OUT / "daily-readback.json").write_text(
        json.dumps(json.loads(json_bytes(result)), ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json_bytes(
            {
                "recovered_rows": sum(r["rows_read"] for r in recovered),
                "datasets": len(recovered),
                "oi_rest_coverage": oi,
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
