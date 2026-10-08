"""Capture host startup evidence without changing units, clocks, or networking."""

import gzip
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PROPS = ",".join((
    "Id", "LoadState", "ActiveState", "SubState", "UnitFileState", "Result",
    "After", "Before", "Wants", "Requires", "FragmentPath", "DropInPaths",
    "MainPID", "ExecMainStatus", "ExecMainStartTimestamp", "ExecMainStartTimestampMonotonic",
    "ExecMainExitTimestamp", "ExecMainExitTimestampMonotonic",
    "ActiveEnterTimestampMonotonic", "ConditionResult", "ExecStart",
))


def stamp():
    return datetime.now(timezone.utc).isoformat()


def run(args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30)
    return {"command": args, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def main():
    started = stamp()
    commands = {
        "version": ["systemctl", "--version"],
        "user_units": ["systemctl", "--user", "show", "marketdata-depth.service",
                       "marketdata-aggtrade.service", "network-online.target", "time-sync.target",
                       "--property=" + PROPS],
        "system_units": ["systemctl", "show", "network-online.target", "time-sync.target",
                         "time-set.target", "NetworkManager-wait-online.service",
                         "systemd-time-wait-sync.service", "systemd-timesyncd.service",
                         "NetworkManager.service", f"user@{os.getuid()}.service",
                         "--property=" + PROPS],
        "time_status": ["timedatectl", "show", "--all"],
        "timesync_status": ["timedatectl", "show-timesync", "--all"],
        "login_state": ["loginctl", "show-user", str(os.getuid()),
                        "--property=Linger,State,RuntimePath"],
        "wired_state": ["nmcli", "-g", "GENERAL.DEVICE,GENERAL.STATE,IP4.GATEWAY,IP4.DNS",
                        "device", "show", "enp2s0"],
        "network_chain": ["systemd-analyze", "critical-chain", "network-online.target"],
        "time_chain": ["systemd-analyze", "critical-chain", "time-sync.target"],
        "user_chain": ["systemd-analyze", "critical-chain", f"user@{os.getuid()}.service"],
    }
    results = {key: run(args) for key, args in commands.items()}
    assert results["user_units"]["returncode"] == 0, results["user_units"]["stderr"]
    assert results["system_units"]["returncode"] == 0, results["system_units"]["stderr"]
    files = {}
    paths = [Path("/etc/adjtime"), Path("/etc/systemd/timesyncd.conf"),
             Path("/etc/os-release"), Path("/proc/sys/kernel/random/boot_id")]
    for unit in ("marketdata-depth.service", "marketdata-aggtrade.service"):
        paths += [Path.home() / ".config/systemd/user" / unit, ROOT / "integrations/systemd" / unit]
    for unit in ("systemd-timesyncd.service", "systemd-time-wait-sync.service",
                 "NetworkManager-wait-online.service", "network-online.target", "time-sync.target", "user@.service"):
        paths.append(Path("/usr/lib/systemd/system") / unit)
    for path in paths:
        raw = path.read_bytes()
        files[str(path)] = {"sha256": hashlib.sha256(raw).hexdigest(), "text": raw.decode(),
                            "mtime_ns": path.stat().st_mtime_ns}
    dropins = {}
    for name in ("/etc/systemd/timesyncd.conf.d", "/run/systemd/timesyncd.conf.d",
                 "/usr/lib/systemd/timesyncd.conf.d"):
        dropins[name] = {str(p): p.read_text() for p in Path(name).glob("*.conf")}
    markers = {}
    for name in ("/run/systemd/timesync/synchronized", "/var/lib/systemd/timesync/clock"):
        path = Path(name)
        markers[name] = {"exists": path.exists()}
        if path.exists():
            stat = path.stat()
            markers[name].update(mtime_ns=stat.st_mtime_ns, size=stat.st_size, uid=stat.st_uid)
    journal_args = ["journalctl", "--boot=0", "--since=2026-10-05 10:58:00 UTC",
                    "--until=2026-10-05 11:05:00 UTC", "--no-pager", "-o", "json",
                    "--output-fields=__REALTIME_TIMESTAMP,__MONOTONIC_TIMESTAMP,_BOOT_ID,_PID,_SYSTEMD_UNIT,_SYSTEMD_USER_UNIT,MESSAGE"]
    journals = {}
    for label, extra in {
        "startup-system": ["-u", f"user@{os.getuid()}.service", "-u", "NetworkManager-wait-online.service",
                           "-u", "systemd-time-wait-sync.service", "-u", "systemd-timesyncd.service"],
        "startup-user": ["--user"],
    }.items():
        result = run(journal_args + extra)
        raw = result.pop("stdout").encode()
        path = OUT / (label + ".jsonl.gz")
        with path.open("xb") as handle:
            handle.write(gzip.compress(raw, mtime=0))
        journals[label] = {**result, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(raw.splitlines()),
                           "path": path.name}
    result = {"started_at": started, "finished_at": stamp(), "commands": results,
              "files": files, "timesync_dropins": dropins, "markers": markers, "journals": journals,
              "settings_changed": False, "services_restarted": [], "new_network_probes": 0}
    with (OUT / "host.json").open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"finished_at": result["finished_at"],
                      "returncodes": {k: v["returncode"] for k, v in results.items()},
                      "journal_rows": {k: v["rows"] for k, v in journals.items()}}))


if __name__ == "__main__":
    main()
