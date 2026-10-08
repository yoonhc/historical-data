"""Render inert system units from an explicit machine profile. Never installs units."""

import argparse
import json
import pwd
import re
from pathlib import Path


def render(profile, profile_path):
    user = pwd.getpwuid(profile["uid"]).pw_name
    if profile["uid"] <= 0 or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("Non-root local account required")
    paths = [
        profile["release_root"],
        profile["data_root"],
        profile["evidence_root"],
        str(profile_path),
    ]
    if any(not re.fullmatch(r"/[A-Za-z0-9_./-]+", p) for p in paths):
        raise ValueError("Unit path needs explicit systemd escaping; simple paths only")
    root, data, evidence, config = paths
    run = f"{root}/.venv/bin/python -I {root}/integrations/runtime/marketdata_runtime.py"
    args = f"--profile {config} --stream %i"
    return f"""[Unit]
Description=Market data %i collector with sampled clock quality
Wants=network-online.target chrony.service chrony-wait.service
After=network-online.target chrony.service chrony-wait.service time-sync.target
RequiresMountsFor={data} {root} {evidence}
StartLimitIntervalSec=0

[Service]
Type=exec
User={user}
WorkingDirectory={root}
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStartPre={run} wait-ready {args}
ExecStart={run} run {args}
Restart=always
RestartSec=15s
TimeoutStartSec=150s
TimeoutStopSec=150s
KillSignal=SIGINT
KillMode=mixed
SendSIGKILL=yes
UMask=0077
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectClock=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={data} {evidence}
PrivateTmp=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--installed-profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "marketdata-collector@.service").write_text(
        render(profile, args.installed_profile)
    )
    # Keep distro daemon sandbox + identity; pin the daemon config without starter overrides.
    (args.output / "chrony-override.conf").write_text(
        "[Service]\nExecStart=\nExecStart=!/usr/sbin/chronyd -F 1 -f /etc/chrony/marketdata.conf\n"
    )


if __name__ == "__main__":
    main()
