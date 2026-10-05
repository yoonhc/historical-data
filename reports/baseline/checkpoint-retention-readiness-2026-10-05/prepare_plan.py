"""Prepare the exact final-policy plan for review; never call apply."""

import json
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))

from marketdata.domain import json_bytes, now, sha256  # noqa: E402

from scripts import archive_backups as archive  # noqa: E402
from scripts import checkpoint_code  # noqa: E402
from scripts import retire_checkpoints as retention  # noqa: E402
from scripts.restic_trial import sample_streams  # noqa: E402


def read(path):
    return json.loads(path.read_bytes())


def save(name, value):
    with (OUT / name).open("xb") as handle:
        handle.write(json_bytes(value))


def manifests(profile):
    return {
        str(p): sha256(p.read_bytes()) for p in sorted(profile.backup_dir.glob("*/*/backup.json"))
    }


def main():
    readiness = read(OUT / "results.json")
    assert readiness["ok"] and readiness["new_scheduled_flow_after_final_install"]
    code = checkpoint_code.current_code()
    assert code == readiness["code"] and code["source_dirty"] is False
    policy = retention.Policy.load(OUT / "approval-policy.toml")
    assert policy.apply_enabled is True
    old_policy = OUT.parent / "checkpoint-retention-2026-10-03/preview-policy.toml"
    assert retention.Policy.load(old_policy).apply_enabled is False
    profile = archive.Profile.load(policy.archive_profile)
    profile.guard()
    # The denied processes are inspected by name/start time, without arguments or env.
    readers = read(OUT / "reader-snapshot.json")
    denied = readers["permission_denied_same_uid_pids"]
    if denied:
        output = subprocess.check_output(
            ["ps", "-p", ",".join(map(str, denied)), "-o", "pid=,user=,lstart=,comm="],
            text=True,
            timeout=10,
        )
        save(
            "restricted-process-names.json",
            {"at": now(), "ps_output": output, "full_process_access_still_unavailable": True},
        )
    state_before = sha256(profile.state_file.read_bytes())
    before = {
        "at": now(),
        "manifests": manifests(profile),
        "streams": sample_streams(ROOT),
        "archive_state_sha256": state_before,
        "original_policy_sha256": sha256(old_policy.read_bytes()),
    }
    save("before-plan.json", before)
    directories = set(profile.reports_root.glob("*-retention-*"))
    start = time.monotonic()
    receipt = retention.execute(policy, "plan")
    save("operational-plan.json", receipt)
    new_dirs = set(profile.reports_root.glob("*-retention-*")) - directories
    assert len(new_dirs) == 1
    commands = [read(p) for p in next(iter(new_dirs)).glob("*.json") if p.name != "retention.json"]
    snapshot_commands = [c for c in commands if c.get("operation") == "snapshots"]
    assert len(snapshot_commands) == 1
    snapshots = read(Path(snapshot_commands[0]["stdout"]))
    state = archive.load_state(profile)
    stored_ids = {r["stored"]["snapshot_id"] for r in state["checkpoints"].values()}
    assert {s["id"] for s in snapshots} == stored_ids and len(snapshots) == len(stored_ids)
    for bid in state["checkpoints"]:
        assert sum(f"backup_id={bid}" in s.get("tags", []) for s in snapshots) == 1
    save("live-snapshots.json", snapshots)
    save("plan-commands.json", commands)
    after = {
        "at": now(),
        "manifests": manifests(profile),
        "streams": sample_streams(ROOT),
        "archive_state_sha256": sha256(profile.state_file.read_bytes()),
        "original_policy_sha256": sha256(old_policy.read_bytes()),
        "wall_seconds": time.monotonic() - start,
        "all_backup_manifests_unchanged": before["manifests"] == manifests(profile),
        "archive_state_unchanged": state_before == sha256(profile.state_file.read_bytes()),
        "old_policy_unchanged": before["original_policy_sha256"] == sha256(old_policy.read_bytes()),
        "duplicate_snapshots": 0,
        "live_snapshot_count": len(snapshots),
        "operational_deletions": 0,
        "apply_called": False,
        "services_or_timers_changed": False,
        "code_unchanged": code == checkpoint_code.current_code(),
    }
    save("after-plan.json", after)
    assert after["all_backup_manifests_unchanged"] and after["archive_state_unchanged"]
    assert after["old_policy_unchanged"] and after["code_unchanged"]
    plan = receipt["result"]
    print(
        json_bytes(
            {
                "ok": receipt["ok"],
                "plan_id": plan["plan_id"],
                "keep": len(plan["keep"]),
                "candidates": len(plan["candidates"]),
                "excluded": len(plan["excluded"]),
                "space": plan["space"],
                "deleted_checkpoints": plan["deleted_checkpoints"],
                "wall_seconds": after["wall_seconds"],
                "snapshot_count": len(snapshots),
                "backup_manifests_unchanged": True,
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
