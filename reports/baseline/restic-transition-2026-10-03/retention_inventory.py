"""Read-only, dated retention inventory. This is not a deletion command."""

import hashlib
import json
import os
import stat
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from marketdata.domain import json_bytes, now  # noqa: E402

from scripts.archive_backups import (  # noqa: E402
    Profile,
    checkpoint,
    load_state,
    overview,
    timestamp,
)


def inventory():
    profile = Profile.load(Path("/home/yoonhc/.config/marketdata/restic-archive.toml"))
    profile.guard()
    began = now()
    state = load_state(profile)
    status = overview(profile)
    checked = state["repository_check"]
    assert checked["ok"] and checked["read_data"]
    checked_ids = set(checked["snapshot_ids"])
    rows, incomplete, unknown = [], [], []
    for day in sorted(profile.backup_dir.iterdir()):
        assert not day.is_symlink()
        if not day.is_dir():
            unknown.append(str(day))
            continue
        for folder in sorted(day.iterdir()):
            assert not folder.is_symlink()
            if not folder.is_dir():
                unknown.append(str(folder))
                continue
            if not (folder / "backup.json").exists():
                incomplete.append(str(folder))
                continue
            entry = checkpoint(folder, profile)
            record = state["checkpoints"].get(entry["backup_id"])
            stored = (record or {}).get("stored")
            enrolled = entry["checkpoint_at"][:10] >= profile.enroll_from_utc_date
            valid = bool(
                enrolled
                and stored
                and record["entry"] == entry
                and record.get("input_verified_at")
                and not record.get("error")
                and stored["snapshot_id"] in checked_ids
            )
            rows.append(
                {
                    **entry,
                    "snapshot_id": stored["snapshot_id"] if stored else None,
                    "recorded_input_and_archive_checks_passed": valid,
                    "enrolled": enrolled,
                }
            )
    eligible = sorted(
        (r for r in rows if r["recorded_input_and_archive_checks_passed"]),
        key=lambda r: (r["checkpoint_at"], r["backup_id"]),
    )
    newest = {r["backup_id"] for r in eligible[-3:]}
    candidates = set()
    for row in rows:
        if not row["enrolled"]:
            decision = "exclude_before_enrollment"
        elif not row["recorded_input_and_archive_checks_passed"]:
            decision = "preserve_unverified_or_unmapped"
        elif row["backup_id"] in newest:
            decision = "keep_latest_three"
        elif began - timestamp(row["completed_at"]) < timedelta(hours=72):
            decision = "keep_minimum_72_hours"
        else:
            decision = "working_copy_candidate_only"
            candidates.add(row["backup_id"])
        row["decision"] = decision

    inodes = {}
    candidate_links = Counter()
    candidate_directory_bytes = 0
    all_directory_bytes = 0
    candidate_logical_bytes = 0
    candidate_file_count = 0
    for row in rows:
        folder = Path(row["path"])
        is_candidate = row["backup_id"] in candidates
        manifest = json.loads((folder / "backup.json").read_bytes())
        expected_files = {f["path"] for f in manifest["files"]} | {"backup.json"}
        found = set()
        for directory, directories, filenames in os.walk(folder, followlinks=False):
            directory = Path(directory)
            ds = directory.lstat()
            assert stat.S_ISDIR(ds.st_mode)
            all_directory_bytes += ds.st_blocks * 512
            if is_candidate:
                candidate_directory_bytes += ds.st_blocks * 512
            for name in directories:
                assert not (directory / name).is_symlink()
            for name in filenames:
                path = directory / name
                fs = path.lstat()
                assert stat.S_ISREG(fs.st_mode) and fs.st_dev == ds.st_dev
                found.add(str(path.relative_to(folder)))
                key = (fs.st_dev, fs.st_ino)
                signature = (fs.st_size, fs.st_blocks, fs.st_nlink)
                if key in inodes:
                    assert inodes[key]["signature"] == signature
                else:
                    inodes[key] = {"signature": signature, "sample": str(path)}
                if is_candidate:
                    candidate_links[key] += 1
                    candidate_logical_bytes += fs.st_size
                    candidate_file_count += 1
        assert found == expected_files, f"Unexpected or missing files: {folder}"
    # Ensure no concurrent link-count/allocation changes invalidate this dated estimate.
    for record in inodes.values():
        fs = Path(record["sample"]).lstat()
        assert (fs.st_size, fs.st_blocks, fs.st_nlink) == record["signature"]
    reclaim = sum(
        inodes[key]["signature"][1] * 512
        for key, links in candidate_links.items()
        if links == inodes[key]["signature"][2]
    )
    shared = sum(
        inodes[key]["signature"][1] * 512
        for key, links in candidate_links.items()
        if links < inodes[key]["signature"][2]
    )
    assert all(links <= inodes[key]["signature"][2] for key, links in candidate_links.items())
    scope_snapshots = [
        r["stored"]["snapshot_id"] for r in state["checkpoints"].values() if r["stored"]
    ]
    scope_dates = sorted(
        {r["entry"]["checkpoint_at"][:10] for r in state["checkpoints"].values() if r["stored"]}
    )
    # Every current date fits daily=30. This limited preview is not a general restic simulator.
    assert len(scope_dates) == len(scope_snapshots) <= 30
    result = {
        "started_at": began,
        "finished_at": now(),
        "mode": "read_only_inventory_no_forget_no_prune_no_delete",
        "identity": profile.identity,
        "backup_dir": str(profile.backup_dir),
        "enroll_from_utc_date": profile.enroll_from_utc_date,
        "policy": {
            "working_keep_latest_verified": 3,
            "working_minimum_age_hours": 72,
            "restic_keep_last": 3,
            "restic_keep_daily": 30,
            "restic_keep_weekly": 12,
            "restic_keep_monthly": 12,
            "restic_keep_yearly": "unlimited",
            "restic_timezone": "UTC",
            "group_by": "",
            "required_tags_all": [
                "marketdata-archive-v1",
                f"archive_scope={profile.archive_scope}",
            ],
        },
        "checkpoints": sorted(rows, key=lambda r: (r["checkpoint_at"], r["backup_id"])),
        "incomplete_folders_preserved": incomplete,
        "unexpected_entries_preserved": unknown,
        "counts": dict(Counter(r["decision"] for r in rows)),
        "space": {
            "method": "lstat st_blocks*512 once per (device,inode); reclaim only when every hardlink is a candidate",
            "candidate_manifest_payload_bytes": sum(
                r["bytes"] for r in rows if r["backup_id"] in candidates
            ),
            "candidate_logical_file_bytes_including_manifests": candidate_logical_bytes,
            "candidate_regular_file_paths": candidate_file_count,
            "candidate_unique_inodes": len(candidate_links),
            "candidate_reclaimable_regular_file_bytes": reclaim,
            "candidate_retained_by_other_hardlinks_bytes": shared,
            "candidate_directory_allocated_bytes": candidate_directory_bytes,
            "all_completed_unique_regular_file_bytes": sum(
                r["signature"][1] * 512 for r in inodes.values()
            ),
            "all_completed_directory_allocated_bytes": all_directory_bytes,
            "filesystem_journal_and_parent_directory_overhead_excluded": True,
            "actual_bytes_freed": 0,
        },
        "restic_preview": {
            "known_stored_snapshots": scope_snapshots,
            "checkpoint_utc_dates": scope_dates,
            "kept_snapshot_count": len(scope_snapshots),
            "removal_candidates": [],
            "basis": "all seven distinct UTC checkpoint dates fit daily=30; no restic forget command executed",
            "general_policy_simulation": False,
            "live_repository_checked_by_this_inventory": False,
        },
        "preconditions_snapshot": {
            "healthy": status["healthy"],
            "daily_stale": status["daily_stale"],
            "pending_count": status["pending_count"],
            "unchecked_snapshot_ids": status["unchecked_snapshot_ids"],
            "checkpoint_errors": status["checkpoint_errors"],
            "repository_check_finished_at": checked["finished_at"],
            "repository_check_sha256": hashlib.sha256(json_bytes(checked)).hexdigest(),
        },
        "deletion_authorized": False,
        "execution_recheck_required": True,
    }
    with (OUT / "retention-inventory.json").open("xb") as handle:
        handle.write(json_bytes(result))
    print(
        json_bytes({k: result[k] for k in ("counts", "space", "preconditions_snapshot")}).decode()
    )


if __name__ == "__main__":
    os.nice(10)
    inventory()
