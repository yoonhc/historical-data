"""Check the approved release, login persistence, and non-deleting service probe."""

import hashlib
import json
import os
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
CANDIDATE = OUT.parent / "checkpoint-automation-2026-10-05"
RELEASE = CANDIDATE / "release"
UNIT_ROOT = Path("/home/yoonhc/.config/systemd/user")
UNITS = ("marketdata-checkpoint-retirement.service", "marketdata-checkpoint-retirement.timer")
EXISTING = (
    "marketdata-depth.service",
    "marketdata-aggtrade.service",
    "marketdata-backup.timer",
    "marketdata-daily.timer",
    "marketdata-restic-archive.timer",
    "marketdata-pack.timer",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(name, arguments, *, timeout=60):
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    value = {
        "command": arguments,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    (OUT / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n")
    result.check_returncode()
    return result.stdout


def properties(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def main():
    release = json.loads((CANDIDATE / "release.json").read_bytes())
    assert all(digest(RELEASE / name) == sha for name, sha in release["files"].items())
    assert digest(CANDIDATE / "source.tar.gz") == release["source_archive_sha256"]
    assert digest(CANDIDATE / "candidate.patch") == release["patch_sha256"]
    original = json.loads((CANDIDATE / "operating-source-before.json").read_bytes())
    assert all(digest(ROOT / name) == sha for name, sha in original["files"].items())
    configs = {}
    for copy in sorted((CANDIDATE / "proposed-settings").iterdir()):
        actual = Path("/home/yoonhc/.config/marketdata") / copy.name
        assert actual.read_bytes() == copy.read_bytes()
        assert actual.stat().st_uid == os.getuid() == 1000
        assert stat.S_IMODE(actual.stat().st_mode) == 0o600
        configs[str(actual)] = digest(actual)
    proposed = json.loads(
        (CANDIDATE / "proposed-settings/checkpoint-retention-authorization.json").read_bytes()
    )
    assert proposed["enabled"] is False
    assert (
        proposed["scope_id"] == "8e1b3059c148f2ca235ec44faf317f8d680f5a846d4445327363534cfb297c5b"
    )
    for unit in UNITS:
        assert not os.path.lexists(UNIT_ROOT / unit)
        props = properties(
            command(
                unit + "-before",
                [
                    "systemctl",
                    "--user",
                    "show",
                    unit,
                    "--property=Id,LoadState,ActiveState,UnitFileState,FragmentPath,DropInPaths",
                ],
            )
        )
        assert props["LoadState"] == "not-found" and props["ActiveState"] == "inactive"
        assert not props["DropInPaths"]
    login = properties(
        command(
            "login",
            [
                "loginctl",
                "show-user",
                "yoonhc",
                "--property=UID",
                "--property=Name",
                "--property=Linger",
                "--property=State",
                "--property=RuntimePath",
            ],
        )
    )
    assert login["UID"] == "1000" and login["Name"] == "yoonhc" and login["Linger"] == "yes"
    command(
        "unit-verify",
        [
            "systemd-analyze",
            "--user",
            "verify",
            *[str(RELEASE / "integrations/systemd" / unit) for unit in UNITS],
        ],
    )
    command(
        "existing-services-before",
        [
            "systemctl",
            "--user",
            "show",
            *EXISTING,
            "--property=Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,DropInPaths,MainPID,ExecMainStartTimestamp,NextElapseUSecRealtime",
        ],
    )
    installed_files = {str(p): digest(p) for p in sorted(UNIT_ROOT.rglob("*")) if p.is_file()}

    # Derive the transient preflight environment and scheduling properties from the approved unit.
    # Replace only ExecStart with a read-only probe; never start the deleting service manually.
    options = []
    environments = []
    in_service = False
    for line in (RELEASE / "integrations/systemd" / UNITS[0]).read_text().splitlines():
        if line.startswith("["):
            in_service = line == "[Service]"
        elif in_service and "=" in line:
            key, value = line.split("=", 1)
            if key == "Environment":
                environments.append(value)
            elif key not in {"ExecStart", "StandardOutput", "StandardError"}:
                options.append(f"--property={key}={value.replace('%h', '/home/yoonhc')}")
    options.append("--property=Environment=" + " ".join(environments))
    command(
        "service-probe-command",
        [
            "systemd-run",
            "--user",
            "--unit=marketdata-checkpoint-retirement-preflight-20261005",
            "--wait",
            "--pipe",
            "--quiet",
            "--expand-environment=yes",
            *options,
            "/usr/bin/env",
            "${MARKETDATA_RETIREMENT_PYTHON}",
            str(OUT / "service_probe.py"),
        ],
    )
    probe = json.loads((OUT / "service-preflight.json").read_bytes())
    assert probe["ok"] and probe["scope_id"] == proposed["scope_id"]
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "ok": True,
        "release_id": release["release_id"],
        "code_id": proposed["scope"]["code"]["code_id"],
        "scope_id": proposed["scope_id"],
        "release_files_match": len(release["files"]),
        "operating_files_unchanged": len(original["files"]),
        "configuration_sha256": configs,
        "existing_unit_file_sha256": installed_files,
        "login": login,
        "user_service_probe_ok": probe["ok"],
        "unit_verify": "passed",
        "grant_enabled": False,
        "timer_installed": False,
        "deletion_attempted": False,
    }
    (OUT / "preflight.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in {"configuration_sha256", "existing_unit_file_sha256"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
