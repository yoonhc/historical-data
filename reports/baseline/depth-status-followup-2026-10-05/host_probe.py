"""Read actual systemd/proc/journal and depth-status; never restart services."""

import gzip
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def save(name, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode()
    with (OUT / name).open("xb") as f:
        f.write(raw + b"\n")


def run(args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=45)
    return result


def main():
    started = stamp()
    properties = "Id,ActiveState,SubState,MainPID,ExecMainStartTimestamp,ExecMainExitTimestamp,ExecMainCode,ExecMainStatus,Result,NRestarts,ExecStart,WorkingDirectory,Restart,RestartUSec,FragmentPath,DropInPaths"
    units = ["marketdata-depth.service", "marketdata-aggtrade.service", "marketdata-pack.service",
             "marketdata-backup.service", "marketdata-restic-archive.service"]
    proc = run(["systemctl", "--user", "show", *units, "--property=" + properties])
    assert proc.returncode == 0, proc.stderr
    services = {}
    for block in proc.stdout.strip().split("\n\n"):
        item = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        services[item["Id"]] = item
    pid = int(services["marketdata-depth.service"]["MainPID"])
    p = Path("/proc") / str(pid)
    env = dict(v.split("=", 1) for v in (p / "environ").read_bytes().decode().split("\0") if "=" in v)
    process = {"pid": pid, "cmdline": (p / "cmdline").read_bytes().decode().split("\0"),
               "cwd": os.readlink(p / "cwd"), "exe": os.readlink(p / "exe"),
               "selected_environment": {k: env[k] for k in ("SESSION_SECONDS", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV") if k in env}}
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    boot_seconds = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines() if line.startswith("btime "))
    boots = run(["journalctl", "--list-boots", "--no-pager"])
    journals = {}
    queries = {
        "depth": ["--user-unit=marketdata-depth.service", "--since=2026-10-05 05:00:00 UTC"],
        "aggtrade-boundary": ["--user-unit=marketdata-aggtrade.service", "--since=2026-10-05 10:58:00 UTC", "--until=2026-10-05 11:03:00 UTC"],
        "system-boundary": ["--since=2026-10-05 10:58:00 UTC", "--until=2026-10-05 11:03:00 UTC",
                            "-u", "systemd-logind.service", "-u", "NetworkManager.service", "-u", "systemd-resolved.service"],
    }
    for label, args in queries.items():
        command = ["journalctl", *args, "--no-pager", "-o", "json",
                   "--output-fields=__REALTIME_TIMESTAMP,__MONOTONIC_TIMESTAMP,_BOOT_ID,_PID,_SYSTEMD_INVOCATION_ID,_SYSTEMD_USER_UNIT,MESSAGE"]
        result = run(command)
        assert result.returncode == 0, result.stderr
        raw = result.stdout.encode()
        (OUT / (label + ".journal.jsonl.gz")).write_bytes(gzip.compress(raw, mtime=0))
        rows = [json.loads(line) for line in result.stdout.splitlines() if line]
        important, phases, final_reports = [], 0, []
        for r in rows:
            message = r.get("MESSAGE", "")
            if not isinstance(message, str):
                continue
            if '"phase":"depth_stream"' in message or '"phase": "depth_stream"' in message:
                phases += 1
                continue
            parsed = None
            if message.startswith("{"):
                try:
                    parsed = json.loads(message)
                except ValueError:
                    pass
            if isinstance(parsed, dict) and "epochs" in parsed:
                final_reports.append({"at": r.get("__REALTIME_TIMESTAMP"), "boot_id": r.get("_BOOT_ID"),
                                      "pid": r.get("_PID"), "report_id": parsed.get("id"),
                                      "state": parsed.get("state"), "messages": parsed.get("messages"),
                                      "persisted_messages": parsed.get("persisted_messages"),
                                      "error": parsed.get("error"), "errors": parsed.get("errors"),
                                      "flush_error": parsed.get("flush_error")})
                continue
            if label != "depth" or any(s in message.lower() for s in ("start", "stop", "exit", "fail", "error", "interrupt", "reboot", "shutdown")):
                important.append({**{k: v for k, v in r.items() if k != "MESSAGE"}, "MESSAGE": message[:4000]})
        journals[label] = {"command": command, "rows": len(rows), "sha256": hashlib.sha256(raw).hexdigest(),
                           "compressed_file": label + ".journal.jsonl.gz", "progress_rows": phases,
                           "final_reports": final_reports, "important": important, "stderr": result.stderr}
    samples = []
    for n in range(3):
        result = run([str(ROOT / ".venv/bin/marketdata"), "--root", str(ROOT), "depth-status"])
        assert result.returncode in (0, 2), result.stderr
        status = json.loads(result.stdout)
        latest = json.loads((ROOT / "state/depth-latest.json").read_bytes())
        with (OUT / f"status-{n}.json.gz").open("xb") as f:
            f.write(gzip.compress(result.stdout.encode(), mtime=0))
        r = status.get("report", {})
        samples.append({"checked_at": stamp(), "returncode": result.returncode, "healthy": status["healthy"],
                        "state": status["state"], "age_seconds": status.get("last_receive_age_seconds"),
                        "id": r.get("id"), "messages": r.get("messages"), "persisted_messages": r.get("persisted_messages"),
                        "sync_losses": r.get("sync_losses"), "connection_errors": r.get("connection_errors"),
                        "db_and_json_report_equal": r == latest, "failure_notice": r.get("failure_notice"),
                        "flush_error": r.get("flush_error")})
        if n != 2:
            time.sleep(3)
    release = json.loads((OUT.parent / "depth-status-release-2026-10-05/release.json").read_bytes())
    hashes = {}
    for entry in release["changed_files"]:
        f = ROOT / entry["path"]
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        assert digest == entry["after_sha256"]
        hashes[entry["path"]] = {"sha256": digest, "mtime_ns": f.stat().st_mtime_ns}
    save("host.json", {"started_at": started, "finished_at": stamp(), "services": services, "process": process,
                       "current_boot_id": boot_id, "current_boot_started_at": datetime.fromtimestamp(boot_seconds, timezone.utc).isoformat(),
                       "boot_list": {"returncode": boots.returncode, "stdout": boots.stdout, "stderr": boots.stderr},
                       "journals": journals, "live_status_samples": samples, "release_files": hashes,
                       "service_actions": [], "network_requests": 0})
    print(json.dumps({"depth_service": services["marketdata-depth.service"]["ActiveState"],
                      "boot_at": datetime.fromtimestamp(boot_seconds, timezone.utc).isoformat(),
                      "healthy_samples": sum(s["healthy"] for s in samples), "samples": len(samples),
                      "journal_final_reports": len(journals["depth"]["final_reports"])}), flush=True)


if __name__ == "__main__":
    main()
