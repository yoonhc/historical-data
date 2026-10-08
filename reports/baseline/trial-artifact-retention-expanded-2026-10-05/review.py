"""Read-only expanded trial-retirement review. No apply/delete/service mutation.

Copies only non-secret evidence into reports; restic uses its ordinary locks.
The original 12-directory plan and its recorded evidence remain unchanged.
"""

import gzip
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
OLD = BASE.parent / "trial-artifact-retention-plan-2026-10-05"
ARTIFACTS = ROOT / "reports/trial-artifact-retention-expanded-2026-10-05"
TARGETS = {
    "pilot-repository": Path("/mnt/backup/marketdata-restic-pilot"),
    "pilot-work": Path("/mnt/backup/marketdata-restic-pilot-work"),
    "independent-trial": Path("/mnt/backup/marketdata-independent-recovery-20261003"),
    "production-restore-run": Path("/mnt/backup/marketdata-restic-restore/0cc942c867b34ba1b561fc4f7e6d1dca"),
}
TOP = {
    "pilot-repository": {"config", "data", "index", "keys", "locks", "snapshots"},
    "pilot-work": {"2026-09-26-extracted", "2026-09-26-store", "2026-09-26-new-backup",
                   "2026-09-27-extracted", "2026-09-27-store", "2026-09-27-new-backup", "cache"},
    "independent-trial": {"repository", "restore-work"},
    "production-restore-run": {"extracted", "store", "new-backup"},
}
PRODUCTION = Path("/mnt/backup/marketdata-restic")
CLONE = TARGETS["independent-trial"] / "repository"
PILOT = TARGETS["pilot-repository"]
KIT = Path("/mnt/backup/marketdata-recovery-kit-20261003")
PROFILE = Path.home() / ".config/marketdata/backup-host.toml"
EXPECTED_REPOSITORY = "b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558"
EXPECTED_PILOT = "3cb78902823a4335c5a9fc3ab5635f583c4b4080a8273422a0defdbe3f11effc"
UUID = "d90aaf81-2358-411f-8022-4e1a3f7b0973"


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def write(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def old_helpers():
    # Reuse only the already reviewed, read-only command and process-query helpers.
    import sys

    sys.path.insert(0, str(OLD))
    spec = importlib.util.spec_from_file_location("previous_probe", OLD / "probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe():
    helper = old_helpers()
    commands = []

    def command(args):
        raw, record = helper.command(args)
        commands.append(record)
        return raw

    def restic(repo, *args):
        password = KIT / "secrets/restic-password" if repo == CLONE else helper.PASSWORD
        return command([str(helper.BINARY), "-r", str(repo), "--password-file", str(password),
                        "--no-cache", "--json", *args])

    started = now()
    ARTIFACTS.mkdir(parents=True, mode=0o700, exist_ok=False)
    paths = [str(p) for p in TARGETS.values()]
    use_before = helper.usage(paths)
    mount = json.loads(command(["findmnt", "-J", "-R", "/mnt/backup", "-o", "TARGET,SOURCE,FSTYPE,UUID,OPTIONS"]))
    assert mount["filesystems"][0]["uuid"] == UUID
    assert not mount["filesystems"][0].get("children"), "Review nested mounts first"
    repos = {}
    for repo in (PRODUCTION, PILOT, CLONE):
        config = json.loads(restic(repo, "cat", "config"))
        assert config["id"] == (EXPECTED_PILOT if repo == PILOT else EXPECTED_REPOSITORY)
        snapshots = json.loads(restic(repo, "snapshots"))
        repos[str(repo)] = {"config": config, "snapshots": snapshots}
    prod = {row["id"]: row for row in repos[str(PRODUCTION)]["snapshots"]}
    clone = repos[str(CLONE)]["snapshots"]
    duplicate_snapshots = []
    for row in clone:
        assert row == prod[row["id"]], "Clone has distinct snapshot metadata"
        duplicate_snapshots.append({"snapshot_id": row["id"], "tree": row["tree"],
                                    "all_snapshot_metadata_equal": True})
    old_cases = load(OLD / "cases.json")
    local = Path("/mnt/backup/marketdata-historical/automatic/2026-09-26/d4e81e6b027349cdb2e372da4f47288d")
    sources = []
    for case in old_cases[:2]:
        pilot_raw = restic(PILOT, "dump", case["snapshot_id"], "/backup.json")
        assert pilot_raw == (Path(case["extracted"]) / "backup.json").read_bytes()
        if case["case"].endswith("09-26"):
            preserved_raw = (local / "backup.json").read_bytes()
            retained = {"kind": "pre_enrollment_checkpoint", "path": str(local)}
        else:
            matched = [s for s in prod.values() if "backup_id=" + case["backup_id"] in s.get("tags", [])]
            assert len(matched) == 1
            sid = matched[0]["id"]
            preserved_raw = restic(PRODUCTION, "dump", sid, "/backup.json")
            retained = {"kind": "production_snapshot", "repository": str(PRODUCTION), "snapshot_id": sid}
        assert pilot_raw == preserved_raw
        manifest = json.loads(pilot_raw)
        assert manifest["backup_id"] == case["backup_id"]
        assert manifest["source_root"] == str(ROOT)
        sources.append({"case": case["case"], "backup_id": case["backup_id"],
                        "pilot_snapshot_id": case["snapshot_id"], "retained_source": retained,
                        "manifest_sha256": sha(preserved_raw), "byte_identical_manifest": True,
                        "files": len(manifest["files"])})
    state_path = ROOT / "archive-state" / EXPECTED_REPOSITORY / "historical-data-original-v1/state.json"
    state = load(state_path)
    check = state["repository_check"]
    assert check["ok"] and check["read_data"] and check["status"] == "complete"
    assert not state.get("active_operation")
    assert (state.get("last_operation") or {"ok": True})["ok"]
    assert {s["snapshot_id"] for s in duplicate_snapshots} <= set(check["snapshot_ids"])
    assert sources[1]["retained_source"]["snapshot_id"] in check["snapshot_ids"]
    assert all(not list((repo / "locks").iterdir()) for repo in (PILOT, CLONE)), "Active restic trial locks"
    use_after = helper.usage(paths)
    assert not use_before["matches"] and not use_after["matches"], "Trial path currently in use"
    files = list((Path.home() / ".config/marketdata").glob("*.toml"))
    files += list((Path.home() / ".config/marketdata").glob("*.env"))
    files += list((Path.home() / ".config/systemd/user").glob("marketdata*.*"))
    files += [Path.home() / ".config/marketdata/checkpoint-retention-authorization.json"]
    configs, refs = {}, []
    for path in sorted(set(files)):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        configs[str(path)] = sha(raw)
        for i, line in enumerate(raw.decode().splitlines(), 1):
            if any(value in line for value in paths) or str(PROFILE) in line or "marketdata-restic-restore" in line:
                refs.append({"file": str(path), "line": i, "text": line})
    assert all(r["file"] == str(PROFILE) for r in refs if any(p in r["text"] for p in paths))
    profile_raw = PROFILE.read_bytes()
    # Exact trial profile fields ensure this preservation cannot accidentally include a password value.
    profile = tomllib.loads(profile_raw.decode())
    assert set(profile) == {"store_root", "backup_dir", "backup_mount", "restic_binary", "restic_repository",
                            "restic_password_file", "work_root", "reports_root", "min_free_gib", "restic_threads"}
    assert profile["restic_password_file"] == str(helper.PASSWORD)
    assert profile["restic_repository"] == str(PILOT)
    profile_copy = ARTIFACTS / "preserved-backup-host.toml"
    with profile_copy.open("xb") as stream:
        stream.write(profile_raw)
    profile_copy.chmod(0o600)
    assert profile_copy.read_bytes() == profile_raw
    protected_receipt = ROOT / old_cases[-1]["evidence"]
    policy = tomllib.loads((Path.home() / ".config/marketdata/checkpoint-retention-automatic.toml").read_text())
    assert policy["recovery_receipt_sha256"] == sha(protected_receipt.read_bytes())
    assert load(protected_receipt)["result"]["snapshot_id"] in prod
    systemd = {}
    for label, args in {
        "units": ["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend", "marketdata*"],
        "timer": ["systemctl", "--user", "show", "marketdata-checkpoint-retirement.timer", "-p", "ActiveState", "-p", "LastTriggerUSec", "-p", "NextElapseUSecRealtime"],
        "service_references": ["systemctl", "--user", "show", "marketdata-backup.service", "marketdata-restic-archive.service", "marketdata-checkpoint-retirement.service", "-p", "Id", "-p", "ExecStart", "-p", "EnvironmentFiles", "-p", "FragmentPath", "-p", "DropInPaths"],
    }.items():
        systemd[label] = command(args).decode()
    assert str(PROFILE) not in systemd["service_references"]
    assert not any(p in systemd["service_references"] for p in paths)
    vfs = os.statvfs("/mnt/backup")
    result = {"started_at": started, "finished_at": now(), "targets": {k: str(v) for k, v in TARGETS.items()},
              "mounts": mount, "repositories": repos, "clone_snapshot_equivalence": duplicate_snapshots,
              "pilot_retained_sources": sources, "existing_production_full_check": check,
              "state_path": str(state_path), "current_unresolved_archive_error": False,
              "trial_lock_files_after_probe": 0, "process_usage_before": use_before, "process_usage_after": use_after,
              "configuration_sha256": configs, "configuration_references": refs, "systemd": systemd,
              "trial_profile": {"source": str(PROFILE), "source_sha256": sha(profile_raw),
                                "preserved_copy": str(profile_copy.relative_to(ROOT)),
                                "copy_mode": oct(profile_copy.stat().st_mode & 0o777),
                                "copied_exactly": True, "original_still_present": True,
                                "future_action": "retire this inactive profile only after separate cleanup approval"},
              "protected_recovery_receipt": {"path": str(protected_receipt), "sha256": sha(protected_receipt.read_bytes())},
              "kit_credential_mode": oct((KIT / "secrets/restic-password").stat().st_mode & 0o777),
              "available_bytes": vfs.f_bavail * vfs.f_frsize, "commands": commands,
              "fresh_full_repository_read": False, "fresh_restore_test": False, "deleted_files": 0,
              "source_sha256": sha(Path(__file__).read_bytes())}
    write(BASE / "probe.json", result)
    print(json.dumps({"pilot_sources_retained": len(sources), "clone_snapshots_identical": len(duplicate_snapshots),
                      "process_path_matches": 0, "deleted_files": 0}), flush=True)


def signature(st):
    return {k: getattr(st, "st_" + k) for k in (
        "dev", "ino", "mode", "uid", "gid", "size", "mtime_ns", "ctime_ns", "nlink", "blocks")}


def inventory():
    started = now()
    old = load(OLD / "inventory.json")
    old_rows = {Path(r["path"]): r for r in old["candidates"]}
    old_hashes = {root: hashlib.sha256() for root in old_rows}
    shared, summaries = {}, []
    dev = Path("/mnt/backup").stat().st_dev

    def onerror(error):
        raise error

    for label, root in TARGETS.items():
        assert root.resolve() == root
        assert all(not p.is_symlink() for p in [root, *root.parents])
        assert {p.name for p in root.iterdir()} == TOP[label], "Unreviewed top-level child"
        before = root.stat()
        assert before.st_dev == dev
        counters = {"files": 0, "directories": 0, "apparent_file_bytes": 0,
                    "single_link_file_blocks_bytes": 0, "directory_blocks_bytes": 0,
                    "regular_allocated_path_bytes": 0}
        hashed = hashlib.sha256()
        output = ARTIFACTS / (label + ".inventory.jsonl.gz")

        def record(path, st, stream):
            assert st.st_dev == dev
            assert stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode), "Unexpected file type"
            row = {"path": path.relative_to(root).as_posix(), **signature(st)}
            line = encoded(row) + b"\n"
            hashed.update(line)
            stream.write(line)
            for previous, previous_hash in old_hashes.items():
                if path == previous or path.is_relative_to(previous):
                    previous_hash.update(encoded({**row, "path": path.relative_to(previous).as_posix()}) + b"\n")
                    break
            if stat.S_ISDIR(st.st_mode):
                counters["directories"] += 1
                counters["directory_blocks_bytes"] += st.st_blocks * 512
            else:
                counters["files"] += 1
                counters["apparent_file_bytes"] += st.st_size
                counters["regular_allocated_path_bytes"] += st.st_blocks * 512
                if st.st_nlink == 1:
                    counters["single_link_file_blocks_bytes"] += st.st_blocks * 512
                else:
                    key = (st.st_dev, st.st_ino)
                    item = shared.setdefault(key, {"nlink": st.st_nlink, "blocks": st.st_blocks, "paths": []})
                    assert (item["nlink"], item["blocks"]) == (st.st_nlink, st.st_blocks)
                    item["paths"].append(str(path))

        with output.open("xb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
                record(root, before, stream)
                for current, dirs, names, fd in os.fwalk(root, follow_symlinks=False, onerror=onerror):
                    dirs.sort()
                    names.sort()
                    for name in dirs + names:
                        record(Path(current) / name, os.stat(name, dir_fd=fd, follow_symlinks=False), stream)
        assert signature(root.stat()) == signature(before), "Root changed while scanning"
        entry = {"label": label, "path": str(root), "root": signature(before), **counters,
                 "top_level_allowlist": sorted(TOP[label]), "inventory": str(output.relative_to(ROOT)),
                 "inventory_sha256": hashed.hexdigest(), "gzip_sha256": sha(output.read_bytes()),
                 "gzip_bytes": output.stat().st_size}
        summaries.append(entry)
        print(json.dumps({"label": label, "files": counters["files"], "directories": counters["directories"]}), flush=True)
    unchanged = []
    for root, old_row in old_rows.items():
        assert old_hashes[root].hexdigest() == old_row["inventory_sha256"], f"Original trial tree changed: {root}"
        unchanged.append({"path": str(root), "metadata_inventory_unchanged": True,
                          "inventory_sha256": old_row["inventory_sha256"]})
    groups = [{"dev": key[0], "ino": key[1], **item} for key, item in shared.items()]
    assert all(len(g["paths"]) <= g["nlink"] for g in groups)
    outside = [g for g in groups if len(g["paths"]) < g["nlink"]]
    reclaimed = sum(r["single_link_file_blocks_bytes"] + r["directory_blocks_bytes"] for r in summaries)
    reclaimed += sum(g["blocks"] * 512 for g in groups if len(g["paths"]) == g["nlink"])
    result = {"started_at": started, "finished_at": now(), "targets": summaries,
              "original_twelve_trees": unchanged, "shared_inodes": groups,
              "external_hardlinked_inodes": len(outside),
              "outside_reference_bytes_excluded": sum(g["blocks"] * 512 for g in outside),
              "estimated_reclaimable_allocated_bytes": reclaimed,
              "apparent_file_bytes": sum(r["apparent_file_bytes"] for r in summaries),
              "source_sha256": sha(Path(__file__).read_bytes()), "payload_hashes_read": False,
              "deleted_files": 0}
    write(BASE / "inventory.json", result)
    print(json.dumps({"estimated_reclaimable_allocated_bytes": reclaimed, "external_hardlinked_inodes": len(outside), "deleted_files": 0}))


if __name__ == "__main__":
    import sys

    assert len(sys.argv) == 2 and sys.argv[1] in {"probe", "inventory"}
    if sys.argv[1] == "probe":
        probe()
    else:
        inventory()
