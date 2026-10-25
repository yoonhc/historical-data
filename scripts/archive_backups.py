"""Archive completed marketdata checkpoints alongside the existing daily backups.

Run from the project root: python -m scripts.archive_backups --profile ... plan
No automatic init, retention, deletion, production Store, or maintenance locks.
"""

import argparse
import fcntl
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
import tomllib
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from marketdata import backup, checkpoint_evidence
from marketdata.checkpoint_locks import checkpoint_locks
from marketdata.domain import json_bytes, now, sha256
from marketdata.fileio import safe_file
from marketdata.store import Store, atomic_write

from scripts.archive_output import retain_verified_manifest
from scripts.restic_trial import compare_readers, overlap, require, sample_streams

VERSION = 1
HEX = r"[0-9a-f]{64}"
TAG = "marketdata-archive-v1"
PATH_FIELDS = {
    "store_root",
    "backup_dir",
    "backup_mount",
    "restic_repository",
    "restic_binary",
    "restic_password_file",
    "state_root",
    "reports_root",
    "cache_root",
    "restore_work_root",
}


def timestamp(value):
    """Unlike domain.utc, refuse ambiguous timezone-free checkpoint timestamps."""
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    require(isinstance(value, datetime) and value.tzinfo is not None, "Explicit timezone required")
    return value.astimezone(UTC)


def no_symlinks(path):
    require(not any(p.is_symlink() for p in (path, *path.parents)), f"Symlink path: {path}")


def mount_identity(path):
    require(path.is_mount(), "Required backup mount is missing")
    info = json.loads(
        subprocess.check_output(
            ["findmnt", "--json", "--mountpoint", str(path), "--output", "TARGET,UUID"], text=True
        )
    )["filesystems"]
    require(len(info) == 1 and info[0]["target"] == str(path), "Mount identity is ambiguous")
    return info[0]["uuid"]


