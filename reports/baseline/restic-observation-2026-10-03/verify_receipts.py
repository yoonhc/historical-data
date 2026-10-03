"""Verify the completed daily retry and its scheduled restic archive from receipts."""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scripts.archive_backups import Profile, checkpoint, load_state  # noqa: E402
from scripts.restic_schedule import eligible_records, load, status  # noqa: E402

BACKUP = "e971e8b4b008fd5144f0ee6e8125ace882d2bdf1c6bf9f1fba23f0f5a1ac2e30"
SNAPSHOT = "d27a0a557e1795a4651132016491f1b2c6735e8181b71303f3adccd414f0f8ad"
DAILY = "d8159f8d97634a82a1b95887f4a4ddcf"


def save(name, value):
    with (OUT / name).open("x") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    profile = Profile.load(Path("/home/yoonhc/.config/marketdata/restic-archive.toml"))
    state, observation = load_state(profile), load(profile)
    current = status(profile)
    record = state["checkpoints"][BACKUP]
    assert record["stored"]["snapshot_id"] == SNAPSHOT
    assert checkpoint(Path(record["entry"]["path"]), profile) == record["entry"]
    journal = [
        json.loads(line)
        for line in (OUT / "scheduled-and-daily-journal-full.jsonl").read_text().splitlines()
    ]
    receipts = [
        json.loads(Path(r["report"]).read_bytes())
        for r in observation["runs"]
        if "2026-10-03T04:00:00" <= r["started_at"] < "2026-10-03T07:08:00"
    ]
    processed = [r for r in receipts if r.get("archive", {}).get("result", {}).get("processed")]
    assert len(processed) == 1
    scheduled = processed[0]
    archive = json.loads(
        (profile.reports_root / scheduled["archive"]["run_id"] / "run.json").read_bytes()
    )
    assert scheduled["archive"] == archive
    evidence = eligible_records(observation, scheduled["context"], archive, state)
    assert len(evidence) == 1 and evidence[0]["backup_id"] == BACKUP
    assert evidence[0]["snapshot_id"] == SNAPSHOT and evidence[0]["date_utc"] == "2026-10-03"

    checked_runs = []
    for receipt in receipts:
        assert receipt["ok"] and receipt["context"]["classification"] == "verified_timer"
        matches = [e for e in journal if receipt["run_id"] in str(e.get("MESSAGE", ""))]
        assert len(matches) == 1 and json.loads(matches[0]["MESSAGE"]) == receipt
        assert matches[0]["_PID"] == str(receipt["context"]["pid"])
        assert (
            matches[0]["_SYSTEMD_INVOCATION_ID"]
            == receipt["context"]["environment"]["INVOCATION_ID"]
        )
        job = receipt["context"]["start_job"]["id"]
        assert any(e.get("JOB_ID") == str(job) and e.get("JOB_RESULT") == "done" for e in journal)
        checked_runs.append(
            {
                "run_id": receipt["run_id"],
                "started_at": receipt["started_at"],
                "finished_at": receipt["finished_at"],
                "ok": receipt["ok"],
                "job_id": job,
                "classification": receipt["context"]["classification"],
                "processed": receipt["archive"]["result"]["processed"],
                "counted_dates": receipt["counted_dates"],
                "journal_result_pid_invocation_and_finished_job_match": True,
            }
        )
    assert len(checked_runs) == 7
    daily = json.loads((ROOT / f"reports/maintenance/2026-10-03/{DAILY}.json").read_bytes())
    first = json.loads(
        (ROOT / "reports/maintenance/2026-10-03/3f90bd386efa4d9f8b4faf6edf4d0485.json").read_bytes()
    )
    assert first["state"] == "failed" and first["error"].startswith("WriterBusy:")
    assert daily["state"] == "complete" and daily["ok"] and daily["backup"]["ok"]
    assert daily["backup"]["backup_id"] == BACKUP
    assert daily["validation"]["ok"] and not daily["validation"]["failures"]
    phases = {}
    final_matches = []
    for item in journal:
        try:
            message = json.loads(item.get("MESSAGE") or "")
        except (ValueError, TypeError):
            continue
        if isinstance(message, dict) and message.get("id") == DAILY:
            phases[message.get("phase")] = int(item["__REALTIME_TIMESTAMP"]) / 1_000_000
            if message.get("phase") == "finished":
                final_matches.append(message)
    assert final_matches == [daily]
    assert any(
        "Scheduled restart job, restart counter is at 1" in str(e.get("MESSAGE")) for e in journal
    )
    previous = json.loads((OUT.parent / "restic-observation-2026-10-01/status.json").read_bytes())
    assert current["observation"]["date_evidence"] == previous["observation"]["date_evidence"]
    assert current["observation"]["restore"] == previous["observation"]["restore"]
    assert current["observation"]["completed_dates"] == 3 and current["observation"]["complete"]
    assert len(state["restore_checks"]) == 1
    assert current["archive"]["healthy"] and current["archive"]["pending_count"] == 0
    assert current["archive"]["unchecked_snapshot_ids"] == []
    check = state["repository_check"]
    assert check["ok"] and check["read_data"] and SNAPSHOT in check["snapshot_ids"]
    attempt = next(a for a in record["attempts"] if a.get("snapshot_id") == SNAPSHOT)
    for label, command in (("backup", attempt["command"]), ("check", check["receipt"])):
        assert command["returncode"] == 0
        save(f"restic-{label}-command.json", command)
        for extension in ("stdout", "stderr"):
            with (OUT / f"restic-{label}.{extension}").open("xb") as handle:
                handle.write(Path(command[extension]).read_bytes())
    backup_summary = [
        json.loads(line) for line in (OUT / "restic-backup.stdout").read_text().splitlines()
    ]
    backup_summary = next(r for r in backup_summary if r.get("message_type") == "summary")
    assert backup_summary["snapshot_id"] == SNAPSHOT
    check_summary = json.loads((OUT / "restic-check.stdout").read_text())
    assert check_summary["num_errors"] == 0 and not check_summary["broken_packs"]
    save("maintenance-daily-final.json", daily)
    save("maintenance-first-failure.json", first)
    save("scheduled-receipt.json", scheduled)
    save("archive-run.json", archive)
    save(
        "archive-proof.json",
        {"checkpoint": record, "repository_check": check, "eligible_records_recomputed": evidence},
    )
    save("status.json", current)
    save("scheduled-runs.json", checked_runs)
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "backup_id": BACKUP,
        "snapshot_id": SNAPSHOT,
        "daily_retry_id": DAILY,
        "daily_complete": True,
        "daily_started_at": daily["started_at"],
        "daily_finished_at": daily["finished_at"],
        "daily_wall_seconds": (
            datetime.fromisoformat(daily["finished_at"])
            - datetime.fromisoformat(daily["started_at"])
        ).total_seconds(),
        "daily_validation": daily["validation"],
        "validation_phase_seconds_from_journal": phases["backup"] - phases["validate"],
        "stored_and_read_data_checked": True,
        "check_summary": check_summary,
        "backup_summary": backup_summary,
        "scheduled_runs_checked": len(checked_runs),
        "scheduled_processed_runs": len(processed),
        "scheduled_no_op_runs": sum(r["processed"] == 0 for r in checked_runs),
        "observation_remains_three_of_three": True,
        "prior_restore_proof_unchanged": True,
        "manifest_and_original_source_root_verified": True,
        "journal_daily_and_scheduled_receipts_match": True,
        "full_integrity_or_restic_check_rerun": False,
        "restore_rerun": False,
        "service_or_retention_changes": False,
        "evidence_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(OUT.iterdir())
            if p.is_file()
        },
    }
    save("results.json", result)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("backup_summary", "evidence_sha256")},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
