"""Preserve event source bytes and logical IDs while consolidating physical files."""

import hashlib
import os
import tempfile
import zipfile
from collections import defaultdict
from datetime import timedelta

from . import job_clock as jc
from .backup import require_capacity, reserve_bytes
from .domain import DataError, now, sha256, utc
from .fileio import layout_lock, packed_record, safe_file, sync_parent


def logical_files(db):
    files = {}
    for table, digest, kind in (
        ("artifacts", "id", "raw"),
        ("revisions", "file_sha", "canonical"),
        ("event_segments", "file_sha", "canonical"),
        ("snapshots", "id", "snapshots"),
    ):
        for row in db.execute(f"SELECT path,{digest} AS digest FROM {table}"):
            item = {"path": row["path"], "sha256": row["digest"], "kind": kind}
            if row["path"] in files and files[row["path"]] != item:
                raise DataError("Conflicting logical file references")
            files[row["path"]] = item
    return files


def verify_archive(root, pack, members):
    if pack["path"] != f"data/packs/{pack['id'][:2]}/{pack['id']}.zip":
        raise DataError("Invalid content-addressed pack path")
    path = safe_file(root, pack["path"])
    if not 0 < pack["bytes"] <= 512 * 1024**2 or not 1 <= pack["members"] <= 4096:
        raise DataError("Invalid pack bounds")
    if path.stat().st_size != pack["bytes"]:
        raise DataError("Pack size mismatch")
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != pack["id"]:
            raise DataError("Pack hash mismatch")
    expected = {r["path"]: r for r in members}
    if len(expected) != len(members) or len(expected) != pack["members"]:
        raise DataError("Pack member count mismatch")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(expected) or set(names) != set(expected):
                raise DataError("Pack member inventory mismatch")
            for name, record in expected.items():
                safe_file(root, name)
                if record["kind"] not in {"raw", "canonical"} or not name.startswith(
                    f"data/{record['kind']}/"
                ):
                    raise DataError("Invalid packed member path/kind")
                member = archive.getinfo(name)
                if (
                    member.compress_type != zipfile.ZIP_STORED
                    or member.file_size != record["bytes"]
                ):
                    raise DataError("Pack member format/size mismatch")
                if sha256(archive.read(member)) != record["sha256"]:
                    raise DataError("Pack member hash mismatch")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise DataError("Invalid file pack") from exc


def verify_packs(store):
    with layout_lock(store.root):
        logical = logical_files(store.db)
        packs = list(store.db.execute("SELECT * FROM file_packs ORDER BY id"))
        count = 0
        for pack in packs:
            members = list(
                store.db.execute("SELECT * FROM packed_files WHERE pack_id=?", (pack["id"],))
            )
            for member in members:
                reference = logical.get(member["path"])
                if reference != {k: member[k] for k in ("path", "sha256", "kind")}:
                    raise DataError("Packed file differs from logical catalog reference")
            verify_archive(store.root, pack, members)
            count += len(members)
    return {"ok": True, "packs": len(packs), "members": count}


def plan_events(
    store, *, collection_ids=(), before=None, target_mib=64, max_members=1024, max_files=20000
):
    if not 1 <= target_mib <= 256 or not 2 <= max_members <= 4096 or not 2 <= max_files <= 100000:
        raise DataError("Pack limits: target MiB 1..256, members 2..4096, files 2..100000")
    observation = jc.active().point("pack_selection") if jc.active() else None
    cutoff = (
        utc(before)
        if before
        else (observation.lower if observation else now()) - timedelta(hours=1)
    )
    if observation and cutoff > observation.lower - timedelta(hours=1):
        raise jc.ClockDeferred("pack_cutoff_not_proven_old")
    selected = set(collection_ids)
    known = {r[0] for r in store.db.execute("SELECT id FROM event_collections")}
    if selected - known:
        raise DataError("Unknown event collection ID")
    candidates, skipped = {}, []
    rows = store.db.execute(
        "SELECT s.*,c.state,c.updated_at FROM event_segments s JOIN event_collections c "
        "ON c.id=s.collection_id WHERE s.dataset IN ('agg_trades','book_depth') "
        "ORDER BY s.dataset,s.first_time,s.id"
    )
    for row in rows:
        if selected and row["collection_id"] not in selected:
            continue
        if row["state"] == "running" or utc(row["updated_at"]) > cutoff:
            continue
        raw = store.db.execute("SELECT * FROM artifacts WHERE id=?", (row["source_id"],)).fetchone()
        for record, digest, kind in ((row, row["file_sha"], "canonical"), (raw, raw["id"], "raw")):
            path = record["path"]
            if path in candidates or packed_record(store.db, path):
                continue
            size = safe_file(store.root, path).stat().st_size
            if size > target_mib * 1024**2:
                skipped.append({"path": path, "reason": "over_target_size"})
                continue
            if len(candidates) >= max_files:
                break
            candidates[path] = {
                "path": path,
                "sha256": digest,
                "bytes": size,
                "kind": kind,
                "group": (row["dataset"], row["first_time"][:10]),
            }
        if len(candidates) >= max_files:
            break
    buckets = defaultdict(list)
    for record in sorted(candidates.values(), key=lambda r: r["path"]):
        group = record.pop("group")
        buckets[group].append(record)
    groups = []
    for key, members in sorted(buckets.items()):
        batch, size = [], 0
        for member in members:
            if batch and (
                len(batch) >= max_members or size + member["bytes"] > target_mib * 1024**2
            ):
                groups.append({"dataset": key[0], "day": key[1], "members": batch, "bytes": size})
                batch, size = [], 0
            batch.append(member)
            size += member["bytes"]
        if batch:
            groups.append({"dataset": key[0], "day": key[1], "members": batch, "bytes": size})
    groups = [g for g in groups if len(g["members"]) >= 2]
    return {
        "checked_at": now(),
        "before": cutoff,
        **({"clock_observation": observation.evidence} if observation else {}),
        "retention": "indefinite",
        "groups": groups,
        "files": sum(len(g["members"]) for g in groups),
        "bytes": sum(g["bytes"] for g in groups),
        "skipped": skipped,
        "candidate_limit_reached": len(candidates) == max_files,
        "note": "Completed/failed/interrupted event collections only; IDs, bytes and catalog references retained.",
    }