@dataclass(frozen=True)
class Profile:
    store_root: Path
    backup_dir: Path
    backup_mount: Path
    backup_filesystem_uuid: str
    restic_repository: Path
    expected_repository_id: str
    restic_binary: Path
    restic_password_file: Path
    archive_scope: str
    enroll_from_utc_date: str
    state_root: Path
    reports_root: Path
    cache_root: Path
    restore_work_root: Path
    min_free_gib: float
    max_checkpoints_per_run: int
    restic_threads: int

    @classmethod
    def load(cls, path):
        values = tomllib.loads(Path(path).read_text())
        require(set(values) == set(cls.__annotations__), "Unexpected/missing profile fields")
        for key in PATH_FIELDS:
            require(
                isinstance(values[key], str) and Path(values[key]).is_absolute(),
                f"Absolute {key} required",
            )
            value = Path(values[key])
            no_symlinks(value)
            values[key] = value.resolve()
        result = cls(**values)
        result.validate()
        return result

    def validate(self):
        require(re.fullmatch(HEX, self.expected_repository_id), "Full repository ID required")
        require(re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", self.archive_scope), "Invalid archive_scope")
        require(bool(self.backup_filesystem_uuid), "Filesystem UUID required")
        require(
            date.fromisoformat(self.enroll_from_utc_date).isoformat() == self.enroll_from_utc_date,
            "Enrollment must be a UTC date",
        )
        require(
            type(self.min_free_gib) in (float, int)
            and math.isfinite(self.min_free_gib)
            and self.min_free_gib >= 1,
            "At least 1 GiB reserve required",
        )
        require(
            type(self.max_checkpoints_per_run) is int and 1 <= self.max_checkpoints_per_run <= 3,
            "Checkpoint batch limit must be 1..3",
        )
        require(type(self.restic_threads) is int and 1 <= self.restic_threads <= 4, "Thread limit")
        require(self.backup_mount != Path("/"), "Dedicated backup mount required")
        protected = [
            self.store_root / "data",
            self.store_root / "state",
            self.backup_dir,
            self.restic_repository,
            self.restore_work_root,
        ]
        outputs = [self.state_root, self.reports_root, self.cache_root]
        # This project's store root also contains code and reports; protect data/state within it.
        for i, a in enumerate(protected + outputs):
            for b in (protected + outputs)[i + 1 :]:
                require(not overlap(a, b), "Profile storage paths overlap")
        for path in [self.backup_dir, self.restic_repository, self.restore_work_root]:
            require(not overlap(path, self.store_root), "Archive storage overlaps source root")
            require(
                path != self.backup_mount and path.is_relative_to(self.backup_mount),
                "Archive storage must be below the backup mount",
            )
        require(
            not self.state_root.is_relative_to(self.backup_mount)
            and not self.reports_root.is_relative_to(self.backup_mount),
            "State and reports must survive a missing backup mount",
        )
        for root in protected + outputs + [self.store_root]:
            require(not overlap(self.restic_password_file, root), "Password overlaps stored files")
        for key in PATH_FIELDS:
            no_symlinks(getattr(self, key))

    def guard(self, *, reserve=0):
        self.validate()
        require(
            mount_identity(self.backup_mount) == self.backup_filesystem_uuid,
            "Backup filesystem UUID differs",
        )
        require(self.restic_repository.is_dir(), "Repository missing; automatic init is forbidden")
        require(
            self.restic_repository.stat().st_dev == self.backup_mount.stat().st_dev,
            "Repository is on a different filesystem",
        )
        password = self.restic_password_file.stat()
        require(
            stat.S_ISREG(password.st_mode)
            and password.st_uid == os.getuid()
            and stat.S_IMODE(password.st_mode) == 0o600
            and password.st_size > 0,
            "Password must be an owned, nonempty 0600 regular file",
        )
        require(
            self.restic_binary.is_file() and os.access(self.restic_binary, os.X_OK),
            "Pinned restic executable is missing",
        )
        require(
            shutil.disk_usage(self.backup_mount).free > reserve + self.min_free_gib * 1024**3,
            "Backup mount has insufficient free space",
        )

    @property
    def identity(self):
        return {
            "repository_id": self.expected_repository_id,
            "archive_scope": self.archive_scope,
            "source_root": str(self.store_root),
        }

    @property
    def state_file(self):
        return self.state_root / self.expected_repository_id / self.archive_scope / "state.json"

    @property
    def fingerprint(self):
        return sha256(
            json_bytes({k: str(v) if isinstance(v, Path) else v for k, v in vars(self).items()})
        )


def checkpoint(root, profile):
    with checkpoint_locks([root]):
        no_symlinks(root / "backup.json")
        raw = (root / "backup.json").read_bytes()
        return checkpoint_bytes(raw, root, profile)


def checkpoint_bytes(raw, root, profile):
    """Identify an unmodified manifest even when its original host is unavailable."""
    manifest = json.loads(raw)
    identity = manifest["backup_id"]
    require(re.fullmatch(HEX, identity), "Invalid backup ID")
    require(
        sha256(json_bytes({k: v for k, v in manifest.items() if k != "backup_id"})) == identity,
        "Backup manifest hash mismatch",
    )
    require(manifest["source_root"] == str(profile.store_root), "Checkpoint source_root differs")
    require(
        manifest["scope"] == backup.SCOPE and manifest["format_version"] in (1, 2),
        "Unsupported checkpoint",
    )
    times = [
        timestamp(manifest[k])
        for k in ("started_at", "checkpoint_started_at", "checkpoint_finished_at", "completed_at")
    ]
    require(
        times == sorted(times) and times[-1] <= now() + timedelta(minutes=5),
        "Checkpoint timestamps are out of order or in the future",
    )
    return {
        "backup_id": identity,
        "path": str(root),
        "source_root": manifest["source_root"],
        "manifest_sha256": sha256(raw),
        "checkpoint_at": times[2].isoformat(),
        "checkpoint_started_at": times[1].isoformat(),
        "completed_at": times[3].isoformat(),
        "snapshot_time_basis": "checkpoint_finished_at",
        "snapshot_timezone": "UTC",
        "bytes": sum(f["bytes"] for f in manifest["files"]),
    }


def discover(profile):
    require(profile.backup_dir.is_dir(), "Completed backup directory is missing")
    entries, incomplete, seen = [], [], set()
    for day in sorted(profile.backup_dir.iterdir()):
        require(not day.is_symlink(), "Symlink in backup directory")
        if not day.is_dir():
            continue
        require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name), "Unexpected backup date folder")
        date.fromisoformat(day.name)
        for root in sorted(day.iterdir()):
            require(not root.is_symlink(), "Symlink checkpoint folder")
            if not root.is_dir():
                continue
            if not (root / "backup.json").exists():
                incomplete.append(str(root))
                continue
            item = checkpoint(root, profile)
            # Enrollment is the checkpoint's UTC date, not its eventual upload date.
            if item["checkpoint_at"][:10] < profile.enroll_from_utc_date:
                continue
            require(item["backup_id"] not in seen, "Duplicate checkpoint identity in input folders")
            seen.add(item["backup_id"])
            entries.append(item)
    return sorted(entries, key=lambda e: (e["checkpoint_at"], e["backup_id"])), incomplete


