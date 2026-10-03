"""Explicit, resumable retirement of archived working checkpoints only.

No timer, restic forget/prune, source-data removal, or trial-artifact removal.
Plan files are immutable approvals; a separate durable journal records progress.
"""

import argparse
import json
import os
import re
import stat
import tomllib
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from marketdata import backup
from marketdata.checkpoint_locks import checkpoint_locks, plain_path
from marketdata.domain import json_bytes, now, sha256
from marketdata.fileio import sync_parent
from marketdata.maintenance import maintenance_lock
from marketdata.store import atomic_write

from scripts import archive_backups as archive
from scripts.checkpoint_code import current_code
from scripts.restic_trial import overlap, require


def durable_directory(path):
    """Persist newly created ancestors as well as the final directory entry."""
    path = plain_path(path)
    missing, current = [], path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
        sync_parent(directory)


def persist(path, value):
    durable_directory(path.parent)
    atomic_write(plain_path(path), json_bytes(value))


@dataclass(frozen=True)
class Policy:
    archive_profile: Path
    recovery_receipt: Path
    recovery_receipt_sha256: str
    keep_latest: int = 3
    min_age_hours: int = 72
    apply_enabled: bool = False

    @classmethod
    def load(cls, path):
        values = tomllib.loads(plain_path(path).read_text())
        require(set(values) == set(cls.__annotations__), "Unexpected/missing policy fields")
        for key in ("archive_profile", "recovery_receipt"):
            require(isinstance(values[key], str) and Path(values[key]).is_absolute(), key)
            values[key] = plain_path(values[key])
        result = cls(**values)
        result.validate()
        return result

    def validate(self):
        # These are the agreed initial policy bounds, not an unreviewed tuning interface.
        require(type(self.keep_latest) is int and self.keep_latest >= 3, "Keep at least 3")
        require(type(self.min_age_hours) is int and self.min_age_hours >= 72, "Protect >=72 hours")
        require(type(self.apply_enabled) is bool, "apply_enabled must be a boolean")
        require(re.fullmatch(archive.HEX, self.recovery_receipt_sha256), "Recovery receipt SHA")
        for path in (self.archive_profile, self.recovery_receipt):
            require(path.is_absolute(), "Absolute policy paths required")
            plain_path(path)

    @property
    def fingerprint(self):
        return sha256(
            json_bytes({k: str(v) if isinstance(v, Path) else v for k, v in vars(self).items()})
        )


def selection(entries, policy, instant):
    ordered = sorted(entries, key=lambda e: (e["checkpoint_at"], e["backup_id"]), reverse=True)
    latest = {e["backup_id"] for e in ordered[: policy.keep_latest]}
    keep, candidates = [], []
    for entry in ordered:
        reasons = []
        if entry["backup_id"] in latest:
            reasons.append("latest_verified_checkpoints")
        if instant - archive.timestamp(entry["completed_at"]) < timedelta(
            hours=policy.min_age_hours
        ):
            reasons.append("completed_less_than_min_age")
        (keep if reasons else candidates).append(
            {
                **entry,
                "protection": reasons,
                "age_seconds": (instant - archive.timestamp(entry["completed_at"])).total_seconds(),
            }
        )
    return keep, candidates


def source_entry(selected):
    return {
        k: v for k, v in selected.items() if k not in {"protection", "age_seconds", "snapshot_id"}
    }


def filesystem_sample(profile):
    filesystem_uuid = archive.mount_identity(profile.backup_mount)
    require(
        filesystem_uuid == profile.backup_filesystem_uuid, "Space sample filesystem UUID differs"
    )
    info = os.statvfs(plain_path(profile.backup_mount))
    return {
        "ok": True,
        "sampled_at": now().isoformat(),
        "mount": str(profile.backup_mount),
        "device": profile.backup_mount.stat().st_dev,
        "filesystem_uuid": filesystem_uuid,
        "available_bytes": info.f_bavail * info.f_frsize,
        "free_bytes": info.f_bfree * info.f_frsize,
        "total_bytes": info.f_blocks * info.f_frsize,
    }


