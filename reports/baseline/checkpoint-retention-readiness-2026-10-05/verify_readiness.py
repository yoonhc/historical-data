"""Verify the first new scheduled checkpoint after the final retention-code install."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))

from marketdata.domain import json_bytes, sha256  # noqa: E402

from scripts import archive_backups as archive  # noqa: E402
from scripts import checkpoint_code, restic_schedule  # noqa: E402

BACKUP = "70fc23fed2fdba282d5a1adfbd168df8666077f4b4f99984ce56831b74c48b9f"
SNAPSHOT = "057cd08adc8e07dda2a93eb97f4ecfacce76a60971beb5c0d795566d1202f57b"
DAILY = "458bb13de2c14543828ce0225f07d6de"


def read(path):
    return json.loads(path.read_bytes())


def save(name, value):
    with (OUT / name).open("xb") as handle:
        handle.write(json_bytes(value))


def main():
    code = checkpoint_code.current_code()
    assert code["git_commit"] == "c2d02e784b0456560ec734b9ef88f4d960dc6b78"
    assert code["source_dirty"] is False
    installed = read(OUT.parent / "checkpoint-retention-guards-2026-10-04/source-install.json")
    prior = read(OUT.parent / "checkpoint-retention-2026-10-03/source-install.json")
    source_matches = {}
    for name, entry in {**prior["files"], **installed["files"]}.items():
        if name in code["source_files"]:
            source_matches[name] = sha256((ROOT / name).read_bytes()) == entry["sha256"]
    assert source_matches and all(source_matches.values())
    profile = archive.Profile.load(Path("/home/yoonhc/.config/marketdata/restic-archive.toml"))
    state = archive.load_state(profile)
    observation = restic_schedule.load(profile)
    current = archive.overview(profile)
    assert current["healthy"] and current["pending_count"] == 0
    assert not current["active_operation_record"] and not current["unchecked_snapshot_ids"]
    record = state["checkpoints"][BACKUP]
    assert record["stored"]["snapshot_id"] == SNAPSHOT
    assert archive.checkpoint(Path(record["entry"]["path"]), profile) == record["entry"]
    daily = read(ROOT / f"reports/maintenance/2026-10-04/{DAILY}.json")
    assert daily["ok"] and daily["state"] == "complete" and daily["backup"]["ok"]
    assert daily["backup"]["backup_id"] == BACKUP
    assert daily["validation"]["ok"] and daily["validation"]["failures"] == []
    assert archive.timestamp(daily["started_at"]) > archive.timestamp(installed["finished_at"])
    schedules = []
    for path in sorted((profile.reports_root / "scheduled").glob("*.json")):
        receipt = read(path)
        if archive.timestamp(receipt["started_at"]) > archive.timestamp(installed["finished_at"]):
            schedules.append((path, receipt))
    schedules.sort(key=lambda row: row[1]["started_at"])
    assert all(r["ok"] and r["context"]["classification"] == "verified_timer" for _, r in schedules)
    positive = [(p, r) for p, r in schedules if r["archive"]["result"]["processed"] > 0]
    assert len(positive) == 1
    _, scheduled = positive[0]
    run = read(profile.reports_root / scheduled["archive"]["run_id"] / "run.json")
    assert run == scheduled["archive"] and run["result"]["processed"] == 1
    eligible = restic_schedule.eligible_records(observation, scheduled["context"], run, state)
    assert len(eligible) == 1 and eligible[0]["backup_id"] == BACKUP
    assert eligible[0]["snapshot_id"] == SNAPSHOT
    assert len(record["attempts"]) == 1 and record["attempts"][0]["status"] == "stored"
    journal = [
        json.loads(line) for line in (OUT / "service-journal.jsonl").read_text().splitlines()
    ]
    messages = []
    for item in journal:
        try:
            msg = json.loads(item["MESSAGE"])
        except (KeyError, ValueError, TypeError):
            continue
        if isinstance(msg, dict):
            messages.append((item, msg))
    daily_rows = [(item, msg) for item, msg in messages if msg.get("id") == DAILY]
    daily_final = [(item, msg) for item, msg in daily_rows if msg.get("phase") == "finished"]
    assert len(daily_final) == 1 and daily_final[0][1] == daily
    daily_meta = daily_final[0][0]
    assert daily_meta["_SYSTEMD_USER_UNIT"] == "marketdata-backup.service"
    assert str(ROOT / ".venv/bin/marketdata") in daily_meta["_CMDLINE"]
    phases = {msg["phase"]: int(item["__REALTIME_TIMESTAMP"]) / 1e6 for item, msg in daily_rows}
    matches = [(item, msg) for item, msg in messages if msg.get("run_id") == scheduled["run_id"]]
    assert len(matches) == 1 and matches[0][1] == scheduled
    meta = matches[0][0]
    assert meta["_PID"] == str(scheduled["context"]["pid"])
    assert meta["_SYSTEMD_INVOCATION_ID"] == scheduled["context"]["environment"]["INVOCATION_ID"]
    job = scheduled["context"]["start_job"]["id"]
    assert any(e.get("JOB_ID") == str(job) and e.get("JOB_RESULT") == "done" for e in journal)
    check = state["repository_check"]
    assert check["ok"] and check["read_data"] and SNAPSHOT in check["snapshot_ids"]
    for label, command in (
        ("backup", record["attempts"][0]["command"]),
        ("check", check["receipt"]),
    ):
        assert command["returncode"] == 0
        save(f"restic-{label}-command.json", command)
        for extension in ("stdout", "stderr"):
            with (OUT / f"restic-{label}.{extension}").open("xb") as handle:
                handle.write(Path(command[extension]).read_bytes())
    summaries = [
        json.loads(line) for line in (OUT / "restic-backup.stdout").read_text().splitlines()
    ]
    summary = next(r for r in summaries if r.get("message_type") == "summary")
    assert summary["snapshot_id"] == SNAPSHOT
    checked = read(OUT / "restic-check.stdout")
    assert checked["num_errors"] == 0 and not checked["broken_packs"]
    previous = read(OUT.parent / "restic-observation-2026-10-03/status.json")["observation"]
    progress = restic_schedule.status(profile)["observation"]
    assert progress["date_evidence"] == previous["date_evidence"]
    assert progress["restore"] == previous["restore"]
    assert progress["completed_dates"] == 3
    save("maintenance-daily.json", daily)
    save("scheduled-receipt.json", scheduled)
    save("archive-run.json", run)
    save("archive-proof.json", {"record": record, "check": check, "eligible_records": eligible})
    save("status.json", current)
    save(
        "scheduled-runs.json",
        [
            {
                "path": str(p),
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "scheduled": r["context"]["scheduled"],
                "ok": r["ok"],
                "processed": r["archive"]["result"]["processed"],
            }
            for p, r in schedules
        ],
    )
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "ok": True,
        "code": code,
        "installed_at": installed["finished_at"],
        "source_matches": source_matches,
        "daily_id": DAILY,
        "backup_id": BACKUP,
        "snapshot_id": SNAPSHOT,
        "daily_started_at": daily["started_at"],
        "daily_finished_at": daily["finished_at"],
        "validation": daily["validation"],
        "logical_validation_seconds": phases["backup"] - phases["validate"],
        "daily_journal_pid": daily_meta["_PID"],
        "daily_journal_command": daily_meta["_CMDLINE"],
        "new_scheduled_flow_after_final_install": True,
        "journal_and_receipts_match": True,
        "scheduled_runs_checked": len(schedules),
        "processed_runs": 1,
        "no_op_runs": len(schedules) - 1,
        "observation_remains_three_of_three": True,
        "backup_summary": summary,
        "check_summary": checked,
        "repository_metadata_live_check_pending": True,
        "full_integrity_or_restic_check_rerun": False,
        "restore_rerun": False,
        "service_changes": False,
        "operational_deletions": 0,
    }
    save("results.json", result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in [
                    "ok",
                    "daily_finished_at",
                    "validation",
                    "logical_validation_seconds",
                    "scheduled_runs_checked",
                    "no_op_runs",
                    "new_scheduled_flow_after_final_install",
                    "journal_and_receipts_match",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
