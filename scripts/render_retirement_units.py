"""Render only; never install, enable, unmask, start or stop a unit."""

import argparse
import json
from pathlib import Path


def render(release, settings, state, profile):
    source = profile["store_root"]
    archive_lock = f"{profile['state_root']}/{profile['expected_repository_id']}/archive.lock"
    rw = [
        str(state),
        archive_lock,
        f"{source}/state/maintenance.lock",
        profile["cache_root"],
        f"{profile['restic_repository']}/locks",
    ]
    base = f"""[Unit]
Description=Clock-bounded working checkpoint retirement
After=chrony.service marketdata-clock-bootstrap.service
RequiresMountsFor={source} {profile["backup_mount"]}

[Service]
Type=oneshot
User=yoonhc
Group=yoonhc
WorkingDirectory={state}
Environment=LC_ALL=C
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
UMask=0077
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
TimeoutStartSec=2h
TimeoutStopSec=120s
KillSignal=SIGINT
KillMode=mixed
Restart=no
SuccessExitStatus=75
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=read-only
ProtectClock=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
PrivateDevices=yes
PrivateTmp=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictRealtime=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressDeny=any
IPAddressAllow=localhost
ReadWritePaths={" ".join(rw)}
StandardOutput=journal
StandardError=journal
"""
    for path in [release, settings, state, *rw, source, profile["backup_dir"]]:
        if not Path(path).is_absolute() or any(c in str(path) for c in "\n\r %"):
            raise ValueError("Explicit safe absolute unit paths required")
    execute = f"ExecStart={release}/.venv/bin/python -B -I {release}/scripts/run_clock_retirement.py --settings {settings} "
    # Source and staging must share a single systemd writable bind mount.
    # Separate entries can produce EXDEV even on the same underlying device.
    writable = f"ReadWritePaths={Path(profile['backup_dir']).parent}\n"
    return {
        "marketdata-checkpoint-retirement.service": base + writable + execute + "run\n",
        "marketdata-checkpoint-retirement-preview.service": base + execute + "preview\n",
        "marketdata-checkpoint-retirement-apply@.service": base
        + writable
        + execute
        + "apply-plan --plan-id %i\n",
        "marketdata-checkpoint-retirement.timer": """[Unit]
Description=Evaluate clock-bounded checkpoints at 15:40 KST

[Timer]
OnCalendar=*-*-* 06:40:00 UTC
Persistent=false
AccuracySec=1s
Unit=marketdata-checkpoint-retirement.service

[Install]
WantedBy=timers.target
""",
    }


def evidence_dropins(release, settings, archive_profile):
    """Only replace the two approved ExecStart commands; preserve all prechecks/timers."""
    for value in (release, settings, archive_profile):
        if not Path(value).is_absolute() or any(c in str(value) for c in "\n\r %"):
            raise ValueError("Explicit safe absolute evidence paths required")
    python = f"{release}/.venv/bin/python -B -I"
    return {
        "marketdata-backup.service.d/80-checkpoint-evidence.conf": f"[Service]\nExecStart=\nExecStart={python} {release}/scripts/run_evidenced_backup.py --settings {settings} backup --min-free-gib 10 --max-files 20000\n",
        "marketdata-restic-archive.service.d/80-checkpoint-evidence.conf": f"[Service]\nExecStart=\nExecStart={python} {release}/scripts/run_evidenced_archive.py --profile {archive_profile} run\n",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    value = json.loads(args.input.read_text())
    units = render(value["release"], value["settings"], value["state"], value["profile"])
    args.output.mkdir(parents=True, exist_ok=False)
    for name, content in units.items():
        (args.output / name).write_text(content)


if __name__ == "__main__":
    main()
