"""A separate, profile-driven restic trial over immutable marketdata checkpoints.

Never initializes Store on a backup or on the production root. No production
manifest, source-root check, service or retention policy is changed here.
"""

import argparse
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import tomllib
from contextlib import closing
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from marketdata import aggtrades, backup, depth
from marketdata.domain import Key, json_bytes, now, sha256, utc
from marketdata.fileio import packed_record, read_packed, safe_file
from marketdata.store import Store


def require(condition, message):
    if not condition:
        raise ValueError(message)


def overlap(a, b):
    return a.is_relative_to(b) or b.is_relative_to(a)


@dataclass(frozen=True)
class Profile:
    store_root: Path
    backup_dir: Path
    backup_mount: Path
    restic_binary: Path
    restic_repository: Path
    restic_password_file: Path
    work_root: Path
    reports_root: Path
    min_free_gib: float
    restic_threads: int

    @classmethod
    def load(cls, path):
        values = tomllib.loads(Path(path).read_text())
        require(set(values) == set(cls.__annotations__), "Unexpected/missing profile fields")
        for key in set(values) - {"min_free_gib", "restic_threads"}:
            value = values[key]
            require(
                isinstance(value, str) and Path(value).is_absolute(), f"Absolute {key} required"
            )
            candidate = Path(value)
            require(
                not any(p.is_symlink() for p in (candidate, *candidate.parents)), "Symlink path"
            )
            values[key] = candidate.resolve()
        result = cls(**values)
        require(
            isinstance(result.min_free_gib, (int, float))
            and math.isfinite(result.min_free_gib)
            and result.min_free_gib >= 1,
            "At least 1 GiB must remain free",
        )
        require(
            type(result.restic_threads) is int and 1 <= result.restic_threads <= 4, "Thread limit"
        )
        separated = [
            result.store_root,
            result.backup_dir,
            result.restic_repository,
            result.work_root,
        ]
        for i, a in enumerate(separated):
            for b in separated[i + 1 :]:
                require(not overlap(a, b), "Store, archives, repository and work must be separate")
        for root in separated[1:]:
            require(
                root.is_relative_to(result.backup_mount), "Trial storage must be on backup mount"
            )
        require(result.backup_mount != Path("/"), "A dedicated backup mount is required")
        for root in separated[1:] + [result.store_root / "data", result.store_root / "state"]:
            require(not overlap(result.reports_root, root), "Reports overlap protected storage")
        for root in separated + [result.reports_root]:
            require(
                not result.restic_password_file.is_relative_to(root),
                "Keep password outside trial inputs, outputs and reports",
            )
        return result

    def check(self):
        require(self.backup_mount.is_mount(), "Required backup mount is missing")
        require(self.backup_dir.is_dir(), "Existing dedicated backup directory is missing")
        require((self.store_root / backup.CATALOG).is_file(), "Production catalog is missing")
        require(self.restic_binary.is_file(), "Pinned restic executable is missing")
        version = subprocess.check_output([str(self.restic_binary), "version"], text=True).strip()
        require(version.startswith("restic 0.19.1 "), "Trial requires restic 0.19.1")
        password = self.restic_password_file.stat()
        require(
            password.st_uid == os.getuid() and password.st_mode & 0o077 == 0, "Password permissions"
        )
        require(password.st_size > 0, "Password file is empty")
        require(
            shutil.disk_usage(self.backup_mount).free > self.min_free_gib * 1024**3,
            "Backup mount has insufficient free space",
        )


def checkpoints(profile, path):
    specification = tomllib.loads(Path(path).read_text())
    require(set(specification) == {"checkpoints"}, "Unexpected trial fields")
    rows = []
    for item in specification["checkpoints"]:
        require(
            set(item) == {"label", "relative_path", "backup_id"}, "Unexpected checkpoint fields"
        )
        require(re.fullmatch(r"[a-zA-Z0-9_-]+", item["label"]), "Unsafe checkpoint label")
        root = safe_file(profile.backup_dir, item["relative_path"])
        _, manifest = backup.load_backup(root)
        require(manifest["backup_id"] == item["backup_id"], "Checkpoint identity differs")
        require(
            manifest["source_root"] == str(profile.store_root), "Checkpoint source_root differs"
        )
        rows.append(
            {
                **item,
                "path": str(root),
                "manifest_sha256": sha256((root / "backup.json").read_bytes()),
                "manifest": manifest,
            }
        )
    require(
        len(rows) == 2 and rows[0]["label"] != rows[1]["label"], "Two distinct checkpoints required"
    )
    return rows


