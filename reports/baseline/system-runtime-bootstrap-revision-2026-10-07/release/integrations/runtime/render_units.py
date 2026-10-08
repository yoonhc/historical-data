"""Render inert system units from an explicit machine profile. Never installs units."""

import pwd
import re


def render(profile, profile_path, boot_profile="/etc/marketdata/clock-bootstrap.json"):
    user = pwd.getpwuid(profile["uid"]).pw_name
    if profile["uid"] <= 0 or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise ValueError("Non-root local account required")
    paths = [
        profile["release_root"],
        profile["data_root"],
        profile["evidence_root"],
        str(profile_path),
        str(boot_profile),
    ]
    if any(not re.fullmatch(r"/[A-Za-z0-9_./-]+", p) for p in paths):
        raise ValueError("Unit path needs explicit systemd escaping; simple paths only")
    root, data, evidence, config, boot_config = paths
    run = f"{root}/.venv/bin/python -I {root}/integrations/runtime/marketdata_runtime.py"
    gate = f"{root}/.venv/bin/python -I {root}/integrations/runtime/clock_gate.py check --profile {boot_config}"
    args = f"--profile {config} --stream %i"
    return f"""[Unit]
Description=Market data %i collector with sampled clock quality
Wants=network-online.target chrony.service chrony-wait.service marketdata-clock-bootstrap.service
After=network-online.target chrony.service marketdata-clock-bootstrap.service chrony-wait.service time-sync.target
RequiresMountsFor={data} {root} {evidence}
StartLimitIntervalSec=0

[Service]
Type=exec
User={user}
WorkingDirectory={root}
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStartPre={gate}
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
    raise SystemExit(
        "Use render_bootstrap.py; RTC guard and bootstrap units must be rendered together"
    )


if __name__ == "__main__":
    main()
