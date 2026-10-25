"""Read-only input refresh and data-only repinning; never applies the operation.

The five deployment modules and original tests/native preflight are reused byte
for byte. A separate, explicitly derived preflight reuse attestation connects
the unchanged execution inputs to the new preparation digest. It is not a new
administrator test result. Original artifacts and approvals are never edited.
"""

import argparse
import copy
import importlib.util
import json
import os
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "t18-archive-deploy-2026-10-22"
spec = importlib.util.spec_from_file_location("original_handoff", OLD / "handoff.py")
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)
c, op = handoff.c, handoff.op
ARCHIVE_STATE = (
    c.ROOT / "archive-state"
    / "b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558"
    / "historical-data-original-v1/state.json"
)
ORIGINAL_SHA = "50aab5c8d8ded456fb6dffb32aedef755739f09377258d3f96d628dfca65a733"
ALLOWED_PREPARED_FIELDS = {
    "observed_at", "preparation_id", "pending_checkpoints_observed", "capacity"
}


def save_new(name, value):
    path = HERE / name
    raw = c.encode(value) if not isinstance(value, bytes) else value
    if path.exists():
        c.require(path.read_bytes() == raw, "Frozen file differs: " + name)
    else:
        with path.open("xb") as stream:
            stream.write(raw)


def inventory(plan):
    prior = plan["prepared"]
    audit_path = HERE.parent / "t05-recurring-operating-completion-2026-10-19/audit.json"
    audit = json.loads(audit_path.read_bytes())
    expected = {
        path: {"sha256": value["sha256"], "source": str(audit_path)}
        for path, value in audit["sources"].items()
        if path in plan["checkpoint_manifest_paths"]
    }
    for value in prior["pending_checkpoints_observed"]:
        expected[str(Path(value["path"]) / "backup.json")] = {
            "sha256": value["manifest_sha256"], "source": str(OLD / "operation-plan.json")
        }
    c.require(set(expected) == set(plan["checkpoint_manifest_paths"]), "Missing historical manifest identity")
    added_22 = json.loads((OLD / "preapply-hold-20261022.json").read_bytes())["inventory_delta"]
    expected[added_22["added_manifest"]] = {
        "sha256": added_22["added_manifest_sha256"],
        "source": str(OLD / "preapply-hold-20261022.json"),
    }
    initial_observation = HERE / "inventory-observation-051302.json"
    if initial_observation.exists():
        for row in json.loads(initial_observation.read_bytes())["rows"]:
            expected.setdefault(row["manifest_path"], {
                "sha256": row["manifest_sha256"], "source": str(initial_observation)
            })
    paths = sorted(Path("/mnt/backup/marketdata-historical/automatic").glob("*/*/backup.json"))
    names = list(map(str, paths))
    missing = sorted(set(expected) - set(names))
    c.require(not missing, "Existing manifests missing: " + repr(missing))
    state_raw = ARCHIVE_STATE.read_bytes()
    state = json.loads(state_raw)
    rows, changed, seen = [], [], set()
    for path in paths:
        c.require(not path.is_symlink(), "Manifest is a symlink")
        raw = path.read_bytes()
        value = json.loads(raw)
        bid, digest = value["backup_id"], c.sha(raw)
        c.require(bid not in seen, "Duplicate backup identity")
        seen.add(bid)
        reference = expected.get(str(path))
        if reference and reference["sha256"] != digest:
            changed.append(str(path))
        stored = state["checkpoints"].get(bid, {}).get("stored", {}).get("snapshot_id")
        excluded = path.parent.parent.name < "2026-09-27"
        rows.append({
            "path": str(path.parent), "manifest_path": str(path), "backup_id": bid,
            "manifest_sha256": digest, "manifest_bytes": len(raw),
            "completed_at": value["completed_at"],
            "relative_to_original_operation": "existing" if str(path) in plan["checkpoint_manifest_paths"] else "added",
            "historical_reference": reference,
            "historical_content_matches": digest == reference["sha256"] if reference else None,
            "archive_status": "excluded_before_enrollment" if excluded else "stored" if stored else "unarchived",
            "snapshot_id": stored,
        })
    c.require(not changed, "Existing manifest bytes changed: " + repr(changed))
    c.require(names == sorted(str(p) for p in Path("/mnt/backup/marketdata-historical/automatic").glob("*/*/backup.json")), "Inventory changed during read")
    c.require(ARCHIVE_STATE.read_bytes() == state_raw, "Archive state changed during read")
    return {
        "observed_at": datetime.now(UTC).isoformat(), "manifest_paths": names,
        "original_count": len(plan["checkpoint_manifest_paths"]), "current_count": len(rows),
        "previously_observed_manifest_count": len(expected),
        "changed_existing": changed, "missing_existing": missing,
        "added_paths": sorted(set(names) - set(plan["checkpoint_manifest_paths"])),
        "rows": rows, "archive_state_sha256": c.sha(state_raw),
        "archive_state_updated_at": state["updated_at"],
        "stored_snapshot_count": len(state["checkpoints"]),
        "historical_audit_sha256": c.file_sha(audit_path),
        "restic_command_executed": False, "full_integrity_check_performed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    original = handoff.checked()
    c.require(c.file_sha(OLD / "operation-plan.json") == ORIGINAL_SHA, "Original operation changed")
    reuse = json.loads((HERE / "reuse-hash-verification.json").read_bytes())
    c.require(reuse["passed"] and reuse["original_operation_sha256"] == ORIGINAL_SHA, "Reuse verification missing")
    current = inventory(original)
    pending = [
        {key: row[key] for key in ("backup_id", "path", "completed_at", "manifest_bytes", "manifest_sha256")}
        for row in current["rows"] if row["archive_status"] == "unarchived"
    ]
    body_basis = original["prepared"]["capacity"]["manifest_outputs"]
    first_body = sum(x["bytes"] for x in body_basis.values()) + sum(x["manifest_bytes"] for x in pending)
    largest = max([x["bytes"] for x in body_basis.values()] + [x["manifest_bytes"] for x in pending])
    v = os.statvfs(c.ROOT)
    available = v.f_bavail * v.f_frsize
    reserve = original["prepared"]["capacity"]["required_collection_reserve_bytes"]
    extra = original["additional_resume_headroom_bytes"]
    required = reserve + first_body + largest + extra
    request = json.loads((c.ROOT / "state/scheduled-clock-policy/backup/request.json").read_bytes())
    maintenance = json.loads((c.ROOT / "state/maintenance-daily-latest.json").read_bytes())
    current["capacity"] = {
        "existing_stored_body_bytes": sum(x["bytes"] for x in body_basis.values()),
        "unarchived_manifest_bytes": sum(x["manifest_bytes"] for x in pending),
        "first_body_bytes": first_body, "largest_temporary_stdout_bytes": largest,
        "required_collection_reserve_bytes": reserve, "additional_resume_headroom_bytes": extra,
        "required_bytes": required, "available_bytes": available,
        "headroom_bytes": available - required, "passed": available > required,
    }
    current["backup_request"] = {k: request.get(k) for k in ("request_id", "state", "exit_code")}
    current["backup_maintenance"] = {k: maintenance.get(k) for k in ("id", "phase", "started_at", "finished_at", "ok")}
    if not args.freeze:
        target = "inventory-observation-" + datetime.now(UTC).strftime("%H%M%S") + ".json"
        save_new(target, current)
        print(json.dumps({"report": str(HERE / target), "count": current["current_count"], "unarchived": len(pending), "capacity": current["capacity"], "backup": current["backup_maintenance"]}, indent=2))
        return
    c.require(request["state"] == "complete" and maintenance["phase"] == "finished" and maintenance["ok"], "Wait for the running regular backup before freezing its inventory")
    c.require(current["manifest_paths"] == sorted(str(p) for p in Path("/mnt/backup/marketdata-historical/automatic").glob("*/*/backup.json")), "Inventory changed after backup observation")
    c.require(available > required, "Insufficient operating filesystem headroom")
    prepared = copy.deepcopy(original["prepared"])
    prepared["observed_at"] = current["observed_at"]
    prepared["pending_checkpoints_observed"] = pending
    prepared["capacity"].update(available_bytes=available, first_body_bytes=first_body, largest_temporary_stdout_bytes=largest)
    prepared["preparation_id"] = c.sha(c.canonical({k: v for k, v in prepared.items() if k != "preparation_id"}))
    c.require({k for k in prepared if prepared[k] != original["prepared"][k]} <= ALLOWED_PREPARED_FIELDS, "Unexpected preparation change")
    for path, pair in prepared["changes"].items():
        c.require(c.node(path, text=True) == pair["before"], "Current configuration differs: " + path)
    c.require(c.node(c.GRANT, text=True) == prepared["recurring_before"], "Recurring changed")
    c.require(json.loads(Path(c.LEDGER).read_bytes()) == prepared["ledger_before"], "Ledger changed")
    for path, node in prepared["protected"].items():
        c.require(c.node(path) == node, "Protected input changed: " + path)
    preflight = json.loads((OLD / original["system_preflight"]).read_bytes())
    attestation = {
        "schema": 1, "kind": "hash_verified_reuse_of_original_native_preflight",
        "passed": True, "native_preflight_rerun": False, "new_native_trial_claimed": False,
        "preparation_id": prepared["preparation_id"], "operating_unchanged": True,
        "cleanup": preflight["cleanup"], "cleanup_evidence_origin": "original native preflight only",
        "original_native_preflight": {"file": original["system_preflight"], "sha256": c.file_sha(OLD / original["system_preflight"]), "preparation_id": preflight["preparation_id"], "started_at": preflight["started_at"], "finished_at": preflight["finished_at"]},
        "original_prepared_plan": {"file": "original-prepared-plan.json", "sha256": c.file_sha(OLD / "prepared-plan.json")},
        "hash_verification_file": "reuse-hash-verification.json",
        "hash_verification_sha256": c.file_sha(HERE / "reuse-hash-verification.json"),
        "allowed_changed_prepared_fields": sorted(ALLOWED_PREPARED_FIELDS),
        "execution_inputs_equal": True, "changes_do_not_affect_native_trial": "Only observation time, completed checkpoint inventory and space arithmetic changed; execution code, Python, configuration, scope, boot and protected inputs are identical.",
    }
    for name, digest in {**original["tool_files"], **original["evidence_files"]}.items():
        if name == "prepared-plan.json":
            continue
        raw = (OLD / name).read_bytes()
        c.require(c.sha(raw) == digest, "Reuse input changed: " + name)
        save_new(name, raw)
    save_new("original-prepared-plan.json", (OLD / "prepared-plan.json").read_bytes())
    save_new("original-operation-plan.json", (OLD / "operation-plan.json").read_bytes())
    save_new("prepared-plan.json", prepared)
    save_new("current-inventory.json", current)
    save_new("system-preflight-reuse.json", attestation)
    plan = copy.deepcopy(original)
    plan["prepared"] = prepared
    plan["checkpoint_manifest_paths"] = current["manifest_paths"]
    plan["system_preflight"] = "system-preflight-reuse.json"
    for name in ("prepared-plan.json", "original-prepared-plan.json", "original-operation-plan.json", "current-inventory.json", "system-preflight-reuse.json", "reuse-hash-verification.json", "inventory-observation-051302.json", "manager-observation.txt", "manager-final-observation.txt", "manager-final-check.json", "native-configuration-metadata.txt"):
        plan["evidence_files"][name] = c.file_sha(HERE / name)
    plan["operation_id"] = c.sha(c.canonical({k: v for k, v in plan.items() if k != "operation_id"}))
    op.validate(plan)
    save_new("operation-plan.json", plan)
    base = op.STATE + "/" + plan["operation_id"]
    paths = {"operation_id": plan["operation_id"], "operation_json_sha256": c.file_sha(HERE / "operation-plan.json"), "tool_id": plan["tool_id"], "source_release_id": prepared["release_id"], "authority": base + "/authority.json", "audit_directory": base, "sealed_tools": base + "/tools", "apply_receipt": base + "/apply-receipt.json", "rollback_receipt": base + "/rollback-receipt.json", "root_paths_created": False, "production_approved": False, "operating_applied": False}
    save_new("artifact-paths.json", paths)
    print(json.dumps({**paths, "manifest_count": len(current["manifest_paths"]), "unarchived_count": len(pending), "required_bytes": required, "available_bytes": available}, indent=2))


if __name__ == "__main__":
    main()
