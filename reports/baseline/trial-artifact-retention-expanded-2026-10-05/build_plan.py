"""Build a no-delete review plan from the expanded read-only evidence."""

import gzip
import subprocess
from pathlib import Path

from review import BASE, KIT, OLD, PRODUCTION, PROFILE, ROOT
from review import TARGETS, UUID, encoded, load, now, sha, signature, write


def main():
    probe = load(BASE / "probe.json")
    inventory = load(BASE / "inventory.json")
    retained = load(BASE / "retained-checkpoint-verification.json")
    old_plan = load(OLD / "plan.json")
    old_inventory = load(OLD / "inventory.json")
    assert not inventory["external_hardlinked_inodes"]
    assert not inventory["shared_inodes"]
    assert all(r["metadata_inventory_unchanged"] for r in inventory["original_twelve_trees"])
    assert retained["result"]["ok"]
    assert retained["result"]["backup_id"] == probe["pilot_retained_sources"][0]["backup_id"]
    assert len(probe["clone_snapshot_equivalence"]) == 7
    assert probe["trial_lock_files_after_probe"] == 0
    assert set(TARGETS.values()).isdisjoint({PRODUCTION, KIT, Path("/mnt/backup/marketdata-historical")})
    for p in (PRODUCTION, KIT, Path("/mnt/backup/marketdata-historical"), ROOT):
        assert all(not p.is_relative_to(t) and not t.is_relative_to(p) for t in TARGETS.values())
    for copy in old_inventory["preserved_metadata"]:
        raw = (ROOT / copy["copy"]).read_bytes()
        assert sha(raw) == copy["compressed_sha256"]
        assert sha(gzip.decompress(raw)) == copy["sha256"]
    for candidate in inventory["targets"]:
        assert sha((ROOT / candidate["inventory"]).read_bytes()) == candidate["gzip_sha256"]
    config_changes = [p for p, digest in probe["configuration_sha256"].items()
                      if sha(Path(p).read_bytes()) != digest]
    assert not config_changes, config_changes
    copied_profile = ROOT / probe["trial_profile"]["preserved_copy"]
    assert copied_profile.read_bytes() == PROFILE.read_bytes()
    note = BASE / "recovery-kit-note.md"
    assert note.is_file()
    command = ["du", "-s", "-c", "-x", "-B1", "--", *(str(p) for p in TARGETS.values())]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    observed = {line.split("\t", 1)[1]: int(line.split("\t", 1)[0]) for line in result.stdout.splitlines()}
    assert observed["total"] == inventory["estimated_reclaimable_allocated_bytes"]
    for row in inventory["targets"]:
        assert observed[row["path"]] == row["regular_allocated_path_bytes"] + row["directory_blocks_bytes"]
    protected = [
        {"path": str(ROOT), "role": "production data/revisions/catalog/code and all evidence reports"},
        {"path": "/mnt/backup/marketdata-historical", "role": "all retained working and pre-enrollment checkpoints"},
        {"path": str(PRODUCTION), "role": "production repository; every snapshot retained"},
        {"path": str(KIT), "role": "whole recovery kit; add only reviewed guidance after cleanup"},
        {"path": "/mnt/backup/marketdata-restic-restore", "role": "keep the configured operational restore parent; only the named old run is a candidate"},
        {"path": str(Path.home() / ".config/marketdata/secrets/restic-password"), "role": "shared production restic credential, never a disposable pilot-only file"},
        {"path": str(ROOT / ".cache/restic/0.19.1/restic"), "role": "shared production restic binary"},
        {"path": str(ROOT / "archive-state"), "role": "original production archive, restore and retirement receipts/state"},
    ]
    inputs = ["review.py", "build_plan.py", "verify_retained.py", "probe.json", "inventory.json",
              "retained-checkpoint-verification.json", "retained-checkpoint-verification-incomplete.json.txt",
              "recovery-kit-note.md"]
    plan = {
        "schema": 1, "kind": "expanded_one_off_trial_retirement_review", "created_at": now(),
        "supersedes_review_plan_id": old_plan["plan_id"],
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "input_sha256": {p: sha((BASE / p).read_bytes()) for p in inputs},
        "authorization": {"deletion_authorized": False, "apply_enabled": False,
                          "recurring_checkpoint_grant_applies": False,
                          "status": "expanded concrete list for separate manual review; no executable apply entrypoint"},
        "filesystem_uuid": UUID, "candidate_roots": inventory["targets"],
        "protected": protected,
        "profile_retirement": {**probe["trial_profile"], "source_identity": signature(PROFILE.stat()),
                               "planned_action": "After approval and exact-copy/hash check, remove only this inactive host profile from its former config path; keep the report copy.",
                               "performed": False},
        "kit_guidance": {"prepared_source": str(note), "sha256": sha(note.read_bytes()),
                         "planned_destination": str(KIT / "TRIAL-CLEANUP.md"),
                         "planned_action": "After approval, add this note and actual completion receipt; preserve original kit files.",
                         "installed": False},
        "preserved_proofs": {"old_inventory": str(OLD / "inventory.json"),
                             "old_inventory_sha256": sha((OLD / "inventory.json").read_bytes()),
                             "historical_proofs": old_plan["historical_proofs"],
                             "copied_manifests_and_restore_receipts": old_inventory["preserved_metadata"],
                             "protected_recovery_receipt": probe["protected_recovery_receipt"]},
        "replacement_sources": probe["pilot_retained_sources"],
        "cloned_snapshots_also_in_production": probe["clone_snapshot_equivalence"],
        "retained_original_checkpoint_validation": retained,
        "existing_production_full_check": probe["existing_production_full_check"],
        "estimated_reclaimable_allocated_bytes": inventory["estimated_reclaimable_allocated_bytes"],
        "additional_allocated_bytes_vs_old_plan": inventory["estimated_reclaimable_allocated_bytes"] - old_plan["estimated_reclaimable_allocated_bytes"],
        "apparent_file_bytes": inventory["apparent_file_bytes"],
        "candidate_file_count": sum(r["files"] for r in inventory["targets"]),
        "candidate_directory_count": sum(r["directories"] for r in inventory["targets"]),
        "du_cross_check": observed, "external_hardlinks_observed": 0,
        "usage": {"matches": 0, "scope": probe["process_usage_after"]["scope"],
                  "permission_denied_pids": probe["process_usage_after"]["permission_denied_pids"]},
        "pre_apply_requirements": [
            "Obtain separate approval for this expanded allowlist, inactive-profile retirement and kit note; the previous 12-directory plan and checkpoint grant do not authorize them.",
            "First observe the 2026-10-06 15:40 KST scheduled checkpoint retirement as the preferred operational sequence; do not equate elapsed time with success.",
            "Recheck mount/UUID, exact repository IDs, retained snapshot identities, current archive health and full-check evidence, original 09-26 checkpoint and protected recovery receipt.",
            "Recheck every candidate tree identity/list/type/size/time, nested mounts and hardlinks; reject unexplained changes or newly added files.",
            "Confirm no trial restic/restore/backup/manual jobs; fence the old trial profile, acquire applicable checkpoint-parent locks exclusively and nonblocking, and check trial repository locks. A /proc sample alone is insufficient.",
            "Prepare a reviewed manual operation with exact-file inventories and interruption journal; do not pass these repository roots to the automatic checkpoint cleaner or delete using a parent glob.",
            "Preserve manifests, receipts, original source_root and production archive state. After actual deletion append a disposal receipt and kit guidance without changing old success evidence.",
            "Measure free space on the same filesystem before/after, verify all protected paths/IDs and unchanged operating configs, and preserve failure/partial-delete records.",
            "No forget/prune or production snapshot expiry, no source/retained-checkpoint/credential deletion, no collector restart."
        ],
        "reproduction_limits": "Pilot repository-specific snapshot IDs and the frozen clone cease to exist after their deletion. Equivalent original checkpoint contents remain at the explicit retained sources; new trials use new profiles/paths and new identities.",
        "actual_files_deleted": 0, "actual_reclaimed_bytes": 0,
    }
    plan["plan_id"] = sha(encoded(plan))
    write(BASE / "plan.json", plan)
    write(BASE / "validation.json", {"at": now(), "plan_id": plan["plan_id"],
                                     "plan_inputs_checked": True, "metadata_copies_checked": True,
                                     "configuration_changes_since_probe": config_changes,
                                     "independent_du_totals_match": True, "du_command": command,
                                     "old_twelve_inventories_unchanged": True,
                                     "original_checkpoint_content_verification_passed": True,
                                     "deleted_files": 0})
    print(encoded({"plan_id": plan["plan_id"], "candidate_roots": len(plan["candidate_roots"]),
                   "estimated_reclaimable_allocated_bytes": plan["estimated_reclaimable_allocated_bytes"],
                   "deleted_files": 0}).decode())


if __name__ == "__main__":
    main()