def load_state(profile):
    if not profile.state_file.exists():
        return {
            "schema": VERSION,
            "identity": profile.identity,
            "checkpoints": {},
            "repository_check": None,
            "restore_checks": [],
        }
    no_symlinks(profile.state_file)
    result = json.loads(profile.state_file.read_bytes())
    require(
        result["schema"] == VERSION and result["identity"] == profile.identity,
        "State belongs to another source/repository/scope",
    )
    return result


class Busy(RuntimeError):
    pass


@contextmanager
def repository_lock(profile):
    path = profile.state_root / profile.expected_repository_id / "archive.lock"
    no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Busy(
                "Another archive/check/recovery holds the repository lock; retry later"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class CommandError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__(
            f"restic {receipt['operation']} failed ({receipt['returncode']}); "
            f"inspect {receipt['stderr']}"
        )


class Restic:
    def __init__(self, profile, reports):
        self.profile, self.reports = profile, reports

    def command(self, arguments, *, cwd=None):
        p = self.profile
        p.guard()
        self.reports.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        out, err = self.reports / f"{token}.stdout", self.reports / f"{token}.stderr"
        receipt = {
            "operation": arguments[0],
            "arguments": arguments,
            "cwd": str(cwd) if cwd else None,
            "started_at": now().isoformat(),
            "stdout": str(out),
            "stderr": str(err),
        }
        command = [
            str(p.restic_binary),
            "--repo",
            str(p.restic_repository),
            "--password-file",
            str(p.restic_password_file),
            "--cache-dir",
            str(p.cache_root),
            "--compression",
            "auto",
            "--json",
            *arguments,
        ]
        env = {k: v for k, v in os.environ.items() if not k.startswith("RESTIC_")}
        # restic 0.19.1 parses --time in time.Local. Make both parser and calendar explicit.
        env.update(
            TZ="UTC",
            GOMAXPROCS=str(p.restic_threads),
            RESTIC_READ_CONCURRENCY=str(p.restic_threads),
        )
        began = time.monotonic()
        with out.open("xb") as stdout, err.open("xb") as stderr:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
            try:
                code = process.wait()
            except BaseException:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                receipt.update(
                    returncode=process.returncode,
                    interrupted=True,
                    finished_at=now().isoformat(),
                    wall_seconds=time.monotonic() - began,
                )
                atomic_write(self.reports / f"{token}.json", json_bytes(receipt))
                raise
        receipt.update(
            returncode=code, finished_at=now().isoformat(), wall_seconds=time.monotonic() - began
        )
        atomic_write(self.reports / f"{token}.json", json_bytes(receipt))
        if code != 0:
            raise CommandError(receipt)
        p.guard()
        return out.read_bytes(), receipt

    def json(self, arguments, **kwargs):
        raw, receipt = self.command(arguments, **kwargs)
        return json.loads(raw), receipt

    def identify(self):
        p = self.profile
        version = subprocess.check_output([str(p.restic_binary), "version"], text=True).strip()
        require(version.startswith("restic 0.19.1 "), "Pinned restic 0.19.1 required")
        config, _ = self.json(["cat", "config"])
        require(config["id"] == p.expected_repository_id, "Restic repository ID differs")
        return version

    def snapshots(self):
        snapshots, _ = self.json(
            ["snapshots", "--tag", f"archive_scope={self.profile.archive_scope}"]
        )
        return snapshots or []


def expected_tags(profile, entry):
    return {TAG, f"archive_scope={profile.archive_scope}", f"backup_id={entry['backup_id']}"}


class Archive:
    def __init__(self, profile, reports, *, share_verified_outputs=True):
        self.p, self.reports = profile, reports
        self.share_verified_outputs = share_verified_outputs
        self.state = load_state(profile)
        self.restic = Restic(profile, reports)

    def save(self):
        self.state["updated_at"] = now().isoformat()
        atomic_write(self.p.state_file, json_bytes(self.state))

    def phase(self, name, action):
        start = time.monotonic()
        self.state["last_phase_started"] = {"name": name, "at": now().isoformat()}
        self.save()
        print(json_bytes({"phase": name, "started_at": now()}).decode(), flush=True)
        result = action()
        path = self.reports / f"{uuid.uuid4().hex}-{name}.json"
        atomic_write(
            path,
            json_bytes(
                {"finished_at": now(), "wall_seconds": time.monotonic() - start, "result": result}
            ),
        )
        return result

    def match(self, snapshot, entry, attempt_id=None):
        require(re.fullmatch(HEX, snapshot["id"]), "Full snapshot ID required")
        tags = set(snapshot.get("tags", []))
        require(expected_tags(self.p, entry) <= tags, "Snapshot scope/backup tags differ")
        for prefix in ("archive_scope=", "backup_id="):
            require(sum(t.startswith(prefix) for t in tags) == 1, "Ambiguous identity tags")
        attempts = [t.removeprefix("attempt_id=") for t in tags if t.startswith("attempt_id=")]
        require(
            len(attempts) == 1 and re.fullmatch(r"[0-9a-f]{32}", attempts[0]),
            "Snapshot attempt tag missing/ambiguous",
        )
        require(attempt_id is None or attempts[0] == attempt_id, "Snapshot attempt differs")
        require(snapshot["paths"] == [entry["path"]], "Snapshot absolute source path differs")
        require(
            timestamp(snapshot["time"]) == timestamp(entry["checkpoint_at"]),
            "Snapshot checkpoint time differs",
        )
        raw, receipt = self.restic.command(["dump", snapshot["id"], "/backup.json"])
        require(sha256(raw) == entry["manifest_sha256"], "Snapshot manifest differs")
        if self.share_verified_outputs:
            retain_verified_manifest(
                self.p.reports_root,
                self.p.expected_repository_id,
                receipt,
                raw,
                entry["manifest_sha256"],
            )
        return attempts[0]

    def clock_bundle(self, snapshot, entry):
        """Bind optional evidence to bytes actually stored, including lost-receipt recovery."""
        root = Path(entry["path"])
        expected = checkpoint_evidence.read_bundle(
            root, json.loads((root / "backup.json").read_bytes())
        )
        if expected is None:
            return None
        raw, _ = self.restic.command(["dump", snapshot, "/" + checkpoint_evidence.SIDECAR])
        require(sha256(raw) == expected["sha256"], "Stored completion evidence differs")
        return {k: expected[k] for k in ("sha256", "object_ids")}

    def extract_verify(self, snapshot, entry):
        self.p.guard(reserve=entry["bytes"])
        root = self.p.restore_work_root / uuid.uuid4().hex / "extracted"
        require(not root.exists(), "Recovery output must be fresh")
        self.restic.command(["restore", snapshot, "--target", str(root), "--verify"])
        require(
            sha256((root / "backup.json").read_bytes()) == entry["manifest_sha256"],
            "Extracted manifest differs",
        )
        self.phase("verify-extracted", lambda: backup.verify_backup(root))
        return root

    def entry_record(self, entry):
        record = self.state["checkpoints"].setdefault(
            entry["backup_id"],
            {"entry": entry, "attempts": [], "reconciliations": [], "stored": None},
        )
        require(record["entry"] == entry, "Previously discovered checkpoint changed")
        return record

    def reconcile(self, entry, record, snapshots):
        candidates = [
            s for s in snapshots if f"backup_id={entry['backup_id']}" in s.get("tags", [])
        ]
        known = {a["id"]: a for a in record["attempts"]}
        valid, unresolved = [], []
        for candidate in candidates:
            evidence = {"snapshot_id": candidate["id"], "started_at": now().isoformat()}
            try:
                failed = [
                    a
                    for aid, a in known.items()
                    if f"attempt_id={aid}" in candidate.get("tags", [])
                    and a.get("returncode") not in (None, 0)
                ]
                if failed:
                    # A known failure may omit backup.json too. Preserve it, retry the input.
                    evidence["known_failed_attempt"] = True
                    continue
                attempt_id = self.match(candidate, entry)
                attempt = known.get(attempt_id)
                if attempt and attempt.get("returncode") not in (None, 0):
                    evidence["known_failed_attempt"] = True
                    continue
                # A lost receipt can hide an incomplete snapshot, even with a valid manifest.
                extracted = self.extract_verify(candidate["id"], entry)
                evidence.update(ok=True, extracted=str(extracted))
                valid.append(candidate)
            except Exception as exc:
                evidence.update(ok=False, error=str(exc))
                unresolved.append(candidate["id"])
            finally:
                evidence["finished_at"] = now().isoformat()
                record["reconciliations"].append(evidence)
                self.save()
        require(
            not unresolved,
            "Unverified recovery candidates require inspection: " + ", ".join(unresolved),
        )
        if valid:
            # Each candidate is fully verified; deterministic selection, never delete duplicates.
            chosen = sorted(valid, key=lambda s: s["id"])[0]
            record["stored"] = {
                "snapshot_id": chosen["id"],
                "adopted_at": now().isoformat(),
                "archive_started_at": None,
                "archive_finished_at": None,
                "evidence": "extracted_and_fully_verified",
                "verified_duplicates": [s["id"] for s in valid],
                "checkpoint_evidence": self.clock_bundle(chosen["id"], entry),
            }
            record.pop("error", None)
            self.save()
            return True
        return False

    def verify(self):
        self.state["repository_check"] = {
            "ok": False,
            "status": "running",
            "started_at": now().isoformat(),
            "read_data": True,
        }
        self.save()
        try:
            _, receipt = self.restic.command(["check", "--read-data"])
            self.state["repository_check"].update(
                ok=True,
                status="complete",
                finished_at=receipt["finished_at"],
                receipt=receipt,
                snapshot_ids=sorted(
                    r["stored"]["snapshot_id"]
                    for r in self.state["checkpoints"].values()
                    if r["stored"]
                ),
            )
        except BaseException as exc:
            self.state["repository_check"].update(
                status="failed", error=str(exc), finished_at=now().isoformat()
            )
            raise
        finally:
            self.save()

    def run(self):
        self.restic.identify()
        prior = self.state["repository_check"]
        require(
            prior is None or prior["ok"],
            "Repository check incomplete/failed; run verify successfully before storing more",
        )
        entries, _ = discover(self.p)
        with checkpoint_locks([entry["path"] for entry in entries]):
            return self._run(entries)

    def _run(self, entries):
        snapshots = self.restic.snapshots()
        by_id = {s["id"]: s for s in snapshots}
        # Audit every known success, including input folders no longer discoverable.
        for record in self.state["checkpoints"].values():
            if record["stored"]:
                sid = record["stored"]["snapshot_id"]
                require(sid in by_id, f"Previously stored snapshot is missing: {sid}")
                self.match(by_id[sid], record["entry"])
        processed = 0
        for entry in entries:
            record = self.entry_record(entry)
            if record["stored"]:
                continue
            if processed >= self.p.max_checkpoints_per_run:
                break
            processed += 1
            self.save()
            try:
                self.p.guard(reserve=entry["bytes"])
                self.phase("verify-input", lambda: backup.verify_backup(Path(entry["path"])))
                require(checkpoint(Path(entry["path"]), self.p) == entry, "Input manifest changed")
                record["input_verified_at"] = now().isoformat()
                self.save()
                if self.reconcile(entry, record, snapshots):
                    continue
                parents = [
                    r
                    for r in self.state["checkpoints"].values()
                    if r["stored"] and r["entry"]["checkpoint_at"] <= entry["checkpoint_at"]
                ]
                parent = (
                    max(parents, key=lambda r: r["entry"]["checkpoint_at"]) if parents else None
                )
                attempt = {
                    "id": uuid.uuid4().hex,
                    "status": "running",
                    "started_at": now().isoformat(),
                    "profile_sha256": self.p.fingerprint,
                    "tool_version": VERSION,
                    "parent": parent["stored"]["snapshot_id"] if parent else None,
                }
                record["attempts"].append(attempt)
                self.save()
                arguments = [
                    "backup",
                    ".",
                    "--time",
                    timestamp(entry["checkpoint_at"]).strftime("%Y-%m-%d %H:%M:%S.%f"),
                ]
                for tag in sorted(expected_tags(self.p, entry) | {f"attempt_id={attempt['id']}"}):
                    arguments += ["--tag", tag]
                # --force means no parent for the first scope snapshot. Subsequent parents are
                # explicit IDs; neither retention grouping nor arbitrary 'latest' selects them.
                arguments += ["--parent", attempt["parent"]] if parent else ["--force"]
                try:
                    raw, receipt = self.restic.command(arguments, cwd=entry["path"])
                    attempt.update(returncode=0, command=receipt)
                    self.save()
                    summaries = [
                        row
                        for line in raw.splitlines()
                        if line.strip()
                        and (row := json.loads(line)).get("message_type") == "summary"
                    ]
                    require(
                        len(summaries) == 1
                        and re.fullmatch(HEX, summaries[0].get("snapshot_id", "")),
                        "Missing full snapshot ID in success summary",
                    )
                    sid = summaries[0]["snapshot_id"]
                    attempt["snapshot_id"] = sid
                    fresh = {s["id"]: s for s in self.restic.snapshots()}
                    require(sid in fresh, "New snapshot cannot be found")
                    self.match(fresh[sid], entry, attempt["id"])
                    require(
                        checkpoint(Path(entry["path"]), self.p) == entry, "Input manifest changed"
                    )
                    record["stored"] = {
                        "snapshot_id": sid,
                        "archive_started_at": receipt["started_at"],
                        "archive_finished_at": receipt["finished_at"],
                        "evidence": "exit_0_summary_metadata_manifest",
                        "checkpoint_evidence": self.clock_bundle(sid, entry),
                    }
                    attempt.update(status="stored", finished_at=now().isoformat())
                    record.pop("error", None)
                    self.save()
                except BaseException as exc:
                    attempt.update(status="failed", finished_at=now().isoformat(), error=str(exc))
                    if isinstance(exc, CommandError):
                        attempt.update(returncode=exc.receipt["returncode"], command=exc.receipt)
                    self.save()
                    raise
            except BaseException as exc:
                record["error"] = {"at": now().isoformat(), "message": str(exc)}
                self.save()
                raise
        stored = {
            r["stored"]["snapshot_id"] for r in self.state["checkpoints"].values() if r["stored"]
        }
        checked = set((self.state["repository_check"] or {}).get("snapshot_ids", []))
        if stored - checked:
            self.verify()
        return {"ok": True, "processed": processed, "stored_snapshots": sorted(stored)}

    def restore_check(self, sid):
        require(re.fullmatch(HEX, sid), "An explicit full snapshot ID is required")
        records = [
            r
            for r in self.state["checkpoints"].values()
            if r["stored"] and r["stored"]["snapshot_id"] == sid
        ]
        snapshots = {s["id"]: s for s in self.restic.snapshots()}
        require(sid in snapshots, "Stored snapshot is missing")
        require(len(records) <= 1, "Snapshot is associated with multiple checkpoints")
        if records:
            entry = records[0]["entry"]
        else:
            # Recover the association from the archived manifest without original host files.
            paths = snapshots[sid]["paths"]
            require(len(paths) == 1 and Path(paths[0]).is_absolute(), "Ambiguous snapshot paths")
            root = Path(paths[0])
            relative = root.relative_to(self.p.backup_dir)
            require(
                len(relative.parts) == 2 and ".." not in relative.parts, "Unexpected source path"
            )
            raw, _ = self.restic.command(["dump", sid, "/backup.json"])
            entry = checkpoint_bytes(raw, root, self.p)
            record = self.entry_record(entry)
            require(record["stored"] is None, "A different snapshot is already associated")
        self.match(snapshots[sid], entry)
        self.p.guard(reserve=3 * entry["bytes"])
        evidence = {"snapshot_id": sid, "started_at": now().isoformat(), "ok": False}
        self.state["restore_checks"].append(evidence)
        self.save()
        try:
            extracted = self.extract_verify(sid, entry)
            if not records:
                record["stored"] = {
                    "snapshot_id": sid,
                    "adopted_at": now().isoformat(),
                    "archive_started_at": None,
                    "archive_finished_at": None,
                    "evidence": "explicit_restore_extracted_and_fully_verified",
                }
                self.save()
            restored, new = extracted.parent / "store", extracted.parent / "new-backup"
            self.phase(
                "restore-store",
                lambda: backup.restore_backup(
                    extracted, restored, min_free_gib=self.p.min_free_gib
                ),
            )
            reads = self.phase("readback", lambda: compare_readers(extracted, restored))
            store = Store(restored)
            try:
                validation = self.phase("validate-store", store.validate)
                require(validation["ok"], "Restored store validation failed")
                self.phase(
                    "new-backup",
                    lambda: backup.create_backup(store, new, min_free_gib=self.p.min_free_gib),
                )
            finally:
                store.close()
            verified = self.phase("verify-new-backup", lambda: backup.verify_backup(new))
            _, new_manifest = backup.load_backup(new)
            require(
                new_manifest["source_root"] == str(restored)
                and new_manifest["backup_id"] != entry["backup_id"]
                and new_manifest["storage"]["mode"] == "independent_copy",
                "New backup identity differs",
            )
            _, old = backup.load_backup(extracted)
            for f in old["files"]:
                files = [safe_file(root, f["path"]) for root in (extracted, restored, new)]
                require(
                    len({(x.stat().st_dev, x.stat().st_ino) for x in files}) == 3,
                    "Recovery copies share an inode",
                )
            require(
                sha256((extracted / "backup.json").read_bytes()) == entry["manifest_sha256"],
                "Original recovery manifest changed",
            )
            evidence.update(
                ok=True,
                extracted=str(extracted),
                restored=str(restored),
                new_backup=str(new),
                new_backup_id=verified["backup_id"],
                queries=reads,
                independent_file_checks=len(old["files"]),
                services_started=False,
            )
        except BaseException as exc:
            evidence["error"] = str(exc)
            raise
        finally:
            evidence["finished_at"] = now().isoformat()
            self.save()
        return evidence


def overview(profile):
    # Read-only local report. Repository identity/presence checks are explicit in run/verify.
    state = load_state(profile)
    environment = {"ok": False, "expected_filesystem_uuid": profile.backup_filesystem_uuid}
    entries, incomplete = [], []
    try:
        profile.guard()
        disk = os.statvfs(profile.backup_mount)
        environment.update(
            ok=True,
            mount=str(profile.backup_mount),
            filesystem_uuid=profile.backup_filesystem_uuid,
            free_bytes=disk.f_bavail * disk.f_frsize,
            free_inodes=disk.f_favail,
        )
        entries, incomplete = discover(profile)
    except Exception as exc:
        environment.update(ok=False, error=str(exc))
    records = state["checkpoints"]
    pending = [e for e in entries if not records.get(e["backup_id"], {}).get("stored")]
    stored = [r for r in records.values() if r["stored"]]
    latest = max(stored, key=lambda r: r["entry"]["checkpoint_at"]) if stored else None
    result = {
        "identity": profile.identity,
        "latest_checkpoint": entries[-1] if entries else None,
        "latest_stored": latest,
        "pending": pending,
        "incomplete_folders": incomplete,
        "repository_check": state["repository_check"],
        "restore_checks": state["restore_checks"],
        "repository_live_checked": False,
        "last_operation": state.get("last_operation"),
        "environment": environment,
        "active_operation_record": state.get("active_operation"),
        "last_phase_started": state.get("last_phase_started"),
    }
    result["daily_stale"] = (
        not entries or (now() - timestamp(entries[-1]["completed_at"])).total_seconds() > 36 * 3600
    )
    result["oldest_pending_seconds"] = max(
        ((now() - timestamp(e["completed_at"])).total_seconds() for e in pending), default=0
    )
    result["archive_late"] = result["oldest_pending_seconds"] > 2 * 3600
    result["checkpoint_errors"] = {bid: r["error"] for bid, r in records.items() if r.get("error")}
    result["pending_count"] = len(pending) if environment["ok"] else None
    lock_path = profile.state_root / profile.expected_repository_id / "archive.lock"
    result["lock_busy"] = False
    no_symlinks(lock_path)
    if lock_path.exists():
        with lock_path.open("r") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                result["lock_busy"] = True
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)
    checked = state["repository_check"] or {}
    result["unchecked_snapshot_ids"] = sorted(
        {r["stored"]["snapshot_id"] for r in stored} - set(checked.get("snapshot_ids", []))
    )
    result["healthy"] = bool(
        environment["ok"]
        and not result["daily_stale"]
        and not result["archive_late"]
        and not result["checkpoint_errors"]
        and not result["unchecked_snapshot_ids"]
        and (not checked or checked.get("ok"))
        and (state.get("last_operation") or {"ok": True}).get("ok")
    )
    return result


