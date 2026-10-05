"""Read-only evidence for the first new scheduled backup after checkpoint retirement.

Only report files and ordinary restic metadata-command receipts are written.
No archive run, full check, restore, retirement or service mutation is performed.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))

from marketdata.checkpoint_locks import checkpoint_locks  # noqa: E402
from marketdata.domain import json_bytes, sha256  # noqa: E402

from scripts import archive_backups as archive  # noqa: E402
from scripts import checkpoint_code, restic_schedule  # noqa: E402

DAILY = "7a1b697d9a434f43a32660760b4b8b26"
BACKUP = "46f4734b48f7e81c4ef805479c8d2c7b605215c6881b3d6b38a0a3a80d19bc1b"
SNAPSHOT = "19bde5fb81b7dde7d45e2bba3504a9bd7969c7b2d1414803a528a0b4f1f6d462"
SCHEDULED = "460689ce971341a1ab57e48b17a2382b"
PRIOR = OUT.parent / "checkpoint-retirement-apply-2026-10-05"
APPLY = PRIOR / "20261004T180904Z-be841c1fd08b493f8afb2e0d5dc2a0be"


def read(path):
    return json.loads(path.read_bytes())


def save(name, value):
    with (OUT / name).open("xb") as handle:
        handle.write(json_bytes(value))


def main():
    checked_at = datetime.now(UTC).isoformat()
    profile = archive.Profile.load(Path("/home/yoonhc/.config/marketdata/restic-archive.toml"))
    profile.guard()
    state = archive.load_state(profile)
    observation = restic_schedule.load(profile)
    current = archive.overview(profile)
    assert current["healthy"] and current["pending_count"] == 0
    assert not current["checkpoint_errors"] and not current["unchecked_snapshot_ids"]
    assert not current["active_operation_record"] and not current["lock_busy"]

    daily = read(ROOT / f"reports/maintenance/2026-10-05/{DAILY}.json")
    assert daily["ok"] and daily["state"] == "complete" and daily["backup"]["ok"]
    assert daily["backup"]["backup_id"] == BACKUP and not daily["backup"]["reused"]
    assert daily["validation"]["ok"] and daily["validation"]["failures"] == []
    after = read(APPLY / "after.json")
    assert archive.timestamp(daily["started_at"]) > archive.timestamp(after["at"])
    record = state["checkpoints"][BACKUP]
    assert record["stored"]["snapshot_id"] == SNAPSHOT
    entry = record["entry"]
    assert archive.checkpoint(Path(entry["path"]), profile) == entry
    assert len(record["attempts"]) == 1 and record["attempts"][0]["status"] == "stored"

    scheduled = read(profile.reports_root / "scheduled" / f"{SCHEDULED}.json")
    run = read(profile.reports_root / scheduled["archive"]["run_id"] / "run.json")
    assert run == scheduled["archive"] and run["ok"] and run["result"]["processed"] == 1
    context = scheduled["context"]
    assert context["scheduled"] and context["service_verified"]
    assert context["classification"] == "verified_timer"
    assert context["provenance_method"] == "current_start_job_activation_details"
    activation = context["start_job"]["activation_details"]
    assert activation["trigger_unit"] == "marketdata-restic-archive.timer"
    trigger = datetime.fromtimestamp(int(activation["trigger_timer_realtime_usec"]) / 1e6, UTC)
    assert trigger == archive.timestamp(context["trigger_at"])
    eligible = restic_schedule.eligible_records(observation, context, run, state)
    assert len(eligible) == 1 and eligible[0]["backup_id"] == BACKUP
    assert eligible[0]["snapshot_id"] == SNAPSHOT

    end = datetime.now(UTC)
    command = [
        "journalctl",
        "--user",
        "--since",
        "2026-10-05 04:00:00 UTC",
        "--until",
        end.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "-u",
        "marketdata-backup.service",
        "-u",
        "marketdata-restic-archive.service",
        "-u",
        "marketdata-pack.service",
        "--output=json",
        "--all",
        "--no-pager",
    ]
    journal_raw = subprocess.check_output(command)
    (OUT / "service-journal.jsonl").write_bytes(journal_raw)
    save("journal-command.json", {"command": command, "captured_at": end.isoformat()})
    journal = [json.loads(line) for line in journal_raw.splitlines()]
    messages = []
    for item in journal:
        try:
            message = json.loads(item["MESSAGE"])
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(message, dict):
            messages.append((item, message))
    daily_rows = [(item, msg) for item, msg in messages if msg.get("id") == DAILY]
    daily_final = [(item, msg) for item, msg in daily_rows if msg.get("phase") == "finished"]
    assert len(daily_final) == 1 and daily_final[0][1] == daily
    daily_meta = daily_final[0][0]
    assert daily_meta["_SYSTEMD_USER_UNIT"] == "marketdata-backup.service"
    assert str(ROOT / ".venv/bin/marketdata") in daily_meta["_CMDLINE"]
    phases = {msg["phase"]: int(item["__REALTIME_TIMESTAMP"]) / 1e6 for item, msg in daily_rows}
    matches = [(item, msg) for item, msg in messages if msg.get("run_id") == SCHEDULED]
    assert len(matches) == 1 and matches[0][1] == scheduled
    meta = matches[0][0]
    invocation = context["service_properties"]["InvocationID"]
    assert meta["_PID"] == str(context["pid"])
    assert meta["_SYSTEMD_INVOCATION_ID"] == invocation
    assert any(
        e.get("USER_INVOCATION_ID") == invocation
        and e.get("MESSAGE", "").startswith("Finished marketdata-restic-archive.service")
        for e in journal
    )

    check = state["repository_check"]
    assert check["ok"] and check["read_data"] and SNAPSHOT in check["snapshot_ids"]
    assert check["receipt"]["arguments"] == ["check", "--read-data"]
    for label, receipt in (
        ("backup", record["attempts"][0]["command"]),
        ("check", check["receipt"]),
    ):
        assert receipt["returncode"] == 0
        save(f"restic-{label}-command.json", receipt)
        for extension in ("stdout", "stderr"):
            (OUT / f"restic-{label}.{extension}").write_bytes(Path(receipt[extension]).read_bytes())
    summaries = [
        json.loads(line) for line in (OUT / "restic-backup.stdout").read_bytes().splitlines()
    ]
    summary = next(row for row in summaries if row.get("message_type") == "summary")
    assert summary["snapshot_id"] == SNAPSHOT
    checked = read(OUT / "restic-check.stdout")
    assert checked["num_errors"] == 0 and not checked["broken_packs"]
    verify_files = list((profile.reports_root / run["run_id"]).glob("*-verify-input.json"))
    assert len(verify_files) == 1
    verified = read(verify_files[0])
    assert verified["result"]["ok"] and verified["result"]["backup_id"] == BACKUP

    # Metadata only: use the existing UUID/repository/tag/path/time/manifest guards.
    client = archive.Archive(profile, OUT / "live-metadata")
    version = client.restic.identify()
    snapshots = client.restic.snapshots()
    actual = {s["id"]: s for s in snapshots}
    old = {s["id"]: s for s in read(PRIOR / "live-snapshots-after.json")}
    assert len(snapshots) == len(actual) == 9
    assert set(actual) - set(old) == {SNAPSHOT}
    assert all(actual[sid] == value for sid, value in old.items())
    assert len([s for s in snapshots if f"backup_id={BACKUP}" in s.get("tags", [])]) == 1
    assert client.match(actual[SNAPSHOT], entry, record["attempts"][0]["id"])
    assert set(check["snapshot_ids"]) == set(actual)

    paths = sorted(profile.backup_dir.glob("*/*/backup.json"))
    with checkpoint_locks([p.parent for p in paths]):
        manifests = {str(p): sha256(p.read_bytes()) for p in paths}
    assert len(manifests) == 7
    assert set(manifests) - set(after["remaining_manifests"]) == {
        str(Path(entry["path"]) / "backup.json")
    }
    assert all(manifests[p] == digest for p, digest in after["remaining_manifests"].items())
    previous = read(PRIOR / "post-status.json")
    prior_observation = read(OUT.parent / "restic-observation-2026-10-03/status.json")[
        "observation"
    ]
    progress = restic_schedule.progress(observation)
    assert progress["date_evidence"] == prior_observation["date_evidence"]
    assert progress["restore"] == prior_observation["restore"]
    assert progress["completed_dates"] == 3 and scheduled["counted_dates"] == []
    assert current["restore_checks"] == previous["restore_checks"]

    pack = read(ROOT / "state/maintenance-hourly-latest.json")
    assert pack["ok"] and pack["state"] == "complete"
    assert archive.timestamp(pack["started_at"]) >= datetime(2026, 10, 5, 5, 15, tzinfo=UTC)
    assert any(msg == pack for _, msg in messages)
    code = checkpoint_code.current_code()
    assert code["git_commit"] == "9d2d136e7aadd65c38937095daeb33f5211f94bf"
    assert code["source_dirty"] is False
    prior_code = read(PRIOR / "post-check-results.json")["code"]
    changed = sorted(
        k for k, digest in prior_code["source_files"].items() if code["source_files"][k] != digest
    )
    assert changed == [
        "packages/marketdata/src/marketdata/depth.py",
        "packages/marketdata/src/marketdata/depth_stream.py",
    ]
    for name, value in (
        ("maintenance-daily.json", daily),
        ("maintenance-hourly.json", pack),
        ("scheduled-receipt.json", scheduled),
        ("archive-run.json", run),
        ("archive-proof.json", {"record": record, "check": check, "eligible_records": eligible}),
        ("verify-input.json", verified),
        ("status.json", current),
        ("live-snapshots.json", snapshots),
        ("local-manifests.json", manifests),
    ):
        save(name, value)
    result = {
        "checked_at": checked_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "ok": True,
        "code": code,
        "source_changes_since_retirement": changed,
        "daily_id": DAILY,
        "backup_id": BACKUP,
        "snapshot_id": SNAPSHOT,
        "scheduled_run_id": SCHEDULED,
        "archive_run_id": run["run_id"],
        "daily_started_at": daily["started_at"],
        "daily_finished_at": daily["finished_at"],
        "validation": daily["validation"],
        "logical_validation_seconds": phases["backup"] - phases["validate"],
        "scheduled_processed": 1,
        "new_scheduled_flow_after_retirement": True,
        "journal_and_receipts_match": True,
        "environment_matches_job": context["environment_matches_job"],
        "provenance_method": context["provenance_method"],
        "backup_summary": summary,
        "check_summary": checked,
        "verify_input_seconds": verified["wall_seconds"],
        "repository_live_checked": True,
        "restic_version": version,
        "restic_snapshots": 9,
        "previous_eight_snapshots_unchanged": True,
        "new_snapshot_duplicates": 0,
        "local_checkpoints": 7,
        "previous_six_manifests_unchanged": True,
        "observation_remains_three_of_three": True,
        "existing_restore_proof_unchanged": True,
        "full_integrity_or_restic_check_rerun": False,
        "restore_rerun": False,
        "archive_run_started_by_this_audit": False,
        "service_changes": False,
        "operational_deletions": 0,
        "forget_or_prune_run": False,
        "depth_regular_session_followup": "pending; check after 2026-10-05 15:05 KST",
        "evidence_sha256": {
            str(p.relative_to(OUT)): sha256(p.read_bytes())
            for p in sorted(OUT.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts
        },
    }
    save("results.json", result)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "ok",
                    "backup_id",
                    "snapshot_id",
                    "logical_validation_seconds",
                    "restic_snapshots",
                    "local_checkpoints",
                    "new_scheduled_flow_after_retirement",
                    "depth_regular_session_followup",
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
