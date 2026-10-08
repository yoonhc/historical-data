"""Assemble this review plan from the recorded observations; never apply it."""

import gzip
import json
import subprocess
import tomllib
from pathlib import Path

from inventory import BASE, ROOT, digest, encoded, now


def load(path):
    return json.loads(path.read_bytes())


def main():
    cases = load(BASE / "cases.json")
    inv = load(BASE / "inventory.json")
    probe = load(BASE / "probe.json")
    host = load(BASE / "host-followup.json")
    assert not (BASE / "plan.json").exists()
    assert not inv["shared_inodes"] and not host["nested_candidate_mounts"]
    assert not probe["process_usage_before"]["matches"]
    assert not probe["process_usage_after"]["matches"]
    assert not host["configuration_changed_since_probe"]
    for copy in inv["preserved_metadata"]:
        compressed = (ROOT / copy["copy"]).read_bytes()
        assert digest(compressed) == copy["compressed_sha256"]
        raw = gzip.decompress(compressed)
        assert digest(raw) == copy["sha256"]
        assert raw == Path(copy["source"]).read_bytes()
    for row in inv["candidates"]:
        assert not any(row[k] for k in ("unexpected_files", "missing_manifest_files",
                                        "size_differences", "extra_empty_directories"))
        assert digest((ROOT / row["inventory"]).read_bytes()) == row["inventory_gzip_sha256"]
    protected = [
        {"path": str(ROOT), "role": "production source, registered market data/revisions, code and all reports", "action": "preserve"},
        {"path": "/mnt/backup/marketdata-historical", "role": "all original working/pre-enrollment checkpoints; governed separately", "action": "preserve"},
        {"path": "/mnt/backup/marketdata-restic", "role": "production restic repository and every snapshot", "action": "preserve"},
        {"path": "/mnt/backup/marketdata-restic-pilot", "role": "pilot restic repository and both snapshot anchors", "action": "preserve"},
        {"path": "/mnt/backup/marketdata-independent-recovery-20261003/repository", "role": "independent recovery clone and its snapshots", "action": "preserve"},
        {"path": "/mnt/backup/marketdata-recovery-kit-20261003", "role": "entire recovery kit, including password, code, packages and evidence", "action": "preserve"},
        {"path": "/mnt/backup/marketdata-restic-pilot-work/cache", "role": "small pilot cache; outside this allowlist", "action": "preserve"},
        {"path": str(Path.home() / ".config/marketdata"), "role": "profiles, credentials, policies and scoped checkpoint grant", "action": "preserve"},
    ]
    parents = sorted({str(Path(c[r]).parent) for c in cases for r in ("extracted", "store", "new-backup")})
    protected += [{"path": p, "role": "container directory; remove neither it nor other children", "action": "retain_parent_directory"} for p in parents]
    histories, file_equivalence, candidates = [], [], []
    for case in cases:
        source = ROOT / case["evidence"]
        original = load(Path(case["extracted"]) / "backup.json")
        new = load(Path(case["new-backup"]) / "backup.json")
        original_files = {x["path"]: x for x in original["files"]}
        new_files = {x["path"]: x for x in new["files"]}
        assert original_files == new_files
        assert original["catalog_counts"] == new["catalog_counts"]
        file_equivalence.append({"case": case["case"], "manifest_file_records_equal": True,
                                 "catalog_counts_equal": True, "file_records": len(original_files),
                                 "scope": "Recorded path/size/SHA-256/kind equality, not a fresh payload read"})
        historic = load(source)
        if case["case"].startswith("pilot"):
            label = case["case"].removeprefix("pilot-")
            proof = next(r for r in historic["recoveries"] if r["label"] == label)
            assert proof["extracted_verification"]["ok"] and proof["new_backup_verification"]["ok"]
            assert proof["validate"]["ok"] and proof["query_summary"]["ok"]
            assert proof["original_backup_id"] == case["backup_id"]
        else:
            assert historic["ok"] and historic["action"] == "restore-check"
            proof = historic["result"]
            assert proof["queries"]["ok"]
        assert proof["ok"] and proof["services_started"] is False
        assert proof["snapshot_id"] == case["snapshot_id"]
        assert proof["new_backup_id"] == case["new_backup_id"]
        assert proof["independent_file_checks"] == len(original_files)
        histories.append({"case": case["case"], "evidence": case["evidence"],
                          "evidence_sha256": digest(source.read_bytes()), "historical_ok": True,
                          "independent_file_checks": proof["independent_file_checks"],
                          "services_started": False})
        anchor = next(x for x in probe["manifest_comparisons"] if x["case"] == case["case"])
        for row in (r for r in inv["candidates"] if r["case"] == case["case"]):
            candidates.append({**row, "snapshot_anchor": anchor,
                               "backup_id": case["new_backup_id"] if row["role"] == "new-backup" else case["backup_id"],
                               "original_backup_id": case["backup_id"]})
    receipt = ROOT / cases[-1]["evidence"]
    policy_path = Path.home() / ".config/marketdata/checkpoint-retention-automatic.toml"
    policy = tomllib.loads(policy_path.read_text())
    assert Path(policy["recovery_receipt"]) == receipt
    assert policy["recovery_receipt_sha256"] == digest(receipt.read_bytes())
    state_path = ROOT / "archive-state/b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558/historical-data-original-v1/state.json"
    state = load(state_path)
    assert not state.get("active_operation")
    assert (state.get("last_operation") or {"ok": True})["ok"]
    check = state["repository_check"]
    assert check["ok"] and check["read_data"] and check["status"] == "complete"
    production_snapshots = {x["id"] for x in probe["repositories"]["/mnt/backup/marketdata-restic"]["snapshots"]}
    for case in cases[-2:]:
        assert case["snapshot_id"] in production_snapshots
        assert case["snapshot_id"] in check["snapshot_ids"]
        matches = [r for r in state["checkpoints"].values() if (r.get("stored") or {}).get("snapshot_id") == case["snapshot_id"]]
        assert len(matches) == 1 and matches[0]["entry"]["backup_id"] == case["backup_id"]
    inputs = ["cases.json", "inventory.json", "probe.json", "host-followup.json",
              "inventory.py", "probe.py", "build_plan.py"]
    plan = {
        "schema": 1, "kind": "one_off_trial_work_artifacts_review", "created_at": now(),
        "authorization": {"deletion_authorized": False, "apply_enabled": False,
                          "recurring_checkpoint_grant_applies": False,
                          "reviewed_deletion_scope": "only these 12 exact directories; no parent wildcard"},
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "input_sha256": {p: digest((BASE / p).read_bytes()) for p in inputs},
        "filesystem": probe["mount"]["filesystems"][0],
        "candidates": candidates, "protected": protected, "historical_proofs": histories,
        "file_equivalence": file_equivalence,
        "checkpoint_dependency": {"recovery_receipt": str(receipt),
                                  "receipt_sha256": digest(receipt.read_bytes()),
                                  "policy": str(policy_path), "receipt_hash_matches_policy": True,
                                  "protected_production_snapshot_ids": [c["snapshot_id"] for c in cases[-2:]],
                                  "latest_full_read_check_observed": check,
                                  "trial_folders_not_required_by_recovery_guard": True},
        "estimated_reclaimable_allocated_bytes": inv["estimated_reclaimable_allocated_bytes"],
        "apparent_file_bytes": inv["apparent_file_bytes"],
        "candidate_file_paths": inv["file_paths"], "candidate_directory_paths": inv["directory_paths"],
        "external_hardlinks_observed": 0, "nested_mounts_observed": 0,
        "usage_observation": {"candidate_references_seen": 0,
                              "restricted_processes": host["restricted_process_identity"],
                              "limitations": "Visible same-user processes at two instants; not exclusion of concurrent or other-user access"},
        "preserved_metadata_bytes_gzip": sum(c["compressed_bytes"] for c in inv["preserved_metadata"]),
        "preserved_inventory_bytes_gzip": sum(c["inventory_gzip_bytes"] for c in inv["candidates"]),
        "first_automatic_checkpoint_run": {"status": "pending", "scheduled_kst": "2026-10-06T15:40:00+09:00",
                                           "observed_trigger": None},
        "pre_apply_requirements": [
            "Obtain separate approval of the exact final plan; checkpoint recurring grant conveys no permission here.",
            "Prefer observing the first scheduled checkpoint retirement before manual trial cleanup; investigate any failure first.",
            "Recheck UUID, repository IDs, exact snapshot IDs and byte-identical manifests, receipt hashes, preserved evidence and kit availability.",
            "Recheck root device/inode, full metadata inventory, unexpected files, symlinks, mounts and hardlinks; any change requires review.",
            "Establish no active reader/writer/restore/backup for each target and hold the existing cooperative locks where applicable; investigate unknown use.",
            "Use a reviewed exact-file operation with an interruption journal, not recursive parent deletion; no apply code is supplied by this plan.",
            "Record before/after free space, removed files, remaining protected paths and unresolved errors; do not claim free-space delta is exact attribution.",
            "Keep old manifests/source_root, historical receipts and archive state unchanged; append a new disposal receipt rather than rewrite evidence."
        ],
        "regeneration_limits": "Original extracted data can be obtained from the exact retained snapshot. A fresh restore/rebackup uses a fresh destination and yields new time/provenance/backup ID; it does not recreate the historical execution or promise the same new-backup ID.",
        "fresh_full_data_check": False, "fresh_restore_test": False,
        "files_deleted": 0, "actual_reclaimed_bytes": 0,
    }
    plan["plan_id"] = digest(encoded(plan))
    (BASE / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"plan_id": plan["plan_id"], "candidates": len(candidates),
                      "estimated_reclaimable_allocated_bytes": plan["estimated_reclaimable_allocated_bytes"],
                      "files_deleted": 0}))


if __name__ == "__main__":
    main()
