"""Read-only operational preview with before/after invariants; no apply entrypoint."""

import json
import os
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


def save(name, value):
    with (OUT / name).open("xb") as handle:
        handle.write(json_bytes(value))


def inventory(profile):
    markers = sorted(profile.backup_dir.glob("*/*/backup.json"))
    with checkpoint_locks([path.parent for path in markers]):
        manifests = {str(path): sha256(path.read_bytes()) for path in markers}
    retention = profile.state_file.parent / "working-retention"
    return {
        "at": datetime.now(UTC).isoformat(),
        "manifests": manifests,
        "checkpoint_records": archive.load_state(profile)["checkpoints"],
        "journals": {
            str(p): sha256(p.read_bytes()) for p in sorted((retention / "journals").glob("*.json"))
        },
        "plans": [p.stem for p in sorted((retention / "plans").glob("*.json"))],
    }


def main():
    release = json.loads((OUT / "release.json").read_bytes())
    assert all(sha256((OUT / "release" / p).read_bytes()) == h for p, h in release["files"].items())
    profile = archive.Profile.load(Path("/home/yoonhc/.config/marketdata/restic-archive.toml"))
    profile.guard()
    client = archive.Restic(profile, OUT / "metadata-commands")
    client.identify()
    snapshots_before = client.snapshots()
    before = inventory(profile)
    save("before.json", before)
    save("snapshots-before.json", snapshots_before)
    command = [
        str(ROOT / ".venv/bin/python"),
        str(OUT / "release/scripts/run_checkpoint_schedule.py"),
        "--settings",
        "/home/yoonhc/.config/marketdata/checkpoint-schedule.toml",
        "preview",
    ]
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    with (
        (OUT / "preview.stdout").open("xb") as stdout,
        (OUT / "preview.stderr").open("xb") as stderr,
    ):
        process = subprocess.run(command, cwd="/home/yoonhc", env=env, stdout=stdout, stderr=stderr)
    preview = json.loads((OUT / "preview.stdout").read_bytes())
    save("preview-command.json", {"command": command, "returncode": process.returncode})
    save("preview.json", preview)
    after = inventory(profile)
    snapshots_after = client.snapshots()
    assert {s["id"]: s for s in snapshots_before} == {s["id"]: s for s in snapshots_after}
    assert before["manifests"] == after["manifests"]
    assert before["checkpoint_records"] == after["checkpoint_records"]
    assert before["journals"] == after["journals"]
    assert not preview["deletion_attempted"] and preview["deleted_checkpoints"] == 0
    grant = json.loads(
        Path("/home/yoonhc/.config/marketdata/checkpoint-retention-authorization.json").read_bytes()
    )
    assert grant["enabled"] is False and grant["approved_at"] is None
    units = ["marketdata-checkpoint-retirement.service", "marketdata-checkpoint-retirement.timer"]
    assert not any((Path("/home/yoonhc/.config/systemd/user") / unit).exists() for unit in units)
    original = json.loads(
        Path("/tmp/checkpoint-automation-operating-source-before.json").read_bytes()
    )
    assert all(sha256((ROOT / name).read_bytes()) == h for name, h in original["files"].items())
    ready = preview["state"] in {"preview_ready", "no_candidates"}
    new_plans = sorted(set(after["plans"]) - set(before["plans"]))
    if ready:
        assert process.returncode == 0 and new_plans == [preview["plan_id"]]
        assert preview["plan"]["automation"] == {
            "run_id": preview["run_id"],
            "scope_id": grant["scope_id"],
        }
        assert preview["plan"]["code"] == grant["scope"]["code"]
    else:
        assert process.returncode == 75 and preview["state"] == "deferred" and not new_plans
    save("after.json", after)
    save("snapshots-after.json", snapshots_after)
    save("operating-source-before.json", original)
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "ok": ready,
        "state": preview["state"],
        "plan_id": preview["plan_id"],
        "run_id": preview["run_id"],
        "scope_id": grant["scope_id"],
        "code_id": grant["scope"]["code"]["code_id"],
        "candidate_count": len(preview.get("plan", {}).get("candidates", [])) if ready else None,
        "keep_count": len(preview.get("plan", {}).get("keep", [])) if ready else None,
        "excluded_count": len(preview.get("plan", {}).get("excluded", [])) if ready else None,
        "unchanged_manifests": len(before["manifests"]),
        "unchanged_snapshots": len(snapshots_after),
        "archive_checkpoint_records_unchanged": True,
        "retirement_journals_unchanged": True,
        "operating_source_files_unchanged": len(original["files"]),
        "new_plans": new_plans,
        "operational_deletions": 0,
        "timer_installed": False,
        "recurring_deletion_authorized": False,
        "full_retained_file_verification_run": False,
        "source_full_validation_or_restic_read_data_rerun": False,
        "restore_run": False,
        "forget_or_prune_run": False,
    }
    save("preview-results.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
