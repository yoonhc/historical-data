"""Recompute today's scheduled date credit from existing receipts, without running restic."""

import hashlib
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scripts.archive_backups import Profile, load_state  # noqa: E402
from scripts.restic_schedule import eligible_records, load  # noqa: E402


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    profile = Profile.load(Path("/home/yoonhc/.config/marketdata/restic-archive.toml"))
    observation, state = load(profile), load_state(profile)
    previous = json.loads((OUT.parent / "restic-observation-2026-09-29/results.json").read_bytes())
    status = json.loads((OUT / "status.json").read_bytes())
    evidence = observation["qualified_dates"]["2026-09-30"]
    record = state["checkpoints"][evidence["backup_id"]]
    archive_run = json.loads(
        (profile.reports_root / evidence["archive_run_id"] / "run.json").read_bytes()
    )
    receipts = [
        json.loads(Path(r["report"]).read_bytes())
        for r in observation["runs"]
        if r["started_at"] > previous["recorded_at"]
    ]
    qualifying = [r for r in receipts if "2026-09-30" in r["counted_dates"]]
    assert len(qualifying) == 1
    receipt = qualifying[0]
    assert receipt["archive"] == archive_run
    recomputed = eligible_records(observation, receipt["context"], archive_run, state)
    assert recomputed == [evidence]
    journal = [
        json.loads(line) for line in (OUT / "qualifying-journal.jsonl").read_text().splitlines()
    ]
    matching = [e for e in journal if receipt["run_id"] in (e.get("MESSAGE") or "")]
    assert len(matching) == 1
    entry = matching[0]
    assert json.loads(entry["MESSAGE"]) == receipt
    assert entry["_PID"] == str(receipt["context"]["pid"])
    assert entry["_SYSTEMD_INVOCATION_ID"] == evidence["invocation_id"]
    job = receipt["context"]["start_job"]["id"]
    assert any(e.get("JOB_ID") == str(job) and e.get("JOB_RESULT") == "done" for e in journal)
    manifest = Path(evidence["source_path"]) / "backup.json"
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == evidence["manifest_sha256"]
    assert state["repository_check"]["ok"] and state["repository_check"]["read_data"]
    assert evidence["snapshot_id"] in state["repository_check"]["snapshot_ids"]
    assert status["observation"]["restore"] == previous["observation"]["restore"]
    assert status["observation"]["restore"]["status"] == "passed"
    assert len(state["restore_checks"]) == previous["restore_checks_count"] == 1
    protected_hashes = json.loads(
        (OUT.parent / "restic-observation-2026-09-29/protected-hashes.json").read_bytes()
    )
    previous_checkpoint = previous["observation"]["date_evidence"]["2026-09-29"]
    protected_hashes[str(Path(previous_checkpoint["source_path"]) / "backup.json")] = (
        previous_checkpoint["manifest_sha256"]
    )
    protected = {
        p: hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
        for p, h in protected_hashes.items()
    }
    assert all(protected.values())
    source_hashes = {
        p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in previous["source_hashes"]
    }
    assert source_hashes == previous["source_hashes"]
    save("scheduled-receipt.json", receipt)
    save("archive-run.json", archive_run)
    save(
        "archive-proof.json",
        {
            "date_evidence": evidence,
            "checkpoint": record,
            "repository_check": state["repository_check"],
            "date_evidence_recomputed_matches": True,
        },
    )
    save(
        "scheduled-runs-since-previous.json",
        [
            {
                "run_id": r["run_id"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "ok": r["ok"],
                "classification": r["context"]["classification"],
                "processed": r.get("archive", {}).get("result", {}).get("processed"),
                "counted_dates": r["counted_dates"],
            }
            for r in receipts
        ],
    )
    stored = next(a for a in record["attempts"] if a.get("snapshot_id") == evidence["snapshot_id"])
    for label, command in (("backup", stored["command"]), ("check", evidence["check_receipt"])):
        save(f"restic-{label}-command.json", command)
        for extension in ("stdout", "stderr"):
            shutil.copy2(command[extension], OUT / f"restic-{label}.{extension}")
    daily = json.loads((OUT / "maintenance-daily-latest.json").read_bytes())
    phases = {}
    for line in (OUT / "daily-journal.jsonl").read_text().splitlines():
        item = json.loads(line)
        try:
            message = json.loads(item.get("MESSAGE") or "")
        except (ValueError, TypeError):
            continue
        if isinstance(message, dict) and message.get("id") == daily["id"]:
            phases[message.get("phase")] = int(item["__REALTIME_TIMESTAMP"]) / 1_000_000
    results = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "previous_recorded_at": previous["recorded_at"],
        "daily": {k: daily[k] for k in ("started_at", "finished_at", "validation", "backup")},
        "daily_wall_seconds": (
            datetime.fromisoformat(daily["finished_at"])
            - datetime.fromisoformat(daily["started_at"])
        ).total_seconds(),
        "daily_validation_phase_seconds": phases["backup"] - phases["validate"],
        "scheduled_runs": {
            "count": len(receipts),
            "classifications": dict(Counter(r["context"]["classification"] for r in receipts)),
            "failed": [r["run_id"] for r in receipts if not r["ok"]],
            "processed_runs": sum(
                r.get("archive", {}).get("result", {}).get("processed", 0) > 0 for r in receipts
            ),
            "no_op_runs": sum(
                r.get("archive", {}).get("result", {}).get("processed") == 0 for r in receipts
            ),
        },
        "completed_dates": status["observation"]["completed_dates"],
        "qualified_dates": status["observation"]["qualified_dates"],
        "remaining_dates": status["observation"]["remaining_dates"],
        "overall_complete": status["observation"]["complete"],
        "date_evidence_recomputed_matches": True,
        "journal_receipt_matches_file": True,
        "journal_pid_and_invocation_match": True,
        "journal_completed_job": job,
        "restore_proof_unchanged": True,
        "restore_checks_count": len(state["restore_checks"]),
        "protected_hash_matches": protected,
        "source_hashes": source_hashes,
        "full_check_or_restore_rerun_by_this_audit": False,
        "future_dates_counted": False,
        "services_changed_or_restarted": False,
        "retention_or_backup_replacement_changed": False,
    }
    save("results.json", results)
    print(
        json.dumps(
            {
                k: results[k]
                for k in (
                    "recorded_at",
                    "daily_wall_seconds",
                    "daily_validation_phase_seconds",
                    "scheduled_runs",
                    "completed_dates",
                    "remaining_dates",
                    "journal_completed_job",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