def build_pack(store, members, minimum):
    directory = safe_file(store.root, "data/packs")
    directory.mkdir(parents=True, exist_ok=True)
    sync_parent(directory)
    require_capacity(
        directory, sum(m["bytes"] + 2 * len(m["path"]) + 256 for m in members), minimum
    )
    fd, temporary = tempfile.mkstemp(prefix=".staging-", dir=directory)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            for member in members:
                blob = store.file_bytes(member["path"])
                if len(blob) != member["bytes"] or sha256(blob) != member["sha256"]:
                    raise DataError("Pack input hash/size mismatch")
                info = zipfile.ZipInfo(member["path"], date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o600 << 16
                archive.writestr(info, blob)
        with open(temporary, "rb") as handle:
            os.fsync(handle.fileno())
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        relative = f"data/packs/{digest[:2]}/{digest}.zip"
        destination = safe_file(store.root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        pack = {
            "id": digest,
            "path": relative,
            "bytes": os.stat(temporary).st_size,
            "members": len(members),
            "created_at": now().isoformat(),
        }
        if not destination.exists():
            os.replace(temporary, destination)
            sync_parent(destination)
            sync_parent(destination.parent)
        verify_archive(store.root, pack, members)
        return pack
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def pack_events(store, *, apply=False, min_free_gib=10, **options):
    minimum = reserve_bytes(min_free_gib)
    plan = plan_events(store, **options)
    if not apply:
        return {**plan, "applied": False}
    published = []
    for group in plan["groups"]:
        members = group["members"]
        pack = build_pack(store, members, minimum)
        # Publication takes the same writer lock as collectors. An exclusive layout
        # attempt never waits while owning it: pinned readers/backups can finish.
        with store.writer(timeout=60), layout_lock(store.root, exclusive=True), store.db:
            if any(packed_record(store.db, m["path"]) for m in members):
                raise DataError("Packing plan changed; retry from a new plan")
            store.db.execute("INSERT INTO file_packs VALUES(?,?,?,?,?)", tuple(pack.values()))
            store.db.executemany(
                "INSERT INTO packed_files VALUES(?,?,?,?,?)",
                [(m["path"], pack["id"], m["sha256"], m["bytes"], m["kind"]) for m in members],
            )
        published.append(pack)
    return {
        "checked_at": now(),
        "applied": True,
        "retention": "indefinite",
        "packs": published,
        "files": plan["files"],
        "source_bytes": plan["bytes"],
        "loose_files_removed": 0,
    }


def prune_loose(store, *, pack_ids=(), apply=False):
    selected = set(pack_ids)
    packs = list(store.db.execute("SELECT * FROM file_packs ORDER BY id"))
    if selected - {p["id"] for p in packs}:
        raise DataError("Unknown pack ID")
    records, removed = [], []
    for pack in packs:
        if selected and pack["id"] not in selected:
            continue
        with store.writer(timeout=60), layout_lock(store.root, exclusive=True):
            members = list(
                store.db.execute("SELECT * FROM packed_files WHERE pack_id=?", (pack["id"],))
            )
            logical = logical_files(store.db)
            if any(
                logical.get(m["path"]) != {k: m[k] for k in ("path", "sha256", "kind")}
                for m in members
            ):
                raise DataError("Packed file differs from logical catalog reference")
            verify_archive(store.root, pack, members)
            existing = []
            # Verify every duplicate in this pack before removing the first one.
            for member in members:
                path = safe_file(store.root, member["path"])
                if path.exists():
                    if sha256(path.read_bytes()) != member["sha256"]:
                        raise DataError("Loose duplicate differs from verified pack; retained")
                    existing.append((member, path))
            for member, path in existing:
                records.append(
                    {"path": member["path"], "bytes": member["bytes"], "pack_id": pack["id"]}
                )
                if apply:
                    path.unlink()
                    sync_parent(path)
                    removed.append(member["path"])
                    # Raw payloads have their own directory. Remove empty hashed
                    # directories only, stopping before data/<kind>.
                    parent = path.parent
                    boundary = store.root / "data" / member["kind"]
                    while parent != boundary:
                        try:
                            parent.rmdir()
                        except OSError:
                            break
                        sync_parent(parent)
                        parent = parent.parent
    return {
        "checked_at": now(),
        "applied": apply,
        "retention": "indefinite",
        "files": records,
        "removed": len(removed),
        "reclaimable_bytes": sum(r["bytes"] for r in records),
        "logical_files_deleted": 0,
    }
