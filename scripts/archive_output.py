"""Share only freshly read, successfully verified immutable manifest outputs.

Every command retains its original stdout pathname and actual command receipt.
This changes allocation, never query frequency or the expected-content check.
Call before the surrounding run/request is finalized, under the archive lock.
"""

import json
import os
import re
import stat
from pathlib import Path

from marketdata.domain import json_bytes, sha256
from marketdata.store import atomic_write


def require(condition, message):
    if not condition:
        raise ValueError(message)


def regular(path):
    require(not any(p.is_symlink() for p in (path, *path.parents)), "Output symlink")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), "Unowned/nonregular output")
    require(not os.listxattr(path), "Output has unsupported extended attributes")
    return info


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def retain_verified_manifest(reports_root, repository_id, receipt, raw, expected_sha256):
    """Keep exact output bytes; share a closed body only after expected hash passes.

    Failure or interruption preserves an independently readable command output.
    An old receipt is still valid if interruption precedes the storage annotation.
    No previous command receipt, ledger, or request is rewritten.
    """
    require(re.fullmatch(r"[0-9a-f]{64}", repository_id), "Full repository ID required")
    args = receipt.get("arguments", [])
    require(
        len(args) == 3
        and args[0] == "dump"
        and args[2] == "/backup.json"
        and re.fullmatch(r"[0-9a-f]{64}", args[1]),
        "Only exact manifest dump outputs",
    )
    require(
        receipt.get("returncode") == 0
        and not receipt.get("interrupted")
        and receipt.get("finished_at"),
        "Output command did not finish successfully",
    )
    require(sha256(raw) == expected_sha256, "Snapshot manifest differs")
    root, out = Path(reports_root), Path(receipt["stdout"])
    require(".." not in root.parts and ".." not in out.parts, "Output has parent component")
    require(
        out.is_relative_to(root)
        and len(out.relative_to(root).parts) == 2
        and out.suffix == ".stdout",
        "Output outside its report run",
    )
    info = regular(out)
    require(out.read_bytes() == raw, "Command output changed")
    record = out.with_suffix(".json")
    regular(record)
    require(json.loads(record.read_bytes()) == receipt, "Command receipt changed")
    body_dir = root / "manifest-objects-v1" / repository_id / expected_sha256
    require(
        not any(p.is_symlink() for p in (body_dir, *body_dir.parents)), "Body directory symlink"
    )
    # Create only the object's three directories under the existing reports root.
    # Retry also synchronizes their parent entries, even if mkdir already happened.
    for directory in (body_dir.parent.parent, body_dir.parent, body_dir):
        directory.mkdir(exist_ok=True, mode=0o700)
    mode = stat.S_IMODE(info.st_mode)
    body = body_dir / f"{info.st_uid}-{info.st_gid}-{mode:o}.stdout"
    # Make the completed body durable before publishing an additional pathname.
    with out.open("rb") as handle:
        os.fsync(handle.fileno())
    try:
        os.link(out, body, follow_symlinks=False)
    except FileExistsError:
        pass
    saved = regular(body)
    require(
        (saved.st_dev, saved.st_uid, saved.st_gid, stat.S_IMODE(saved.st_mode))
        == (info.st_dev, info.st_uid, info.st_gid, mode),
        "Shared body metadata differs",
    )
    require(body.read_bytes() == raw, "Shared body content differs")
    # An existing body may be left by an interrupted publication. Validate it,
    # then confirm its data and every directory entry before exposing the proof.
    with body.open("rb") as handle:
        os.fsync(handle.fileno())
    for directory in (body_dir, body_dir.parent, body_dir.parent.parent, root):
        sync_directory(directory)
    temp = out.with_name("." + out.name + ".manifest-link-v1")
    if temp.exists() or temp.is_symlink():
        pending = regular(temp)
        require(
            (pending.st_dev, pending.st_ino) == (saved.st_dev, saved.st_ino),
            "Unrecognized pending output link",
        )
    elif (info.st_dev, info.st_ino) != (saved.st_dev, saved.st_ino):
        os.link(body, temp, follow_symlinks=False)
        sync_directory(out.parent)
    if temp.exists():
        require(out.read_bytes() == raw, "Output changed before sharing")
        if out.stat().st_ino == saved.st_ino:
            temp.unlink()
        else:
            os.replace(temp, out)
    require(out.read_bytes() == raw, "Shared output verification failed")
    # A previous replace may have succeeded but its directory fsync failed.
    # This must also run when no replacement (or temporary link) remains to do.
    sync_directory(out.parent)
    updated = {
        **receipt,
        "verified_stdout": {
            "schema": 1,
            "storage": "hardlink_by_sha256_v1",
            "body": str(body),
            "bytes": len(raw),
            "sha256": expected_sha256,
            "repository_id": repository_id,
            "snapshot_id": args[1],
            "snapshot_path": args[2],
            "verification": "fresh_dump_expected_hash_match",
        },
    }
    atomic_write(record, json_bytes(updated))
    receipt.clear()
    receipt.update(updated)
    return updated["verified_stdout"]
