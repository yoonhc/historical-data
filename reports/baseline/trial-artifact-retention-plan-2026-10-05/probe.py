"""Read-only restic metadata and host usage probe; no data deletion or service action.

Restic may acquire/release its ordinary repository locks. --no-cache avoids cache writes.
Passwords are read only by restic, and their contents/digests are never recorded.
"""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from inventory import ARTIFACTS, BASE, ROOT, digest, now

BINARY = ROOT / ".cache/restic/0.19.1/restic"
PRODUCTION_ID = "b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558"
PILOT_ID = "3cb78902823a4335c5a9fc3ab5635f583c4b4080a8273422a0defdbe3f11effc"
PASSWORD = Path.home() / ".config/marketdata/secrets/restic-password"
KIT_PASSWORD = Path("/mnt/backup/marketdata-recovery-kit-20261003/secrets/restic-password")


def command(args):
    start = time.monotonic()
    result = subprocess.run(args, capture_output=True, check=True, timeout=120,
                            env={**os.environ, "GOMAXPROCS": "2", "TZ": "UTC"})
    return result.stdout, {"argv": [str(x) for x in args], "returncode": result.returncode,
                           "elapsed_seconds": time.monotonic() - start,
                           "stderr": result.stderr.decode()}


def usage(paths):
    matches, denied, vanished = [], set(), set()
    examined = 0
    for pid in Path("/proc").iterdir():
        if not pid.name.isdigit():
            continue
        try:
            if pid.stat().st_uid != os.getuid():
                continue
            examined += 1
            name = (pid / "comm").read_text().strip()
            links = [pid / "cwd", *list((pid / "fd").iterdir())]
            for link in links:
                target = os.readlink(link).removesuffix(" (deleted)")
                if any(target == p or target.startswith(p + "/") for p in paths):
                    matches.append({"pid": int(pid.name), "comm": name,
                                    "reference": str(link), "path": target})
            for line in (pid / "maps").read_text().splitlines():
                parts = line.split(maxsplit=5)
                if len(parts) == 6:
                    target = parts[5].removesuffix(" (deleted)")
                    if any(target == p or target.startswith(p + "/") for p in paths):
                        matches.append({"pid": int(pid.name), "comm": name,
                                        "reference": "maps", "path": target})
        except PermissionError:
            denied.add(int(pid.name))
        except (FileNotFoundError, ProcessLookupError):
            vanished.add(int(pid.name))
    return {"checked_at": now(), "uid": os.getuid(), "same_uid_processes": examined,
            "matches": matches, "permission_denied_pids": sorted(denied),
            "vanished_pids": sorted(vanished),
            "scope": "Point-in-time visible same-UID cwd/fd/maps; not a lock or all-user proof"}


def main():
    started = now()
    assert not (BASE / "probe.json").exists()
    cases = json.loads((BASE / "cases.json").read_bytes())
    paths = [case[role] for case in cases for role in ("extracted", "store", "new-backup")]
    commands, repositories, snapshots, manifests = [], {}, {}, []
    mount_raw, evidence = command(["findmnt", "-J", "-T", "/mnt/backup", "-o",
                                   "TARGET,SOURCE,FSTYPE,UUID,OPTIONS"])
    commands.append(evidence)
    mount = json.loads(mount_raw)
    assert mount["filesystems"][0]["uuid"] == "d90aaf81-2358-411f-8022-4e1a3f7b0973"
    before = usage(paths)
    for case in cases:
        repo = case["repository"]
        password = KIT_PASSWORD if case["case"].startswith("independent") else PASSWORD
        base = [str(BINARY), "-r", repo, "--password-file", str(password), "--no-cache", "--json"]
        if repo not in repositories:
            raw, evidence = command(base + ["cat", "config"])
            commands.append(evidence)
            config = json.loads(raw)
            expected = PILOT_ID if repo.endswith("-pilot") else PRODUCTION_ID
            assert config["id"] == expected
            raw, evidence = command(base + ["snapshots"])
            commands.append(evidence)
            rows = json.loads(raw)
            snapshots[repo] = {row["id"]: row for row in rows}
            repositories[repo] = {"config": config, "snapshots": rows,
                                  "password_path": str(password),
                                  "password_mode": oct(password.stat().st_mode & 0o777)}
        snapshot = snapshots[repo][case["snapshot_id"]]
        raw, evidence = command(base + ["dump", case["snapshot_id"], "/backup.json"])
        commands.append(evidence)
        local = (Path(case["extracted"]) / "backup.json").read_bytes()
        assert raw == local, "Restic manifest differs from extracted manifest"
        value = json.loads(raw)
        assert value["backup_id"] == case["backup_id"]
        assert value["source_root"] == str(ROOT)
        manifests.append({"case": case["case"], "repository": repo,
                          "snapshot_id": snapshot["id"], "snapshot_time": snapshot["time"],
                          "snapshot_paths": snapshot["paths"], "snapshot_tags": snapshot.get("tags", []),
                          "manifest_sha256": digest(raw), "manifest_bytes": len(raw),
                          "backup_id": value["backup_id"], "matches_extracted_byte_for_byte": True})
    # The independent recovery receipt also depends on this snapshot in production.
    assert cases[-1]["snapshot_id"] in snapshots["/mnt/backup/marketdata-restic"]
    after = usage(paths)
    config_refs, config_hashes = [], {}
    files = list((Path.home() / ".config/marketdata").glob("*.toml"))
    files += list((Path.home() / ".config/marketdata").glob("*.env"))
    files += list((Path.home() / ".config/systemd/user").glob("marketdata*.*"))
    files += [Path.home() / ".config/marketdata/checkpoint-retention-authorization.json"]
    for file in sorted(set(files)):
        if not file.is_file():
            continue
        raw = file.read_bytes()
        config_hashes[str(file)] = digest(raw)
        for index, line in enumerate(raw.decode().splitlines(), 1):
            if "marketdata-restic-pilot" in line or "marketdata-restic-restore" in line or "marketdata-independent-recovery" in line:
                config_refs.append({"file": str(file), "line": index, "text": line})
    outputs = {}
    for label, args in {
        "units": ["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend", "marketdata*"],
        "timer": ["systemctl", "--user", "show", "marketdata-checkpoint-retirement.timer",
                  "-p", "ActiveState", "-p", "LastTriggerUSec", "-p", "NextElapseUSecRealtime"],
        "retirement_service": ["systemctl", "--user", "show", "marketdata-checkpoint-retirement.service",
                  "-p", "ActiveState", "-p", "Result", "-p", "ExecMainStartTimestamp", "-p", "ExecMainStatus"],
    }.items():
        raw, evidence = command(args)
        commands.append(evidence)
        outputs[label] = raw.decode()
    vfs = os.statvfs("/mnt/backup")
    result = {"started_at": started, "finished_at": now(), "mount": mount,
              "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(),
              "repositories": repositories, "manifest_comparisons": manifests,
              "process_usage_before": before, "process_usage_after": after,
              "configuration_references": config_refs, "configuration_sha256": config_hashes,
              "systemd": outputs, "commands": commands,
              "available_bytes": vfs.f_bavail * vfs.f_frsize,
              "free_bytes": vfs.f_bfree * vfs.f_frsize,
              "full_data_check_performed": False, "restore_performed": False,
              "deleted_files": 0, "credential_contents_or_hashes_recorded": False}
    (BASE / "probe.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"repositories": len(repositories), "manifest_matches": len(manifests),
                      "process_matches_before": len(before["matches"]),
                      "process_matches_after": len(after["matches"]), "deleted_files": 0}))


if __name__ == "__main__":
    main()
