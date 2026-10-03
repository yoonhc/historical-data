"""Freeze completed recovery evidence and check it against the original identities."""

import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
RUN = ROOT / "reports/restic-independent-20261003"
sys.path.insert(0, str(ROOT))

from marketdata.domain import json_bytes, now  # noqa: E402

from scripts.archive_backups import Profile, load_state, overview, timestamp  # noqa: E402


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def save(name, value):
    with (OUT / name).open("xb") as handle:
        handle.write(json_bytes(value))


def main():
    prepared = read(OUT / "preparation.json")
    launcher = read(RUN / "launcher-result.json")
    result = read(RUN / "runs/recovery-result.json")
    proof = read(RUN / "runs/isolation-proof.json")
    profile = Profile.load(Path("/home/yoonhc/.config/marketdata/restic-archive.toml"))
    profile.guard()
    assert launcher["returncode"] == 0 and result["ok"]
    recovered = result["result"]
    assert recovered["ok"] and recovered["snapshot_id"] == prepared["snapshot_id"]
    assert not recovered["services_started"]
    assert proof["state_initially_absent"] and proof["cache_initially_empty"]
    assert all(proof["original_paths_absent"].values())
    state_path = next((RUN / "state").glob("*/*/state.json"))
    isolated_state = read(state_path)
    assert list(isolated_state["checkpoints"]) == [prepared["backup_id"]]
    adopted = isolated_state["checkpoints"][prepared["backup_id"]]["stored"]
    assert adopted["snapshot_id"] == prepared["snapshot_id"]
    assert adopted["evidence"] == "explicit_restore_extracted_and_fully_verified"
    assert adopted["archive_started_at"] is None and adopted["archive_finished_at"] is None
    production = load_state(profile)
    entry = production["checkpoints"][prepared["backup_id"]]["entry"]
    assert sha(Path(entry["path"]) / "backup.json") == prepared["source_manifest_sha256"]
    assert (
        sha(Path("/home/yoonhc/.config/marketdata/restic-archive.toml"))
        == prepared["profile_sha256"]
    )
    assert prepared["production_snapshot_names"] == sorted(
        p.name for p in (profile.restic_repository / "snapshots").iterdir()
    )
    for relative, expected in prepared["code_sha256"].items():
        assert sha(ROOT / relative) == expected

    work = Path(prepared["work_path"])
    locations = {
        name: work / Path(recovered[name]).relative_to("/mnt/backup")
        for name in ("extracted", "restored", "new_backup")
    }
    original = read(locations["extracted"] / "backup.json")
    new = read(locations["new_backup"] / "backup.json")
    assert sha(locations["extracted"] / "backup.json") == prepared["source_manifest_sha256"]
    assert original["source_root"] == str(ROOT)
    assert new["source_root"] == recovered["restored"]
    assert new["backup_id"] == recovered["new_backup_id"] != original["backup_id"]
    assert new["storage"]["mode"] == "independent_copy"
    assert new["catalog_counts"] == original["catalog_counts"]
    assert len(new["files"]) == len(original["files"]) == recovered["independent_file_checks"]
    assert {
        (r["path"], r["kind"], r["sha256"], r["bytes"])
        for r in new["files"]
        if r["kind"] != "catalog"
    } == {
        (r["path"], r["kind"], r["sha256"], r["bytes"])
        for r in original["files"]
        if r["kind"] != "catalog"
    }

    report = RUN / "runs" / result["run_id"]
    phases = {}
    for name in (
        "verify-extracted",
        "restore-store",
        "readback",
        "validate-store",
        "new-backup",
        "verify-new-backup",
    ):
        files = [p for p in report.glob("*.json") if p.name.split("-", 1)[-1] == f"{name}.json"]
        assert len(files) == 1
        phases[name] = read(files[0])
    validation = phases["validate-store"]["result"]
    assert validation["ok"] and not validation["failures"]
    counts = original["catalog_counts"]
    assert (
        validation["checked"]
        == counts["artifacts"] + counts["revisions"] + counts["event_segments"]
    )
    queries = recovered["queries"]
    assert queries["ok"] and queries["snapshots"] == counts["snapshots"]
    assert all(q["equal"] for q in queries["snapshot_queries"])
    assert queries["aggtrade"]["equal"] and queries["depth_replay"]["replay_matches_live"]
    assert phases["verify-new-backup"]["result"]["ok"]

    observations = [
        json.loads(line) for line in (RUN / "host-stream-monitor.jsonl").read_text().splitlines()
    ]
    observed = {}
    for stream in ("depth", "aggtrade"):
        samples = [r[stream] for r in observations if stream in r]
        ids = sorted({r["id"] for r in samples})
        observed[stream] = {
            "samples": len(samples),
            "collection_ids": ids,
            "states_seen": sorted({r["state"] for r in samples}),
            "maximum_receive_age_seconds": max(
                r.get("receive_age_seconds", 0) or 0 for r in samples
            ),
            "maximum_counters": {
                key: max(r.get(key, 0) or 0 for r in samples)
                for key in (
                    "connection_errors",
                    "sync_losses",
                    "unreconstructed_messages",
                    "unrepaired_gaps",
                )
            },
            "samples_with_errors": [r for r in samples if r.get("error") or r.get("flush_error")],
            "last_sample": samples[-1],
        }
    internal_monitor = [
        json.loads(line) for line in (report / "monitor.jsonl").read_text().splitlines()
    ]
    assert internal_monitor and all("sampling_error" in r for r in internal_monitor)
    assert all("unable to open database file" in r["sampling_error"] for r in internal_monitor)
    key = Path(prepared["kit_path"]) / "secrets/restic-password"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
    assert key.stat().st_dev != profile.restic_password_file.stat().st_dev
    status = overview(profile)
    evidence = OUT / "recovery-evidence"
    evidence.mkdir()
    # Large original manifest dumps stay in the run directory; index their hashes in Git.
    dump_outputs = {
        Path(read(path)["stdout"]).name
        for path in report.glob("*.json")
        if read(path).get("operation") == "dump"
    }
    retained_locally = []
    for path in report.iterdir():
        if path.is_file():
            if path.name in dump_outputs:
                retained_locally.append(
                    {"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}
                )
            else:
                shutil.copyfile(path, evidence / path.name)
    for source, name in (
        (RUN / "launcher-start.json", "launcher-start.json"),
        (RUN / "launcher-result.json", "launcher-result.json"),
        (RUN / "runs/isolation-proof.json", "isolation-proof.json"),
        (RUN / "runs/recovery-result.json", "recovery-result.json"),
        (state_path, "isolated-state-final.json"),
        (RUN / "host-stream-monitor.jsonl", "host-stream-monitor.jsonl"),
        (RUN / "stdout.jsonl", "stdout.jsonl"),
        (RUN / "stderr.txt", "stderr.txt"),
    ):
        shutil.copyfile(source, evidence / name)
    save("production-status-after.json", status)
    # Test that none of the saved artifacts contain the secret; never serialize its value/hash.
    secret = key.read_bytes().strip()
    assert len(secret) >= 32
    for path in OUT.rglob("*"):
        if path.is_file():
            assert secret not in path.read_bytes(), "Secret unexpectedly present in evidence"
    summary = {
        "reviewed_at": now(),
        "ok": True,
        "run_id": result["run_id"],
        "snapshot_id": prepared["snapshot_id"],
        "old_backup_id": original["backup_id"],
        "new_backup_id": new["backup_id"],
        "started_at": launcher["started_at"],
        "finished_at": launcher["finished_at"],
        "total_seconds": (
            timestamp(launcher["finished_at"]) - timestamp(launcher["started_at"])
        ).total_seconds(),
        "restic_version": result["restic_version"],
        "phase_seconds": {k: v["wall_seconds"] for k, v in phases.items()},
        "full_logical_validation": validation,
        "physical_files_in_three_distinct_copies": recovered["independent_file_checks"],
        "catalog_counts_preserved": counts,
        "raw_revision_and_event_file_inventory_equal": True,
        "queries": {
            "scope": queries["scope"],
            "snapshots": queries["snapshots"],
            "snapshot_query_count": len(queries["snapshot_queries"]),
            "event_segment_samples": len(queries["event_segments"]),
            "raw_samples": len(queries["raw_ids"]),
            "aggtrade_rows": queries["aggtrade"]["result"]["rows"],
            "depth_replay": queries["depth_replay"],
        },
        "host_paths": {k: str(v) for k, v in locations.items()},
        "namespace_paths": {k: recovered[k] for k in locations},
        "original_source_root_preserved": original["source_root"],
        "new_source_root_created_by_program": new["source_root"],
        "production_code_profile_manifest_snapshot_names_unchanged": True,
        "credential_second_ssd_copy_permissions_checked": True,
        "credential_content_or_hash_recorded": False,
        "credential_off_machine": False,
        "new_machine_boot_or_vm_test": False,
        "production_monitor": {
            "samples": len(observations),
            "first_at": observations[0]["at"],
            "last_at": observations[-1]["at"],
            "sampling_errors": [r for r in observations if "monitor_error" in r],
            "streams": observed,
        },
        "isolation_monitor": {
            "expected_source_absence_errors": len(internal_monitor),
            "reason": "original catalog intentionally inaccessible; separate host-side read-only monitor used",
        },
        "production_health_after": {
            k: status[k]
            for k in (
                "healthy",
                "daily_stale",
                "pending_count",
                "unchecked_snapshot_ids",
                "checkpoint_errors",
            )
        },
        "files_deleted": 0,
        "snapshots_forgotten": 0,
        "prune_executed": False,
        "production_services_started_or_stopped": False,
        "large_manifest_dumps_retained_locally": retained_locally,
        "evidence_sha256": {
            str(p.relative_to(OUT)): sha(p) for p in sorted(evidence.iterdir()) if p.is_file()
        },
    }
    save("review.json", summary)
    print(
        json_bytes(
            {
                k: summary[k]
                for k in (
                    "ok",
                    "total_seconds",
                    "full_logical_validation",
                    "queries",
                    "production_health_after",
                )
            }
        ).decode()
    )


if __name__ == "__main__":
    os.nice(10)
    main()