def watch(profile, report, stop):
    while not stop.is_set():
        try:
            row = sample_streams(profile.store_root)
        except Exception as exc:
            row = {"at": now(), "sampling_error": str(exc)}
        with report.open("ab") as handle:
            handle.write(json_bytes(row) + b"\n")
        stop.wait(10)


def execute(profile, action, snapshot_id=None):
    profile.validate()
    if action in ("plan", "status"):
        # A missing mount must not look like an empty, healthy set of daily copies.
        if action == "plan":
            profile.guard()
        return overview(profile)
    run_id = uuid.uuid4().hex
    reports = profile.reports_root / run_id
    reports.mkdir(parents=True, exist_ok=False)
    receipt = {
        "run_id": run_id,
        "action": action,
        "profile_sha256": profile.fingerprint,
        "started_at": now().isoformat(),
        "ok": False,
    }
    try:
        with repository_lock(profile):
            archive = Archive(profile, reports)
            try:
                profile.guard()
                receipt["restic_version"] = archive.restic.identify()
                receipt["binary_sha256"] = sha256(profile.restic_binary.read_bytes())
                archive.state["active_operation"] = {
                    "run_id": run_id,
                    "action": action,
                    "started_at": receipt["started_at"],
                    "pid": os.getpid(),
                }
                archive.save()
                stop = threading.Event()
                worker = threading.Thread(
                    target=watch, args=(profile, reports / "monitor.jsonl", stop), daemon=True
                )
                worker.start()
                try:
                    if action == "run":
                        result = archive.run()
                    elif action == "verify":
                        archive.verify()
                        result = archive.state["repository_check"]
                    else:
                        result = archive.restore_check(snapshot_id)
                    archive.restic.identify()
                    receipt.update(ok=True, result=result)
                finally:
                    stop.set()
                    worker.join(timeout=15)
            except BaseException as exc:
                receipt["error"] = str(exc)
                raise
            finally:
                receipt["finished_at"] = now().isoformat()
                archive.state.pop("active_operation", None)
                archive.state["last_operation"] = receipt
                archive.save()
    except Busy:
        receipt["status"] = "lock_busy"
        raise
    except BaseException as exc:
        receipt["error"] = str(exc)
        raise
    finally:
        receipt["finished_at"] = now().isoformat()
        atomic_write(reports / "run.json", json_bytes(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("action", choices=("plan", "status", "run", "verify", "restore-check"))
    parser.add_argument("--snapshot-id")
    args = parser.parse_args()
    try:
        require(
            (args.action == "restore-check") == (args.snapshot_id is not None),
            "Only restore-check requires --snapshot-id",
        )
        profile = Profile.load(args.profile)
        if args.action not in ("plan", "status"):
            os.nice(10)
            subprocess.run(["ionice", "-c", "2", "-n", "7", "-p", str(os.getpid())], check=True)
        result = execute(profile, args.action, args.snapshot_id)
        print(json_bytes(result).decode(), flush=True)
    except Busy as exc:
        print(
            json_bytes({"ok": False, "status": "lock_busy", "error": str(exc)}).decode(), flush=True
        )
        raise SystemExit(75)
    except (Exception, KeyboardInterrupt) as exc:
        print(
            json_bytes({"ok": False, "error": str(exc), "type": type(exc).__name__}).decode(),
            flush=True,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
