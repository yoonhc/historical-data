"""Non-deleting activation preflight under the actual user service manager."""

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
CANDIDATE = OUT.parent / "checkpoint-automation-2026-10-05"
RELEASE = CANDIDATE / "release"
sys.path.insert(0, str(RELEASE))
sys.path.insert(0, str(RELEASE / "packages/marketdata/src"))

from marketdata.checkpoint_locks import checkpoint_locks  # noqa: E402

from scripts import archive_backups as archive  # noqa: E402
from scripts import checkpoint_schedule as schedule  # noqa: E402
from scripts import retire_checkpoints as retirement  # noqa: E402


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    expected_env = dict(
        line.split("=", 1)
        for line in (CANDIDATE / "proposed-settings/checkpoint-retirement-service.env")
        .read_text()
        .splitlines()
        if line and not line.startswith("#")
    )
    assert all(os.environ.get(key) == value for key, value in expected_env.items())
    assert Path.cwd() == Path("/home/yoonhc")
    assert os.getuid() == 1000
    assert Path(sys.executable) == Path(expected_env["MARKETDATA_RETIREMENT_PYTHON"])
    settings = schedule.Settings.load(Path(expected_env["MARKETDATA_RETIREMENT_SETTINGS"]))
    policy = retirement.Policy.load(settings.retention_policy)
    profile = archive.Profile.load(policy.archive_profile)
    expected = json.loads(
        (CANDIDATE / "proposed-settings/checkpoint-retention-authorization.json").read_bytes()
    )
    assert schedule.proposal(settings) == expected
    assert schedule.authorize(settings, policy, profile, "preview")["enabled"] is False
    profile.guard()
    # Only metadata reads. This does not call the checkpoint retirement runner.
    with archive.repository_lock(profile):
        client = archive.Restic(profile, OUT / "preflight-restic-commands")
        version = client.identify()
        snapshots = client.snapshots()
        state = archive.load_state(profile)
        assert not state.get("active_operation")
        assert state["last_operation"]["ok"]
        daily = schedule.daily_evidence(
            profile, state, datetime.now(schedule.ZONE).date().isoformat()
        )
    markers = sorted(profile.backup_dir.glob("*/*/backup.json"))
    with checkpoint_locks([p.parent for p in markers]):
        manifests = {str(p): digest(p) for p in markers}
    journals = {
        str(p): {"sha256": digest(p), "status": json.loads(p.read_bytes())["status"]}
        for p in sorted((profile.state_file.parent / "working-retention/journals").glob("*.json"))
    }
    assert all(j["status"] == "complete" for j in journals.values())
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "ok": True,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "cwd": os.getcwd(),
        "python": sys.executable,
        "module": str(Path(schedule.__file__).resolve()),
        "invocation_id": os.environ.get("INVOCATION_ID"),
        "environment_file_values_match": True,
        "scope_id": expected["scope_id"],
        "authorization_enabled": False,
        "mount": str(profile.backup_mount),
        "filesystem_uuid": profile.backup_filesystem_uuid,
        "repository_id": profile.expected_repository_id,
        "restic_version": version,
        "password_file_access": "profile owner/mode/nonempty checks and actual repository decryption passed",
        "daily_evidence": daily,
        "snapshot_ids": sorted(s["id"] for s in snapshots),
        "manifests": manifests,
        "retirement_journals": journals,
        "retirement_runner_executed": False,
        "deletion_attempted": False,
    }
    assert result["invocation_id"]
    (OUT / "service-preflight.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"manifests", "retirement_journals"}}
        )
    )


if __name__ == "__main__":
    main()
