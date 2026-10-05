"""Activate only the user's approved fixed scope and daily checkpoint timer."""

import json
import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from preflight import (
    CANDIDATE,
    EXISTING,
    OUT,
    RELEASE,
    ROOT,
    UNIT_ROOT,
    UNITS,
    command,
    digest,
    properties,
)

GRANT = Path("/home/yoonhc/.config/marketdata/checkpoint-retention-authorization.json")
SETTINGS = Path("/home/yoonhc/.config/marketdata/checkpoint-schedule.toml")
ZONE = ZoneInfo("Asia/Seoul")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def replace_grant(value):
    temporary = GRANT.with_name(f".{GRANT.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, GRANT)
    directory = os.open(GRANT.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def main():
    os.environ.update(TZ="Asia/Seoul", LC_ALL="C")
    preflight = json.loads((OUT / "preflight.json").read_bytes())
    assert preflight["ok"] and preflight["login"]["Linger"] == "yes"
    assert datetime.now(UTC) - datetime.fromisoformat(preflight["checked_at"]) < timedelta(hours=1)
    release = json.loads((CANDIDATE / "release.json").read_bytes())
    assert all(digest(RELEASE / name) == sha for name, sha in release["files"].items())
    assert all(digest(Path(name)) == sha for name, sha in preflight["configuration_sha256"].items())
    assert all(
        digest(Path(name)) == sha for name, sha in preflight["existing_unit_file_sha256"].items()
    )
    original = json.loads((CANDIDATE / "operating-source-before.json").read_bytes())
    assert all(digest(ROOT / name) == sha for name, sha in original["files"].items())
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    proposal = subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            str(RELEASE / "scripts/run_checkpoint_schedule.py"),
            "--settings",
            str(SETTINGS),
            "propose",
        ],
        cwd="/home/yoonhc",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    grant = json.loads(GRANT.read_bytes())
    assert json.loads(proposal.stdout) == grant
    assert grant["scope_id"] == preflight["scope_id"] and grant["enabled"] is False
    assert all(not os.path.lexists(UNIT_ROOT / unit) for unit in UNITS)
    for unit in UNITS:
        props = properties(
            command(
                "activate-before-" + unit,
                [
                    "systemctl",
                    "--user",
                    "show",
                    unit,
                    "--property=LoadState,ActiveState,DropInPaths",
                ],
            )
        )
        assert props["LoadState"] == "not-found" and props["ActiveState"] == "inactive"
        assert not props["DropInPaths"]
    write_json(OUT / "authorization-before.json", grant)
    approved_at = datetime.now(UTC).isoformat()
    approval = {
        "recorded_at": approved_at,
        "user_message": "오케이 승인할게",
        "interpretation": "Approval of the immediately preceding fixed release, recurring deletion scope and daily 15:40 KST timer activation.",
        "scope_id": grant["scope_id"],
        "code_id": grant["scope"]["code"]["code_id"],
        "review_document": str(CANDIDATE / "approval-review.md"),
        "review_document_sha256": digest(CANDIDATE / "approval-review.md"),
        "candidate_report": str(CANDIDATE / "summary.md"),
        "candidate_report_sha256": digest(CANDIDATE / "summary.md"),
        "manual_plan_approval_reused": False,
        "exclusions": [
            "source",
            "pre-enrollment checkpoints",
            "restic snapshots",
            "test spaces",
            "recovery kit",
            "forget",
            "prune",
        ],
        "timestamp_meaning": "Time this explicit approval was recorded, not an inferred message delivery time.",
    }
    write_json(OUT / "approval.json", approval)
    grant.update(
        enabled=True, approved_at=approved_at, approval_reference=str(OUT / "approval.json")
    )
    changed_grant = False
    try:
        replace_grant(grant)
        changed_grant = True
        write_json(OUT / "authorization-enabled.json", grant)
        for unit in UNITS:
            with (UNIT_ROOT / unit).open("xb") as handle:
                handle.write((RELEASE / "integrations/systemd" / unit).read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            (UNIT_ROOT / unit).chmod(0o644)
        command("daemon-reload", ["systemctl", "--user", "daemon-reload"])
        command(
            "installed-unit-verify",
            ["systemd-analyze", "--user", "verify", *[str(UNIT_ROOT / u) for u in UNITS]],
        )
        for unit in UNITS:
            props = properties(
                command(
                    "installed-" + unit,
                    [
                        "systemctl",
                        "--user",
                        "show",
                        unit,
                        "--property=Id,LoadState,ActiveState,SubState,FragmentPath,DropInPaths,User,Group,ExecStart,EnvironmentFiles,Environment,WorkingDirectory,Restart,SuccessExitStatus,UMask,Nice,IOSchedulingClass,IOSchedulingPriority,TimeoutStartUSec,TimeoutStopUSec,KillSignal,Persistent,TimersCalendar",
                    ],
                )
            )
            assert props["LoadState"] == "loaded"
            assert props["FragmentPath"] == str(UNIT_ROOT / unit) and not props["DropInPaths"]
            assert digest(UNIT_ROOT / unit) == digest(RELEASE / "integrations/systemd" / unit)
            if unit.endswith(".timer"):
                assert props["Persistent"] == "no" and "06:40:00" in props["TimersCalendar"]
            else:
                assert props["WorkingDirectory"] == "/home/yoonhc"
                assert props["Restart"] == "no" and props["UMask"] == "0077"
                assert "checkpoint-retirement-service.env" in props["EnvironmentFiles"]
        command("enable-timer", ["systemctl", "--user", "enable", "--now", UNITS[1]])
        timer = properties(
            command(
                "timer-after",
                [
                    "systemctl",
                    "--user",
                    "show",
                    UNITS[1],
                    "--property=Id,LoadState,UnitFileState,ActiveState,SubState,NextElapseUSecRealtime,LastTriggerUSec,Persistent,AccuracyUSec,TimersCalendar,FragmentPath,DropInPaths",
                ],
            )
        )
        service = properties(
            command(
                "service-after",
                [
                    "systemctl",
                    "--user",
                    "show",
                    UNITS[0],
                    "--property=Id,LoadState,ActiveState,SubState,UnitFileState,ExecMainStartTimestamp,ExecMainExitTimestamp,ExecMainStatus,MainPID,InvocationID,Result,FragmentPath,DropInPaths",
                ],
            )
        )
        assert timer["UnitFileState"] == "enabled" and timer["ActiveState"] == "active"
        assert timer["SubState"] == "waiting" and not timer["LastTriggerUSec"]
        assert service["ActiveState"] == "inactive" and service["MainPID"] == "0"
        assert not service["ExecMainStartTimestamp"]
        current = datetime.now(ZONE)
        next_time = current.replace(hour=15, minute=40, second=0, microsecond=0)
        if next_time <= current:
            next_time += timedelta(days=1)
        assert timer["NextElapseUSecRealtime"] == next_time.strftime("%a %Y-%m-%d %H:%M:%S %Z")
        assert json.loads(GRANT.read_bytes()) == grant
        assert all(
            digest(Path(name)) == sha
            for name, sha in preflight["existing_unit_file_sha256"].items()
        )
        assert all(digest(ROOT / name) == sha for name, sha in original["files"].items())
        probe = json.loads((OUT / "service-preflight.json").read_bytes())
        assert all(digest(Path(name)) == sha for name, sha in probe["manifests"].items())
        assert all(
            digest(Path(name)) == value["sha256"]
            for name, value in probe["retirement_journals"].items()
        )
        command(
            "existing-services-after",
            [
                "systemctl",
                "--user",
                "show",
                *EXISTING,
                "--property=Id,LoadState,ActiveState,SubState,UnitFileState,MainPID,ExecMainStartTimestamp,NextElapseUSecRealtime",
            ],
        )
        result = {
            "activated_at": datetime.now(UTC).isoformat(),
            "ok": True,
            "release_id": release["release_id"],
            "code_id": grant["scope"]["code"]["code_id"],
            "scope_id": grant["scope_id"],
            "authorization_enabled": True,
            "next_scheduled_kst": next_time.isoformat(),
            "timer": timer,
            "service": service,
            "linger": "yes",
            "scheduled_retirement_executions": 0,
            "operational_deletions": 0,
            "existing_unit_files_unchanged": len(preflight["existing_unit_file_sha256"]),
            "operating_source_files_unchanged": len(original["files"]),
            "local_manifests_unchanged": len(probe["manifests"]),
            "retirement_journals_unchanged": True,
            "first_scheduled_result": "pending",
            "post_retirement_next_daily_restic": "pending",
        }
        write_json(OUT / "activation.json", result)
        print(
            json.dumps({k: v for k, v in result.items() if k not in {"timer", "service"}}, indent=2)
        )
    except BaseException as exc:
        rollback = {}
        if changed_grant:
            # Stop only the newly authorized timer and disable this recurring grant.
            stopped = subprocess.run(
                ["systemctl", "--user", "disable", "--now", UNITS[1]],
                capture_output=True,
                text=True,
            )
            rollback["timer_disable"] = {
                "returncode": stopped.returncode,
                "stdout": stopped.stdout,
                "stderr": stopped.stderr,
            }
            grant["enabled"] = False
            replace_grant(grant)
            rollback["grant_enabled"] = False
        write_json(
            OUT / "activation-failure.json",
            {
                "at": datetime.now(UTC).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
                "rollback": rollback,
            },
        )
        raise


if __name__ == "__main__":
    main()
