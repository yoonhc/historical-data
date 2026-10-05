"""Compare frozen post-retirement journal with actual timer receipts; no service writes."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
START = "2026-10-04T18:22:21.322436+00:00"
END = "2026-10-04T23:54:00+00:00"


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    journal = [
        json.loads(line) for line in (OUT / "post-service-journal.jsonl").read_text().splitlines()
    ]
    expected_ids = sorted(s["id"] for s in read(OUT / "live-snapshots-after.json"))
    copied = OUT / "scheduled-receipts"
    copied.mkdir(exist_ok=False)
    results = []
    for source in sorted((ROOT / "reports/restic-archive-runs/scheduled").glob("*.json")):
        receipt = read(source)
        if not START < receipt["started_at"] < END:
            continue
        context = receipt["context"]
        archived = receipt["archive"]
        assert receipt["ok"] and archived["ok"]
        assert context["scheduled"] and context["service_verified"]
        assert context["classification"] == "verified_timer"
        assert context["provenance_method"] == "current_start_job_activation_details"
        details = context["start_job"]["activation_details"]
        assert details["trigger_unit"] == "marketdata-restic-archive.timer"
        actual_trigger = datetime.fromtimestamp(
            int(details["trigger_timer_realtime_usec"]) / 1_000_000, UTC
        ).isoformat()
        assert actual_trigger == context["trigger_at"]
        assert archived["result"]["processed"] == 0
        assert sorted(archived["result"]["stored_snapshots"]) == expected_ids
        assert not receipt["counted_dates"]
        assert receipt["progress"]["qualified_dates"] == ["2026-09-29", "2026-09-30", "2026-10-01"]
        archive_path = ROOT / "reports/restic-archive-runs" / archived["run_id"] / "run.json"
        assert read(archive_path) == archived
        invocation = context["service_properties"]["InvocationID"]
        matched = [
            (i + 1, item)
            for i, item in enumerate(journal)
            if item.get("_PID") == str(context["pid"])
            and item.get("_SYSTEMD_INVOCATION_ID") == invocation
            and item.get("_SYSTEMD_USER_UNIT") == "marketdata-restic-archive.service"
        ]
        assert len(matched) == 1
        assert json.loads(matched[0][1]["MESSAGE"]) == receipt
        completed = [
            i + 1
            for i, item in enumerate(journal)
            if item.get("USER_INVOCATION_ID") == invocation
            and item.get("MESSAGE", "").startswith("Finished marketdata-restic-archive.service")
        ]
        assert len(completed) == 1
        (copied / source.name).write_bytes(source.read_bytes())
        results.append(
            {
                "run_id": receipt["run_id"],
                "archive_run_id": archived["run_id"],
                "started_at": receipt["started_at"],
                "finished_at": receipt["finished_at"],
                "trigger_at": context["trigger_at"],
                "invocation_id": invocation,
                "pid": context["pid"],
                "receipt_sha256": digest(source),
                "archive_receipt_sha256": digest(archive_path),
                "journal_receipt_line": matched[0][0],
                "journal_completed_line": completed[0],
                "processed": 0,
                "ok": True,
            }
        )
    results.sort(key=lambda r: r["trigger_at"])
    assert len(results) == 11
    triggers = [datetime.fromisoformat(r["trigger_at"]) for r in results]
    assert all(abs((b - a).total_seconds() - 1800) < 1 for a, b in zip(triggers, triggers[1:]))
    value = {
        "at": datetime.now(UTC).isoformat(),
        "ok": True,
        "window_start_exclusive": START,
        "window_end_exclusive": END,
        "scheduled_runs": len(results),
        "scheduled_processed_zero": len(results),
        "scheduled_new_checkpoints": 0,
        "stored_snapshot_ids": expected_ids,
        "observed_qualified_dates_unchanged": ["2026-09-29", "2026-09-30", "2026-10-01"],
        "journal_sha256": digest(OUT / "post-service-journal.jsonl"),
        "runs": results,
        "next_new_daily_flow": "Pending actual 2026-10-05 13:10 KST daily and its scheduled archive",
    }
    with (OUT / "post-scheduled-results.json").open("x") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
    print(
        json.dumps(
            {"ok": True, "scheduled_noop_runs": len(results), "snapshots": len(expected_ids)}
        )
    )


if __name__ == "__main__":
    main()
