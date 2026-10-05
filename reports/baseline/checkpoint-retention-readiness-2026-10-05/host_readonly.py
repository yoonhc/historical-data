"""Capture the selected services' journal and a bounded checkpoint-reader snapshot.

No signals, restarts, mount changes, backup commands, or password reads.
Only process metadata related to this project/checkpoint tree is retained.
"""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
CHECKPOINTS = Path("/mnt/backup/marketdata-historical/automatic")


def save(name, value):
    with (OUT / name).open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def beneath(value, root):
    return value == str(root) or value.startswith(str(root) + "/")


def main():
    started = datetime.now(UTC).isoformat()
    command = [
        "journalctl",
        "--user",
        "--all",
        "--no-pager",
        "--output=json",
        "-u",
        "marketdata-backup.service",
        "-u",
        "marketdata-restic-archive.service",
        "--since",
        "2026-10-04 13:00:00",
        "--until",
        "2026-10-04 14:30:00",
    ]
    journal = subprocess.run(command, check=True, capture_output=True, timeout=30)
    with (OUT / "service-journal.jsonl").open("xb") as handle:
        handle.write(journal.stdout)
    save("journal-command.json", {"command": command, "returncode": 0, "at": started})

    boot = next(
        int(line.split()[1])
        for line in Path("/proc/stat").read_text().splitlines()
        if line.startswith("btime ")
    )
    ticks = os.sysconf("SC_CLK_TCK")
    rows, denied, vanished = [], [], 0
    for proc in sorted(Path("/proc").iterdir()):
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            argv = (proc / "cmdline").read_bytes().decode(errors="replace").split("\0")
            cwd = os.readlink(proc / "cwd")
            executable = os.readlink(proc / "exe")
            relevant = beneath(cwd, ROOT) or any(str(ROOT) in arg for arg in argv)
            references = []
            for fd in (proc / "fd").iterdir():
                try:
                    target = os.readlink(fd)
                except FileNotFoundError:
                    continue
                if beneath(target, CHECKPOINTS):
                    references.append({"fd": fd.name, "path": target})
            cwd_in_checkpoints = beneath(cwd, CHECKPOINTS)
            relevant |= bool(references) or cwd_in_checkpoints
            if not relevant:
                continue
            fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            entrypoints = [
                arg
                for arg in argv
                if arg.startswith(str(ROOT) + "/scripts/")
                or arg.startswith("scripts.")
                or arg.endswith("/marketdata")
            ]
            operations = [
                arg
                for arg in argv
                if arg
                in {
                    "backup",
                    "backup-verify",
                    "restore",
                    "maintenance",
                    "daily",
                    "hourly",
                    "run",
                    "restore-check",
                    "plan",
                    "apply",
                    "stream",
                    "collect-depth",
                    "collect-aggtrades",
                    "stream-depth",
                    "stream-aggtrades",
                }
            ]
            rows.append(
                {
                    "pid": int(proc.name),
                    "executable": executable,
                    "started_at": datetime.fromtimestamp(
                        boot + int(fields[19]) / ticks, UTC
                    ).isoformat(),
                    "cwd": cwd if beneath(cwd, ROOT) or cwd_in_checkpoints else None,
                    "entrypoints": entrypoints,
                    "operations": operations,
                    "checkpoint_references": references,
                    "cwd_in_checkpoints": cwd_in_checkpoints,
                }
            )
        except PermissionError:
            denied.append(int(proc.name))
        except (FileNotFoundError, ProcessLookupError):
            vanished += 1

    parents = {}
    for parent in CHECKPOINTS.iterdir():
        if parent.is_dir():
            info = parent.stat()
            parents[(os.major(info.st_dev), os.minor(info.st_dev), info.st_ino)] = str(parent)
    locks = []
    for line in Path("/proc/locks").read_text().splitlines():
        parts = line.split()
        for token in parts:
            if token.count(":") != 2:
                continue
            try:
                major, minor, inode = token.split(":")
                key = (int(major, 16), int(minor, 16), int(inode))
            except ValueError:
                continue
            if key in parents:
                locks.append({"parent": parents[key], "kernel_record": line})
    result = {
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "uid": os.getuid(),
        "pid_namespace": os.readlink("/proc/self/ns/pid"),
        "same_uid_related_processes": rows,
        "permission_denied_same_uid_pids": denied,
        "processes_vanished_during_scan": vanished,
        "checkpoint_parent_locks": locks,
        "scope": "Host same-UID processes and checkpoint parent inode locks at this instant",
        "limits": "Does not attest imported Python bytecode, other users, or future readers. Repeat immediately before apply.",
        "mutations": [],
    }
    save("reader-snapshot.json", result)
    print(
        json.dumps(
            {
                "journal_lines": len(journal.stdout.splitlines()),
                "related_processes": len(rows),
                "denied": denied,
                "checkpoint_reference_processes": [
                    r["pid"] for r in rows if r["checkpoint_references"] or r["cwd_in_checkpoints"]
                ],
                "parent_locks": len(locks),
            }
        )
    )


if __name__ == "__main__":
    main()
