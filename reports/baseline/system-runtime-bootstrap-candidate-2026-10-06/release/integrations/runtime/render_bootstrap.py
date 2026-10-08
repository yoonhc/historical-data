"""Render a complete inert installation tree; never install or run services."""

import argparse
import hashlib
import importlib.util
import json
import pwd
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "collector_units", Path(__file__).with_name("render_units.py")
)
units = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(units)


def render(runtime, runtime_bytes, inventory, boot_path="/etc/marketdata/clock-bootstrap.json"):
    root = runtime["release_root"]
    base = f"{root}/.venv/bin/python -I {root}/integrations/runtime/clock_gate.py"
    guard = f"{base} check --profile {boot_path}"
    dropin = f"[Service]\nExecStartPre={guard}\n"
    user = pwd.getpwuid(runtime["uid"]).pw_name
    files = {
        "system/marketdata-collector@.service": units.render(
            runtime, "/etc/marketdata/runtime.json", boot_path
        ),
        "system/chrony.service.d/marketdata.conf": (
            f"[Service]\nExecStartPre=!{base} rtc-check --profile {boot_path}\n"
            "ExecStart=\nExecStart=!/usr/sbin/chronyd -F 1 -f /etc/chrony/marketdata.conf\n"
        ),
        "system/chrony-wait.service.d/marketdata.conf": (
            "[Unit]\nWants=marketdata-clock-bootstrap.service\n"
            "After=marketdata-clock-bootstrap.service\n"
        ),
        "system/marketdata-clock-bootstrap.service": f"""[Unit]
Description=Prepare this boot's clock before releasing marketdata jobs
Wants=network-online.target chrony.service user@{runtime["uid"]}.service
After=network-online.target chrony.service user@{runtime["uid"]}.service
Before=chrony-wait.service time-sync.target
StartLimitIntervalSec=0

[Service]
Type=oneshot
ExecStart={base} bootstrap --profile {boot_path}
RemainAfterExit=yes
Restart=on-failure
RestartSec=15s
TimeoutStartSec=150s
TimeoutStopSec=10s
StateDirectory=marketdata-clock
StateDirectoryMode=0755
UMask=0022
NoNewPrivileges=yes
CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_SETUID CAP_SETGID
ProtectClock=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
StandardOutput=journal
StandardError=journal
SyslogLevelPrefix=yes

[Install]
WantedBy=multi-user.target
""",
    }
    for name, mode in inventory.items():
        if mode == "guarded":
            files[f"user/{name}.d/90-marketdata-clock.conf"] = dropin
    profile = {
        "schema": 1,
        "runtime_profile": "/etc/marketdata/runtime.json",
        "runtime_profile_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
        "state_dir": "/var/lib/marketdata-clock",
        "chrony_socket": "/run/chrony/chronyd.sock",
        "rtc_confirmation": "/etc/marketdata/rtc-utc-confirmation.json",
        "user": user,
        "uid": runtime["uid"],
        "policy": {
            "step_above_seconds": 0.1,
            "max_initial_correction_seconds": 300.0,
            "source_age_seconds": 30.0,
            "attempt_seconds": 120.0,
            "poll_seconds": 5.0,
            "long_wait_seconds": 600.0,
        },
        "user_units": inventory,
        "system_collectors": [
            "marketdata-collector@depth.service",
            "marketdata-collector@aggtrade.service",
        ],
        "guard_command": guard,
        "guard_files": {
            f"/etc/systemd/{name}": hashlib.sha256(content.encode()).hexdigest()
            for name, content in files.items()
        },
    }
    return files, profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-profile", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = args.runtime_profile.read_bytes()
    files, profile = render(json.loads(data), data, json.loads(args.inventory.read_text()))
    args.output.mkdir(parents=True, exist_ok=False)
    for name, value in files.items():
        p = args.output / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)
    (args.output / "clock-bootstrap.json").write_text(
        json.dumps(profile, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