def finish_space_observation(observation, profile):
    try:
        after = filesystem_sample(profile)
        before = observation["before"]
        require(after["device"] == before["device"], "Space sample filesystem changed")
        observation.update(
            after=after,
            observed_available_change_bytes=after["available_bytes"] - before["available_bytes"],
            observed_free_change_bytes=after["free_bytes"] - before["free_bytes"],
        )
    except Exception as exc:
        # Preserve the deletion outcome/exception if measuring space fails.
        observation["after"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def signature(info):
    result = {"dev": info.st_dev, "ino": info.st_ino, "mode": info.st_mode}
    if stat.S_ISREG(info.st_mode):
        result.update(size=info.st_size, mtime_ns=info.st_mtime_ns)
    return result


def tree_inventory(root, manifest=None):
    """No-follow metadata inventory; no data hashing or live catalog validation."""
    root = plain_path(root)
    info = root.lstat()
    require(stat.S_ISDIR(info.st_mode), "Checkpoint root must be a directory")
    records, allocation = {}, {}
    for directory, dirs, files, fd in os.fwalk(root, follow_symlinks=False):
        relative = Path(directory).relative_to(root).as_posix()
        current = os.fstat(fd)
        require(current.st_dev == info.st_dev, "Nested filesystem in checkpoint")
        records[relative] = signature(current)
        allocation[relative] = {"blocks": current.st_blocks, "links": current.st_nlink}
        for name in dirs + files:
            item = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(item.st_dev == info.st_dev, "Nested filesystem in checkpoint")
            require(
                stat.S_ISREG(item.st_mode) or stat.S_ISDIR(item.st_mode), "Symlink/special input"
            )
            if stat.S_ISREG(item.st_mode):
                path = (Path(directory) / name).relative_to(root).as_posix()
                records[path] = signature(item)
                allocation[path] = {"blocks": item.st_blocks, "links": item.st_nlink}
    if manifest is not None:
        expected = {f["path"] for f in manifest["files"]} | {"backup.json"}
        require(len(expected) == len(manifest["files"]) + 1, "Duplicate inventory paths")
        directories = {"."}
        for file in expected:
            backup.safe_file(root, file)
            directories.update(p.as_posix() for p in Path(file).parents)
        require(set(records) == expected | directories, "Unexpected/missing checkpoint contents")
        for file in expected:
            require(stat.S_ISREG(records[file]["mode"]), "Expected regular checkpoint file")
        for file in manifest["files"]:
            require(records[file["path"]]["size"] == file["bytes"], "Checkpoint file size changed")
    return {"records": records, "allocation": allocation}


def reclaim_estimate(inventories):
    refs, objects, directory_blocks, logical = Counter(), {}, 0, 0
    for inventory in inventories:
        for path, record in inventory["records"].items():
            allocated = inventory["allocation"][path]
            if stat.S_ISDIR(record["mode"]):
                directory_blocks += allocated["blocks"] * 512
            else:
                key = (record["dev"], record["ino"])
                refs[key] += 1
                objects[key] = allocated
                logical += record["size"]
    files = sum(v["blocks"] * 512 for k, v in objects.items() if refs[k] == v["links"])
    return {
        "logical_file_bytes": logical,
        "estimated_reclaim_file_bytes": files,
        "estimated_reclaim_directory_bytes": directory_blocks,
        "estimated_reclaim_bytes": files + directory_blocks,
        "semantics": "point-in-time allocated blocks with all hardlinks in candidates; "
        "open descriptors/filesystem sharing and concurrent writes can affect observed free space",
    }


class Retirement:
    def __init__(self, policy, reports):
        policy.validate()
        self.policy, self.p = policy, archive.Profile.load(policy.archive_profile)
        self.root = self.p.state_file.parent / "working-retention"
        self.trash = (
            self.p.backup_dir.parent
            / ("." + self.p.backup_dir.name + ".retiring")
            / self.p.archive_scope
        )
        for protected in (
            self.p.store_root,
            self.p.backup_dir,
            self.p.restic_repository,
            self.p.restore_work_root,
            self.p.state_root,
            self.p.reports_root,
            self.p.cache_root,
            self.p.restic_password_file,
            policy.recovery_receipt,
        ):
            require(
                not overlap(self.trash, protected), "Retirement staging overlaps protected path"
            )
        plain_path(self.root)
        plain_path(self.trash)
        self.a = archive.Archive(self.p, reports)

    @contextmanager
    def locks(self):
        self.p.guard()
        require(
            self.p.backup_dir.stat().st_dev == self.p.backup_mount.stat().st_dev,
            "Checkpoint directory is on a different filesystem",
        )
        # Opening a Store would create/migrate the live catalog. Only the existing
        # maintenance lock is required, never the collector writer/layout locks.
        plain_path(self.p.store_root / "state/maintenance.lock")
        with (
            maintenance_lock(SimpleNamespace(root=self.p.store_root)),
            archive.repository_lock(self.p),
        ):
            self.a.state = archive.load_state(self.p)
            yield

    def load_plan(self, pid, *, current_config=True):
        require(re.fullmatch(archive.HEX, pid or ""), "Explicit full plan ID required")
        result = json.loads(plain_path(self.root / "plans" / f"{pid}.json").read_bytes())
        require(
            result.pop("plan_id") == pid and sha256(json_bytes(result)) == pid, "Plan hash differs"
        )
        result["plan_id"] = pid
        require(result["identity"] == self.p.identity, "Plan identity differs")
        if current_config:
            require(result.get("schema") == 2, "Legacy plan lacks code identity; create a new plan")
            self.check_code(result)
            require(
                result["profile_sha256"] == self.p.fingerprint, "Profile changed; new plan required"
            )
            require(
                result["policy_sha256"] == self.policy.fingerprint,
                "Policy changed; new plan required",
            )
        return result

    def check_code(self, plan):
        require(
            plan.get("code") == current_code(), "Approved code identity differs; new plan required"
        )

    def check_bindings(self, plan):
        selections = [(e, e["snapshot_id"]) for e in plan["keep"]]
        selections += [(c["entry"], c["snapshot_id"]) for c in plan["candidates"]]
        for entry, sid in selections:
            record = self.a.state["checkpoints"].get(entry["backup_id"], {})
            require(
                record.get("entry") == source_entry(entry)
                and (record.get("stored") or {}).get("snapshot_id") == sid,
                "Approved checkpoint/snapshot association changed; new plan required",
            )

    def journals(self):
        result = []
        for path in sorted((self.root / "journals").glob("*.json")):
            value = json.loads(plain_path(path).read_bytes())
            require(value["identity"] == self.p.identity, "Journal identity differs")
            require(value["plan_id"] == path.stem, "Journal plan ID differs")
            plan = self.load_plan(path.stem, current_config=False)
            expected = {c["entry"]["backup_id"]: c for c in plan["candidates"]}
            require(set(value["items"]) == set(expected), "Journal target set differs from plan")
            require(
                value["status"] in {"applying", "interrupted", "complete"}, "Journal status differs"
            )
            for bid, item in value["items"].items():
                require(
                    {key: item[key] for key in expected[bid]} == expected[bid],
                    "Journal target differs from approved plan",
                )
                require(
                    item["staged"] == str(self.trash / path.stem / bid), "Journal staging differs"
                )
                require(
                    item["phase"] in {"prepared", "deleting", "removing_root", "complete"},
                    "Journal phase differs",
                )
            require(
                value["status"] != "complete"
                or all(i["phase"] == "complete" for i in value["items"].values()),
                "Incomplete items in completed journal",
            )
            result.append(value)
        return result

    def recovery_guard(self, snapshots):
        raw = plain_path(self.policy.recovery_receipt).read_bytes()
        require(sha256(raw) == self.policy.recovery_receipt_sha256, "Recovery receipt changed")
        receipt = json.loads(raw)
        r = receipt.get("result", {})
        require(
            receipt.get("ok") is True and receipt.get("action") == "restore-check",
            "Recovery failed",
        )
        require(
            r.get("ok") is True
            and r.get("queries", {}).get("ok") is True
            and r.get("independent_file_checks", 0) > 0
            and r.get("services_started") is False
            and re.fullmatch(archive.HEX, r.get("new_backup_id", "")),
            "Complete recovery/readback/independent backup evidence required",
        )
        sid = r["snapshot_id"]
        require(sid in snapshots, "Recovery-proven snapshot is missing")
        records = [
            x
            for x in self.a.state["checkpoints"].values()
            if (x.get("stored") or {}).get("snapshot_id") == sid
        ]
        require(
            len(records) == 1 and records[0]["entry"]["backup_id"] != r["new_backup_id"],
            "Recovery association differs",
        )
        return {
            "snapshot_id": sid,
            "receipt_sha256": sha256(raw),
            "finished_at": receipt["finished_at"],
        }

    def audit(self, *, resume=None):
        self.p.guard()
        self.a.restic.identify()
        snapshots = {s["id"]: s for s in self.a.restic.snapshots()}
        state = self.a.state
        checked = state["repository_check"] or {}
        require(
            checked.get("ok") is True
            and checked.get("status") == "complete"
            and checked.get("read_data") is True,
            "Repository check incomplete/failed",
        )
        require(not state.get("active_operation"), "Unresolved archive operation")
        require(
            (state.get("last_operation") or {"ok": True}).get("ok"),
            "Unresolved last archive failure",
        )
        latest_restore = {}
        for item in state["restore_checks"]:
            latest_restore[item["snapshot_id"]] = item
        require(all(r.get("ok") for r in latest_restore.values()), "Unresolved restore failure")
        journals = self.journals()
        pending = [j for j in journals if j["status"] != "complete"]
        require(
            not pending or (len(pending) == 1 and pending[0]["plan_id"] == resume),
            "Unfinished retirement must be resumed first",
        )
        retired = {}
        for journal in journals:
            for bid, item in journal["items"].items():
                require(bid not in retired, "Duplicate retirement identity")
                retired[bid] = item
        entries, incomplete = archive.discover(self.p)
        require(not incomplete, "Incomplete checkpoint folders need separate review")
        live = {e["backup_id"]: e for e in entries}
        require(len(live) >= self.policy.keep_latest, "Fewer than required verified working copies")
        instant = now()
        require(
            instant - archive.timestamp(entries[-1]["completed_at"]) <= timedelta(hours=36),
            "Latest daily checkpoint is stale",
        )
        for bid, record in state["checkpoints"].items():
            require(not record.get("error"), f"Unresolved checkpoint failure: {bid}")
            stored = record.get("stored")
            require(
                stored and record.get("input_verified_at"),
                f"Unverified/unarchived checkpoint: {bid}",
            )
            sid = stored["snapshot_id"]
            require(sid in snapshots, f"Previously stored snapshot is missing: {sid}")
            require(sid in checked.get("snapshot_ids", []), f"Unchecked snapshot: {sid}")
            self.a.match(snapshots[sid], record["entry"])
            if bid in retired and retired[bid]["phase"] != "prepared":
                require(bid not in live, "Retired checkpoint unexpectedly reappeared")
            if bid not in live:
                require(
                    bid in retired and retired[bid]["entry"] == record["entry"],
                    f"Unexplained missing local checkpoint: {bid}",
                )
                item = retired[bid]
                require(
                    not Path(item["entry"]["path"]).exists(), "Retired source unexpectedly exists"
                )
                if item["phase"] == "complete":
                    require(
                        not Path(item["staged"]).exists(), "Completed retirement staging reappeared"
                    )
            else:
                require(
                    live[bid] == record["entry"], "Local checkpoint differs from stored identity"
                )
        require(set(live) <= set(state["checkpoints"]), "Unarchived local checkpoint")
        proof = self.recovery_guard(snapshots)
        assessed_at = now()
        keep, candidates = selection(entries, self.policy, assessed_at)
        keep = [
            {**e, "snapshot_id": state["checkpoints"][e["backup_id"]]["stored"]["snapshot_id"]}
            for e in keep
        ]
        return {
            "assessed_at": assessed_at.isoformat(),
            "keep": keep,
            "candidates": candidates,
            "recovery": proof,
            "checked_snapshot_ids": checked["snapshot_ids"],
            "repository_check_finished_at": checked["finished_at"],
        }

    def plan(self):
        code = current_code()
        with self.locks():
            audit = self.audit()
            entries = audit["keep"] + audit["candidates"]
            with checkpoint_locks([e["path"] for e in entries]):
                inventories, candidates = [], []
                for entry in audit["candidates"]:
                    require(
                        Path(entry["path"]).stat().st_dev == self.p.backup_mount.stat().st_dev,
                        "Checkpoint is on a different filesystem",
                    )
                    manifest = json.loads((Path(entry["path"]) / "backup.json").read_bytes())
                    inventory = tree_inventory(Path(entry["path"]), manifest)
                    inventories.append(inventory)
                    candidates.append(
                        {
                            "entry": source_entry(entry),
                            "age_seconds": entry["age_seconds"],
                            "snapshot_id": self.a.state["checkpoints"][entry["backup_id"]][
                                "stored"
                            ]["snapshot_id"],
                            "tree_sha256": sha256(json_bytes(inventory["records"])),
                            "root": inventory["records"]["."],
                        }
                    )
                excluded = []
                included = {entry["path"] for entry in entries}
                for marker in sorted(self.p.backup_dir.glob("*/*/backup.json")):
                    if str(marker.parent) not in included:
                        old = archive.checkpoint(marker.parent, self.p)
                        require(
                            old["checkpoint_at"][:10] < self.p.enroll_from_utc_date,
                            "Unexpected excluded checkpoint",
                        )
                        excluded.append({**old, "reason": "before_enrollment"})
                result = {
                    "schema": 2,
                    "code": code,
                    "assessed_at": audit["assessed_at"],
                    "identity": self.p.identity,
                    "created_at": now(),
                    "profile_sha256": self.p.fingerprint,
                    "policy_sha256": self.policy.fingerprint,
                    "apply_enabled": self.policy.apply_enabled,
                    "keep_latest": self.policy.keep_latest,
                    "min_age_hours": self.policy.min_age_hours,
                    "keep": audit["keep"],
                    "excluded": excluded,
                    "candidates": candidates,
                    "recovery": audit["recovery"],
                    "repository_check_finished_at": audit["repository_check_finished_at"],
                    "checked_snapshot_ids": audit["checked_snapshot_ids"],
                    "space": reclaim_estimate(inventories),
                    "deleted_checkpoints": 0,
                }
                result["plan_id"] = sha256(json_bytes(result))
                self.check_code(result)
                persist(self.root / "plans" / f"{result['plan_id']}.json", result)
                return result

    def save_journal(self, journal):
        journal["updated_at"] = now().isoformat()
        persist(self.root / "journals" / f"{journal['plan_id']}.json", journal)

    def inventory_file(self, pid, bid):
        return self.root / "inventories" / pid / f"{bid}.json"

    def apply(self, pid, *, hook=lambda phase, item: None):
        require(self.policy.apply_enabled, "Apply disabled: preview-only policy")
        with self.locks():
            plan = self.load_plan(pid)
            existing = [j for j in self.journals() if j["plan_id"] == pid]
            if existing and existing[0]["status"] == "complete":
                self.audit()
                self.check_bindings(plan)
                return {"ok": True, "already_complete": True, "journal": existing[0]}
            audit = self.audit(resume=pid if existing else None)
            self.check_bindings(plan)
            protected = {e["backup_id"] for e in audit["keep"]}
            require(
                not protected.intersection(c["entry"]["backup_id"] for c in plan["candidates"]),
                "Planned candidate is now protected",
            )
            if not existing:
                require(
                    [e["backup_id"] for e in audit["keep"]]
                    == [e["backup_id"] for e in plan["keep"]]
                    and {e["backup_id"] for e in audit["candidates"]}
                    == {c["entry"]["backup_id"] for c in plan["candidates"]},
                    "Selection changed; create and review a new plan",
                )
            stage = self.trash / pid
            durable_directory(stage)
            require(
                stage.stat().st_dev == self.p.backup_dir.stat().st_dev, "Staging filesystem differs"
            )
            inputs = [e["path"] for e in audit["keep"]] + [
                c["entry"]["path"] for c in plan["candidates"]
            ]
            inputs.append(stage / "input-lock")
            with checkpoint_locks(inputs, exclusive=True, wait=False):
                # Recheck retained physical bytes before any retirement, not the
                # expensive full store logical/recovery/re-backup procedure.
                for entry in audit["keep"]:
                    backup.verify_backup(entry["path"])
                journal = (
                    existing[0]
                    if existing
                    else {
                        "schema": 1,
                        "identity": self.p.identity,
                        "plan_id": pid,
                        "started_at": now().isoformat(),
                        "status": "applying",
                        "items": {},
                        "attempts": [],
                    }
                )
                if not existing:
                    # Validate every target before retiring even the first one.
                    for candidate in plan["candidates"]:
                        e = candidate["entry"]
                        require(
                            archive.checkpoint(Path(e["path"]), self.p) == e,
                            "Candidate manifest changed",
                        )
                        inventory = tree_inventory(
                            Path(e["path"]),
                            json.loads((Path(e["path"]) / "backup.json").read_bytes()),
                        )
                        require(
                            sha256(json_bytes(inventory["records"])) == candidate["tree_sha256"],
                            "Candidate tree changed; new plan required",
                        )
                        records = inventory["records"]
                        persist(self.inventory_file(pid, e["backup_id"]), records)
                        journal["items"][e["backup_id"]] = {
                            **candidate,
                            "phase": "prepared",
                            "staged": str(stage / e["backup_id"]),
                            "unlinked_files": 0,
                        }
                self.check_code(plan)
                attempt = {
                    "started_at": now().isoformat(),
                    "ok": False,
                    "code_id": plan["code"]["code_id"],
                    "space_observation": {
                        "before": filesystem_sample(self.p),
                        "after": None,
                        "observed_available_change_bytes": None,
                        "observed_free_change_bytes": None,
                        "exact_reclaimed_bytes": None,
                        "includes_concurrent_activity": True,
                    },
                }
                journal["attempts"].append(attempt)
                journal["status"] = "applying"
                self.save_journal(journal)
                try:
                    for item in journal["items"].values():
                        self.p.guard()
                        self.check_code(plan)
                        self.retire_item(journal, item, hook)
                    journal["status"] = "complete"
                    attempt["ok"] = True
                except BaseException as exc:
                    journal["status"] = "interrupted"
                    attempt["error"] = f"{type(exc).__name__}: {exc}"
                    raise
                finally:
                    finish_space_observation(attempt["space_observation"], self.p)
                    attempt["finished_at"] = now().isoformat()
                    self.save_journal(journal)
                return {"ok": True, "already_complete": False, "journal": journal}

    def retire_item(self, journal, item, hook):
        if item["phase"] == "complete":
            return
        source, target = plain_path(item["entry"]["path"]), plain_path(item["staged"])
        require(
            target == self.trash / journal["plan_id"] / item["entry"]["backup_id"],
            "Staging scope differs",
        )
        raw = plain_path(
            self.inventory_file(journal["plan_id"], item["entry"]["backup_id"])
        ).read_bytes()
        records = json.loads(raw)
        require(sha256(json_bytes(records)) == item["tree_sha256"], "Retirement inventory changed")
        if source.exists():
            require(
                item["phase"] == "prepared" and not target.exists(),
                "Ambiguous retirement locations",
            )
            current = tree_inventory(source)["records"]
            require(current == records, "Checkpoint changed before retirement")
            require(
                archive.checkpoint(source, self.p) == item["entry"], "Candidate manifest changed"
            )
            hook("before_move", item)
            # Intent and original inventory are already fsynced before this rename.
            source.rename(target)
            sync_parent(source)
            sync_parent(target)
            hook("after_move", item)
        if not target.exists():
            require(item["phase"] == "removing_root", "Retirement tree disappeared unexpectedly")
        else:
            require(signature(target.lstat()) == item["root"], "Staged root identity differs")
            current = tree_inventory(target)["records"]
            require(
                all(records.get(k) == v for k, v in current.items()),
                "Unexpected/changed file in retirement tree",
            )
            if item["phase"] == "prepared":
                require(current == records, "Files disappeared before deletion began")
            item["files_absent_when_resumed"] = sum(
                stat.S_ISREG(record["mode"]) and path not in current
                for path, record in records.items()
            )
            item["phase"] = "deleting"
            self.save_journal(journal)
            for directory, dirs, files, fd in os.fwalk(
                target, topdown=False, follow_symlinks=False
            ):
                relative = Path(directory).relative_to(target)
                require(
                    signature(os.fstat(fd)) == records.get(relative.as_posix()),
                    "Directory identity changed",
                )
                for name in files:
                    path = (relative / name).as_posix()
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    require(
                        stat.S_ISREG(info.st_mode) and signature(info) == records.get(path),
                        "Unexpected file during retirement",
                    )
                    os.unlink(name, dir_fd=fd)
                    item["unlinked_files"] += 1
                    hook("after_unlink", item)
                for name in dirs:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    require(
                        stat.S_ISDIR(info.st_mode)
                        and signature(info) == records.get((relative / name).as_posix()),
                        "Unexpected directory during retirement",
                    )
                    os.rmdir(name, dir_fd=fd)
                os.fsync(fd)
            item["phase"] = "removing_root"
            self.save_journal(journal)
            hook("before_root_remove", item)
            target.rmdir()
            sync_parent(target)
            hook("after_root_remove", item)
        item["phase"] = "complete"
        item["finished_at"] = now().isoformat()
        self.save_journal(journal)


def execute(policy, action, plan_id=None):
    p = archive.Profile.load(policy.archive_profile)
    report = p.reports_root / (now().strftime("%Y%m%dT%H%M%SZ") + "-retention-" + uuid.uuid4().hex)
    require(not plain_path(report).exists(), "Fresh report path required")
    durable_directory(report)
    receipt = {"action": action, "started_at": now(), "ok": False}
    try:
        receipt["code"] = current_code()
        agent = Retirement(policy, report)
        if action == "plan":
            result = agent.plan()
        elif action == "apply":
            result = agent.apply(plan_id)
        elif action == "status":
            result = {
                "identity": p.identity,
                "apply_enabled": policy.apply_enabled,
                "journals": agent.journals(),
                "repository_live_checked": False,
            }
        else:
            raise ValueError("Unknown retirement action")
        receipt.update(ok=True, result=result)
        return receipt
    except BaseException as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        receipt["finished_at"] = now()
        persist(report / "retention.json", receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("action", choices=("plan", "apply", "status"))
    parser.add_argument("--plan-id")
    args = parser.parse_args()
    try:
        print(json_bytes(execute(Policy.load(args.policy), args.action, args.plan_id)).decode())
    except (Exception, KeyboardInterrupt) as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