def disk_usage(roots):
    """Count allocated bytes once per inode, including files and directory blocks."""
    seen, allocated, logical, files, directories = set(), 0, 0, 0, 0
    for root in roots:
        if not root.exists():
            continue
        for path in (root, *root.rglob("*")):
            require(not path.is_symlink(), "Unexpected symlink in measurement")
            item = path.stat()
            if path.is_file():
                logical += item.st_size
                files += 1
            elif path.is_dir():
                directories += 1
            identity = (item.st_dev, item.st_ino)
            if identity not in seen:
                allocated += item.st_blocks * 512
                seen.add(identity)
    return {
        "allocated_bytes": allocated,
        "apparent_file_bytes": logical,
        "unique_inodes": len(seen),
        "file_paths": files,
        "directory_paths": directories,
        "semantics": "st_blocks*512; inode deduplication; includes directory and manifest blocks",
    }


class ArchiveReader(Store):
    """Immutable archive reader: no schema initialization or layout lock file creation."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.db = sqlite3.connect(
            (self.root / backup.CATALOG).as_uri() + "?mode=ro&immutable=1",
            uri=True,
            isolation_level=None,
        )
        self.db.row_factory = sqlite3.Row
        self._files_db = sqlite3.connect(
            (self.root / backup.CATALOG).as_uri() + "?mode=ro&immutable=1",
            uri=True,
            isolation_level=None,
        )
        self._files_db.row_factory = sqlite3.Row

    def file_bytes(self, relative):
        safe_file(self.root, relative)
        record = packed_record(self._files_db, relative)
        return (
            read_packed(self.root, record)
            if record
            else safe_file(self.root, relative).read_bytes()
        )


def compare_readers(archive, restored):
    expected, actual = ArchiveReader(archive), ArchiveReader(restored)
    reads, segments, raw = [], [], []
    try:
        snapshots = list(expected.db.execute("SELECT id FROM snapshots ORDER BY id"))
        for row in snapshots:
            identity = row["id"]
            spec = expected.snapshot_manifest(identity)
            require(spec == actual.snapshot_manifest(identity), "Snapshot manifest differs")
            start = utc(spec["start"])
            end = min(utc(spec["end"]), start + timedelta(days=1))
            for definition in spec["keys"].values():
                key = Key(**definition)
                a, b = (
                    expected.read(key, start, end, identity),
                    actual.read(key, start, end, identity),
                )
                require(a.equals(b), "Restored snapshot query differs")
                reads.append(
                    {"snapshot_id": identity, "key": key.id, "rows": len(a), "equal": True}
                )
        for dataset in ("agg_trades", "book_depth"):
            for order in ("ASC", "DESC"):
                record = expected.db.execute(
                    f"SELECT * FROM event_segments WHERE dataset=? ORDER BY first_time {order} LIMIT 1",
                    (dataset,),
                ).fetchone()
                if record is not None:
                    a, b = expected._revision_table(record), actual._revision_table(record)
                    require(a.equals(b), "Restored event segment differs")
                    segments.append({"dataset": dataset, "id": record["id"], "rows": len(a)})
        for order in ("ASC", "DESC"):
            for row in expected.db.execute(
                f"SELECT id FROM artifacts ORDER BY path {order} LIMIT 5"
            ):
                require(expected.raw(row[0]) == actual.raw(row[0]), "Restored raw record differs")
                raw.append(row[0])
        last = expected.db.execute(
            "SELECT MAX(last_time) FROM event_segments WHERE dataset='agg_trades'"
        ).fetchone()[0]
        trades = None
        if last:
            end = utc(last) + timedelta(microseconds=1)
            start = end - timedelta(minutes=10)
            a, b = (
                aggtrades.read_trades(expected, start, end),
                aggtrades.read_trades(actual, start, end),
            )
            require(json_bytes(a) == json_bytes(b), "Restored aggregate trades differ")
            trades = {"start": start, "end": end, "equal": True, "result": b[1]}
        closed = expected.db.execute(
            "SELECT id FROM event_collections WHERE dataset='book_depth' "
            "AND state='complete' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        replay = None
        if closed:
            a, b = depth.replay(expected, closed[0]), depth.replay(actual, closed[0])
            require(json_bytes(a) == json_bytes(b), "Restored depth replay differs")
            require(b["replay_matches_live"], "Depth replay does not match recorded collection")
            replay = {
                k: b[k]
                for k in ("collection_id", "rows", "valid", "continuous", "replay_matches_live")
            }
        return {
            "ok": True,
            "snapshots": len(snapshots),
            "snapshot_queries": reads,
            "event_segments": segments,
            "raw_ids": raw,
            "aggtrade": trades,
            "depth_replay": replay,
            "scope": "all snapshot manifests; first <=24h per key; selected raw/events; 10min trades; one closed depth session",
        }
    finally:
        expected.close()
        actual.close()


def sample_streams(root):
    sampled = {"at": now()}
    # A live catalog must see its WAL. immutable=1 is only valid for closed archives.
    with closing(sqlite3.connect((root / backup.CATALOG).as_uri() + "?mode=ro", uri=True)) as db:
        for name, dataset in (("aggtrade", "agg_trades"), ("depth", "book_depth")):
            row = db.execute(
                "SELECT report FROM event_collections WHERE dataset=? AND kind='websocket' "
                "ORDER BY started_at DESC LIMIT 1",
                (dataset,),
            ).fetchone()
            if row is None:
                sampled[name] = {"state": "missing"}
                continue
            report = json.loads(row[0])
            sampled[name] = {
                k: report.get(k)
                for k in (
                    "id",
                    "state",
                    "updated_at",
                    "last_received_at",
                    "sync_losses",
                    "synchronized",
                    "unreconstructed_messages",
                    "lagged_snapshots",
                    "unrepaired_gaps",
                    "out_of_order_messages",
                    "connection_errors",
                    "error",
                    "flush_error",
                )
            }
            received = report.get("last_received_at")
            sampled[name]["receive_age_seconds"] = (
                (now() - utc(received)).total_seconds() if received else None
            )
    return sampled


class Trial:
    def __init__(self, profile, inputs):
        self.profile, self.inputs = profile, inputs

    def save(self, name, value):
        path = self.profile.reports_root / (name + ".json")
        require(not path.exists(), f"Report exists; inspect previous attempt: {path}")
        path.write_bytes(json_bytes(value))
        return value

    def read(self, name):
        return json.loads((self.profile.reports_root / (name + ".json")).read_bytes())

    def unchanged(self):
        for entry in self.inputs:
            marker = Path(entry["path"]) / "backup.json"
            require(
                sha256(marker.read_bytes()) == entry["manifest_sha256"], "Original manifest changed"
            )
            require(
                json.loads(marker.read_bytes()) == entry["manifest"], "Original manifest differs"
            )

    def restic(self, name, arguments, cwd=None):
        profile = self.profile
        profile.check()
        command = [
            str(profile.restic_binary),
            "--repo",
            str(profile.restic_repository),
            "--password-file",
            str(profile.restic_password_file),
            "--cache-dir",
            str(profile.work_root / "cache"),
            "--compression",
            "auto",
            "--json",
            *arguments,
        ]
        env = {k: v for k, v in os.environ.items() if not k.startswith("RESTIC_")}
        env["GOMAXPROCS"] = str(profile.restic_threads)
        env["RESTIC_READ_CONCURRENCY"] = str(profile.restic_threads)
        timing = profile.reports_root / (name + ".time.json")
        stdout, stderr = (
            profile.reports_root / (name + ".jsonl"),
            profile.reports_root / (name + ".stderr"),
        )
        require(not stdout.exists() and not timing.exists(), "Command already attempted")
        started = now()
        print(json_bytes({"phase": name, "started_at": started}).decode(), flush=True)
        with stdout.open("xb") as out, stderr.open("xb") as err:
            result = subprocess.run(
                [
                    "/usr/bin/time",
                    "-f",
                    '{"wall_seconds":%e,"user_seconds":%U,"system_seconds":%S,"max_rss_kib":%M,"input_blocks":%I,"output_blocks":%O}',
                    "-o",
                    str(timing),
                    *command,
                ],
                cwd=cwd,
                env=env,
                stdout=out,
                stderr=err,
            )
        info = {"started_at": started, "finished_at": now(), "returncode": result.returncode}
        if result.returncode == 0:
            info["resources"] = json.loads(timing.read_bytes())
        self.save(name + "-command", info)
        require(result.returncode == 0, f"{name} failed ({result.returncode}); inspect {stderr}")
        return [json.loads(line) for line in stdout.read_text().splitlines() if line.strip()]

    def prepare(self):
        p = self.profile
        for path in (p.restic_repository, p.work_root, p.reports_root):
            require(not path.exists(), f"Fresh trial path required: {path}")
        required = sum(sum(f["bytes"] for f in x["manifest"]["files"]) for x in self.inputs)
        require(
            shutil.disk_usage(p.backup_mount).free > required * 4 + p.min_free_gib * 1024**3,
            "Reserve space for repository, extraction, restored stores and new independent backups",
        )
        p.work_root.mkdir(parents=True)
        p.reports_root.mkdir(parents=True)
        first = disk_usage([Path(self.inputs[0]["path"])])
        both = disk_usage([Path(x["path"]) for x in self.inputs])
        self.save(
            "baseline",
            {
                "at": now(),
                "inputs": self.inputs,
                "profile": {k: str(v) if isinstance(v, Path) else v for k, v in vars(p).items()},
                "hardlink_first": first,
                "hardlink_both": both,
                "hardlink_increment_allocated_bytes": both["allocated_bytes"]
                - first["allocated_bytes"],
                "binary_sha256": sha256(p.restic_binary.read_bytes()),
            },
        )
        self.restic("init", ["init", "--repository-version", "2"])
        self.save("repository-initial", disk_usage([p.restic_repository]))

    def back_up(self, index):
        entry = self.inputs[index]
        self.unchanged()
        previous = self.read("backup-" + self.inputs[0]["label"])["snapshot_id"] if index else None
        arguments = ["backup", ".", "--tag", "marketdata-restic-pilot", "--tag", entry["label"]]
        if previous:
            arguments.extend(["--parent", previous])
        lines = self.restic("backup-" + entry["label"], arguments, cwd=entry["path"])
        summaries = [r for r in lines if r.get("message_type") == "summary"]
        require(
            len(summaries) == 1 and re.fullmatch(r"[0-9a-f]{64}", summaries[0]["snapshot_id"]),
            "Missing snapshot",
        )
        self.unchanged()
        self.save(
            "backup-" + entry["label"],
            {
                **summaries[0],
                "parent_requested": previous,
                "repository_usage": disk_usage([self.profile.restic_repository]),
                "cache_usage": disk_usage([self.profile.work_root / "cache"]),
            },
        )

    def check(self):
        self.restic("check-read-data", ["check", "--read-data"])
        self.unchanged()
        self.save("check", {"ok": True, "at": now(), "read_data": True})

    def recover(self, index):
        entry, p = self.inputs[index], self.profile
        label, original = entry["label"], Path(entry["path"])
        snapshot = self.read("backup-" + label)["snapshot_id"]
        extracted, restored = p.work_root / (label + "-extracted"), p.work_root / (label + "-store")
        new_backup = p.work_root / (label + "-new-backup")
        for path in (extracted, restored, new_backup):
            require(not path.exists(), "Recovery output already exists; inspect previous attempt")
        self.restic(
            "extract-" + label, ["restore", snapshot, "--target", str(extracted), "--verify"]
        )
        require(
            (extracted / "backup.json").read_bytes() == (original / "backup.json").read_bytes(),
            "Extracted manifest differs",
        )
        self.save(
            "verify-extracted-" + label,
            self.phase("verify-extracted-" + label, lambda: backup.verify_backup(extracted)),
        )
        self.save(
            "restore-" + label,
            self.phase(
                "restore-" + label,
                lambda: backup.restore_backup(extracted, restored, min_free_gib=p.min_free_gib),
            ),
        )
        self.save(
            "readback-" + label,
            self.phase("readback-" + label, lambda: compare_readers(extracted, restored)),
        )
        with_store = Store(restored)
        try:
            validation = self.phase("validate-" + label, with_store.validate)
            self.save("validate-" + label, validation)
            require(validation["ok"], "Restored store validation failed")
            created = self.phase(
                "new-backup-" + label,
                lambda: backup.create_backup(with_store, new_backup, min_free_gib=p.min_free_gib),
            )
            self.save("new-backup-" + label, created)
        finally:
            with_store.close()
        verified = self.phase(
            "verify-new-backup-" + label, lambda: backup.verify_backup(new_backup)
        )
        self.save("verify-new-backup-" + label, verified)
        new_manifest = json.loads((new_backup / "backup.json").read_bytes())
        require(new_manifest["source_root"] == str(restored), "New backup has wrong source_root")
        require(
            new_manifest["backup_id"] != entry["backup_id"], "New backup must have a new identity"
        )
        require(
            new_manifest["storage"]["mode"] == "independent_copy",
            "First new backup must be independent",
        )
        inode_checks = 0
        for record in entry["manifest"]["files"]:
            paths = [
                safe_file(root, record["path"])
                for root in (original, extracted, restored, new_backup)
            ]
            require(
                len({(f.stat().st_dev, f.stat().st_ino) for f in paths}) == 4,
                "Restore shares an input inode",
            )
            inode_checks += 1
        require(
            (extracted / "backup.json").read_bytes() == (original / "backup.json").read_bytes(),
            "Extracted manifest changed",
        )
        self.unchanged()
        self.save(
            "recovery-" + label,
            {
                "ok": True,
                "snapshot_id": snapshot,
                "original_backup_id": entry["backup_id"],
                "original_source_root": entry["manifest"]["source_root"],
                "restored_root": str(restored),
                "new_backup_id": new_manifest["backup_id"],
                "new_source_root": new_manifest["source_root"],
                "independent_file_checks": inode_checks,
                "original_manifest_unchanged": True,
                "extracted_manifest_unchanged": True,
                "services_started": False,
            },
        )

    def phase(self, name, action):
        print(json_bytes({"phase": name, "started_at": now()}).decode(), flush=True)
        start = time.monotonic()
        result = action()
        self.save(
            name + "-timing", {"wall_seconds": time.monotonic() - start, "finished_at": now()}
        )
        print(json_bytes({"phase": name, "finished_at": now()}).decode(), flush=True)
        return result


def monitor(profile, step, stop):
    while not stop.is_set():
        try:
            sample = {"step": step, **sample_streams(profile.store_root)}
        except Exception as exc:
            sample = {"step": step, "at": now(), "sampling_error": str(exc)}
        with (profile.reports_root / "monitor.jsonl").open("ab") as handle:
            handle.write(json_bytes(sample) + b"\n")
        stop.wait(10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument(
        "step",
        choices=(
            "prepare",
            "backup-first",
            "backup-second",
            "check",
            "recover-first",
            "recover-second",
        ),
    )
    args = parser.parse_args()
    profile = Profile.load(args.profile)
    profile.check()
    trial = Trial(profile, checkpoints(profile, args.inputs))
    if args.step != "prepare":
        baseline = trial.read("baseline")
        require(json_bytes(baseline["inputs"]) == json_bytes(trial.inputs), "Trial inputs changed")
        require(
            sha256(profile.restic_binary.read_bytes()) == baseline["binary_sha256"],
            "Executable changed",
        )
        require(
            baseline["profile"]
            == {k: str(v) if isinstance(v, Path) else v for k, v in vars(profile).items()},
            "Profile changed",
        )
    os.nice(10)
    subprocess.run(["ionice", "-c", "2", "-n", "7", "-p", str(os.getpid())], check=True)
    stop, watcher = threading.Event(), None
    if args.step != "prepare":
        watcher = threading.Thread(target=monitor, args=(profile, args.step, stop), daemon=True)
        watcher.start()
    try:
        if args.step == "prepare":
            trial.prepare()
        elif args.step.startswith("backup-"):
            trial.back_up(0 if args.step.endswith("first") else 1)
        elif args.step == "check":
            trial.check()
        else:
            trial.recover(0 if args.step.endswith("first") else 1)
    finally:
        stop.set()
        if watcher:
            watcher.join()
    print(json_bytes({"step": args.step, "ok": True, "finished_at": now()}).decode(), flush=True)


if __name__ == "__main__":
    main()
