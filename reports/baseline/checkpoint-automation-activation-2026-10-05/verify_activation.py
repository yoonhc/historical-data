"""Read-only post-activation checks; do not invoke the retirement service."""

import json
from datetime import UTC, datetime
from pathlib import Path

from preflight import CANDIDATE, OUT, RELEASE, ROOT, UNIT_ROOT, UNITS, command, properties
from service_probe import archive, digest, retirement, schedule


def main():
    activation = json.loads((OUT / "activation.json").read_bytes())
    preflight = json.loads((OUT / "preflight.json").read_bytes())
    probe = json.loads((OUT / "service-preflight.json").read_bytes())
    settings = schedule.Settings.load(
        Path("/home/yoonhc/.config/marketdata/checkpoint-schedule.toml")
    )
    policy = retirement.Policy.load(settings.retention_policy)
    profile = archive.Profile.load(policy.archive_profile)
    authorized = schedule.authorize(settings, policy, profile, "run")
    assert authorized["enabled"] and authorized["scope_id"] == activation["scope_id"]
    approval = json.loads(Path(authorized["approval_reference"]).read_bytes())
    assert digest(Path(approval["review_document"])) == approval["review_document_sha256"]
    assert digest(Path(approval["candidate_report"])) == approval["candidate_report_sha256"]
    for unit in UNITS:
        assert digest(UNIT_ROOT / unit) == digest(RELEASE / "integrations/systemd" / unit)
    with archive.repository_lock(profile):
        client = archive.Restic(profile, OUT / "post-activation-restic-commands")
        client.identify()
        snapshots = sorted(s["id"] for s in client.snapshots())
    assert snapshots == probe["snapshot_ids"]
    assert all(digest(Path(name)) == sha for name, sha in probe["manifests"].items())
    assert all(
        digest(Path(name)) == value["sha256"]
        for name, value in probe["retirement_journals"].items()
    )
    original = json.loads((CANDIDATE / "operating-source-before.json").read_bytes())
    assert all(digest(ROOT / name) == sha for name, sha in original["files"].items())
    assert all(
        digest(Path(name)) == sha for name, sha in preflight["existing_unit_file_sha256"].items()
    )
    timer = properties(
        command(
            "verified-timer",
            [
                "systemctl",
                "--user",
                "show",
                UNITS[1],
                "--property=ActiveState,SubState,UnitFileState,NextElapseUSecRealtime,LastTriggerUSec,Persistent,DropInPaths",
            ],
        )
    )
    service = properties(
        command(
            "verified-service",
            [
                "systemctl",
                "--user",
                "show",
                UNITS[0],
                "--property=ActiveState,SubState,MainPID,ExecMainStartTimestamp,ExecMainExitTimestamp,ExecMainStatus,DropInPaths",
            ],
        )
    )
    assert timer["ActiveState"] == "active" and timer["SubState"] == "waiting"
    assert timer["UnitFileState"] == "enabled" and timer["Persistent"] == "no"
    assert not timer["LastTriggerUSec"] and not timer["DropInPaths"]
    assert timer["NextElapseUSecRealtime"] == activation["timer"]["NextElapseUSecRealtime"]
    assert service["ActiveState"] == "inactive" and not service["ExecMainStartTimestamp"]
    assert not service["DropInPaths"]
    runs = [
        json.loads(p.read_bytes())
        for p in (profile.reports_root / "checkpoint-scheduled").glob("*/run.json")
    ]
    actual_runs = [r for r in runs if r.get("action") == "run"]
    assert not actual_runs
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "ok": True,
        "authorization_scope_verified": authorized["scope_id"],
        "authorization_enabled": True,
        "installed_unit_files_match_release": True,
        "timer": timer,
        "service": service,
        "snapshot_ids_unchanged": snapshots,
        "local_manifests_unchanged": len(probe["manifests"]),
        "retirement_journals_unchanged": True,
        "existing_unit_files_unchanged": len(preflight["existing_unit_file_sha256"]),
        "operating_source_files_unchanged": len(original["files"]),
        "scheduled_retirement_executions": 0,
        "operational_deletions": 0,
        "read_data_rerun": False,
        "restore_run": False,
        "forget_or_prune_run": False,
    }
    (OUT / "post-activation-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in {"timer", "service", "snapshot_ids_unchanged"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
