"""Install only the approved observer and a finite, read-only activation watcher."""

import hashlib
import json
import os
import pwd
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
USER = Path.home()
SERVICE = "marketdata-network-observer.service"
TIMER = "marketdata-network-observer.timer"
WATCHER = "marketdata-depth-refresh-observe-20261002"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args!r}: {result.stdout} {result.stderr}")
    return result.stdout + result.stderr


def protected_files():
    paths = list((USER / ".config/systemd/user").glob("marketdata-*"))
    paths += list((USER / ".config/marketdata").glob("restic*"))
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if p.is_file() and p.name not in (SERVICE, TIMER)
    }


def main():
    target = OUT / "network-install.json"
    assert not target.exists(), "Keep prior installation evidence; inspect before retrying"
    result = {
        "started_at": timestamp(),
        "status": "preflight",
        "uid": os.getuid(),
        "user": pwd.getpwuid(os.getuid()).pw_name,
        "commands": {},
    }

    def save():
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")

    save()
    try:
        assert result["user"] == "yoonhc" and USER == Path("/home/yoonhc")
        result["protected_before"] = protected_files()
        for p in (
            "/usr/bin/python3",
            "/usr/bin/env",
            "/usr/bin/ip",
            "/usr/bin/ping",
            "/usr/bin/getent",
            "/usr/bin/curl",
            str(ROOT / ".venv/bin/python"),
        ):
            assert os.access(p, os.X_OK), f"Cannot execute {p}"
        result["python"] = command(["/usr/bin/python3", "--version"])
        result["linger"] = command(["loginctl", "show-user", "yoonhc", "--property=Linger"])
        assert result["linger"].strip() == "Linger=yes"
        result["unit_validation"] = command(
            [
                "systemd-analyze",
                "--user",
                "verify",
                str(ROOT / "integrations/systemd" / SERVICE),
                str(ROOT / "integrations/systemd" / TIMER),
            ]
        )
        config = USER / ".config/marketdata"
        profile = config / "network-observer.toml"
        environment = config / "network-observer.env"
        units = USER / ".config/systemd/user"
        payloads = {
            profile: ((ROOT / "configs/network-observer.example.toml").read_bytes(), 0o600),
            environment: (
                f"MARKETDATA_NETWORK_PYTHON=/usr/bin/python3\n"
                f"MARKETDATA_NETWORK_SCRIPT={ROOT}/scripts/network_observer.py\n"
                f"MARKETDATA_NETWORK_PROFILE={profile}\n".encode(),
                0o600,
            ),
            units / SERVICE: ((ROOT / "integrations/systemd" / SERVICE).read_bytes(), 0o644),
            units / TIMER: ((ROOT / "integrations/systemd" / TIMER).read_bytes(), 0o644),
        }
        for p in payloads:
            assert not p.exists() and not p.is_symlink(), f"Refuse to overwrite {p}"
        result["status"] = "installing"
        save()
        result["installed"] = {}
        for p, (blob, mode) in payloads.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("xb") as handle:
                os.fchmod(handle.fileno(), mode)
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            assert os.access(p, os.R_OK)
            result["installed"][str(p)] = {
                "sha256": hashlib.sha256(blob).hexdigest(),
                "mode": oct(p.stat().st_mode & 0o777),
            }
        result["commands"]["reload"] = command(["systemctl", "--user", "daemon-reload"])
        # A real oneshot service run uses its configured Python, environment and profile.
        result["commands"]["first_service"] = command(["systemctl", "--user", "start", SERVICE])
        latest = json.loads((ROOT / "reports/network-observer/latest.json").read_bytes())
        assert latest["context"]["uid"] == os.getuid()
        assert latest["context"]["script"] == str(ROOT / "scripts/network_observer.py")
        assert latest["context"]["invocation_id"]
        (OUT / "network-first-service.json").write_text(json.dumps(latest, indent=2) + "\n")
        result["first_probe_status"] = latest["status"]
        result["commands"]["timer"] = command(["systemctl", "--user", "enable", "--now", TIMER])
        result["commands"]["finite_watcher"] = command(
            [
                "systemd-run",
                "--user",
                "--collect",
                "--unit=" + WATCHER,
                "--property=Type=exec",
                "--property=RuntimeMaxSec=75min",
                "--property=Nice=10",
                "--property=WorkingDirectory=" + str(ROOT),
                str(ROOT / ".venv/bin/python"),
                str(OUT / "watch_activation.py"),
            ]
        )
        time.sleep(7)
        result["unit_status"] = command(
            [
                "systemctl",
                "--user",
                "show",
                SERVICE,
                TIMER,
                "--property=Id,ActiveState,SubState,UnitFileState,Result,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp,LastTriggerUSec,NextElapseUSecRealtime,InvocationID",
            ]
        )
        result["collector_status"] = command(
            [
                "systemctl",
                "--user",
                "show",
                "marketdata-depth.service",
                "marketdata-aggtrade.service",
                "--property=Id,ActiveState,MainPID,ExecMainStartTimestamp",
            ]
        )
        result["protected_after"] = protected_files()
        assert result["protected_before"] == result["protected_after"]
        result.update(status="applied", finished_at=timestamp())
        save()
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "first_probe_status": result["first_probe_status"],
                    "unit_status": result["unit_status"],
                }
            ),
            flush=True,
        )
    except Exception as exc:
        result.update(
            status="failed", error=f"{type(exc).__name__}: {exc}", finished_at=timestamp()
        )
        save()
        raise


if __name__ == "__main__":
    main()
