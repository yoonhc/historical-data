import json
import os
import stat
from pathlib import Path

import pytest
from marketdata.domain import json_bytes, sha256

from scripts import archive_output as output

REPOSITORY = "a" * 64
SNAPSHOT = "b" * 64


def completed(root, name, raw, mode=0o600):
    folder = root / name
    folder.mkdir()
    out = folder / "command.stdout"
    out.write_bytes(raw)
    out.chmod(mode)
    receipt = {
        "operation": "dump",
        "arguments": ["dump", SNAPSHOT, "/backup.json"],
        "stdout": str(out),
        "stderr": str(folder / "command.stderr"),
        "started_at": name,
        "finished_at": name + "-finished",
        "returncode": 0,
    }
    out.with_suffix(".json").write_bytes(json_bytes(receipt))
    return out, receipt


def retain(root, receipt, raw):
    return output.retain_verified_manifest(root, REPOSITORY, receipt, raw, sha256(raw))


def allocated(root):
    inodes = {}
    for path in root.rglob("*"):
        if path.is_file():
            s = path.stat()
            inodes[s.st_dev, s.st_ino] = s.st_blocks * 512
    return sum(inodes.values())


def test_repeated_success_retains_one_body_and_small_fresh_receipts(tmp_path):
    raw = b'{"large_manifest":"' + b"x" * 1024**2 + b'"}'
    paths, receipts, sizes = [], [], []
    for n in range(8):
        path, receipt = completed(tmp_path, f"query-{n}", raw)
        proof = retain(tmp_path, receipt, raw)
        paths.append(path)
        receipts.append(receipt)
        sizes.append(allocated(tmp_path))
        assert proof["verification"] == "fresh_dump_expected_hash_match"
        assert receipt["started_at"] == f"query-{n}"
        assert receipt["stdout"] == str(path)
        assert path.read_bytes() == raw
        assert json.loads(path.with_suffix(".json").read_bytes()) == receipt
    assert len({p.stat().st_ino for p in paths}) == 1
    assert len(list((tmp_path / "manifest-objects-v1").rglob("*.stdout"))) == 1
    assert sizes[-1] - sizes[0] < 8 * 8192
    assert len({r["finished_at"] for r in receipts}) == 8


def test_different_body_or_permissions_never_share(tmp_path):
    paths = []
    for name, raw, mode in [("a", b"one", 0o600), ("b", b"two", 0o600), ("c", b"one", 0o644)]:
        path, receipt = completed(tmp_path, name, raw, mode)
        retain(tmp_path, receipt, raw)
        paths.append(path)
        assert path.stat().st_mode & 0o777 == mode
    assert len({p.stat().st_ino for p in paths}) == 3


@pytest.mark.parametrize("kind", ["hash", "failed", "interrupted", "wrong_path", "receipt_changed"])
def test_unverified_outputs_are_kept_independently(tmp_path, kind):
    raw = b"original evidence"
    path, receipt = completed(tmp_path, "run", raw)
    original = path.stat().st_ino
    expected = sha256(raw)
    if kind == "hash":
        expected = "0" * 64
    elif kind == "failed":
        receipt["returncode"] = 1
    elif kind == "interrupted":
        receipt["interrupted"] = True
    elif kind == "wrong_path":
        receipt["arguments"][2] = "/different.json"
    else:
        path.with_suffix(".json").write_text("{}")
    with pytest.raises(ValueError):
        output.retain_verified_manifest(tmp_path, REPOSITORY, receipt, raw, expected)
    assert path.read_bytes() == raw and path.stat().st_ino == original
    assert not (tmp_path / "manifest-objects-v1").exists()


def test_corrupt_canonical_never_replaces_a_fresh_successful_output(tmp_path):
    first, receipt = completed(tmp_path, "first", b"body")
    proof = retain(tmp_path, receipt, b"body")
    Path(proof["body"]).write_bytes(b"corrupted")
    second, receipt = completed(tmp_path, "second", b"body")
    inode = second.stat().st_ino
    with pytest.raises(ValueError, match="content differs"):
        retain(tmp_path, receipt, b"body")
    assert second.read_bytes() == b"body" and second.stat().st_ino == inode


@pytest.mark.parametrize("stage", ["link", "replace", "receipt"])
def test_interruptions_keep_original_reference_readable_and_can_finish(
    tmp_path, monkeypatch, stage
):
    raw = b"body"
    _, first = completed(tmp_path, "first", raw)
    retain(tmp_path, first, raw)
    path, receipt = completed(tmp_path, "second", raw)
    previous = path.with_suffix(".json").read_bytes()
    called = False

    def crash(*args, **kwargs):
        nonlocal called
        called = True
        raise OSError("injected interruption")

    with monkeypatch.context() as m:
        m.setattr(
            output.os if stage != "receipt" else output,
            {"link": "link", "replace": "replace", "receipt": "atomic_write"}[stage],
            crash,
        )
        with pytest.raises(OSError, match="injected"):
            retain(tmp_path, receipt, raw)
    assert called and path.read_bytes() == raw
    assert path.with_suffix(".json").read_bytes() == previous
    retain(tmp_path, receipt, raw)
    assert path.read_bytes() == raw
    assert json.loads(path.with_suffix(".json").read_bytes())["verified_stdout"]
    assert not list(path.parent.glob("*.manifest-link-v1"))


def test_existing_receipt_and_shared_output_are_idempotent(tmp_path):
    path, receipt = completed(tmp_path, "run", b"body")
    retain(tmp_path, receipt, b"body")
    before = path.with_suffix(".json").read_bytes()
    os.link(path, path.with_name("." + path.name + ".manifest-link-v1"))
    retain(tmp_path, receipt, b"body")
    assert path.with_suffix(".json").read_bytes() == before
    assert path.stat().st_nlink == 2


@pytest.mark.parametrize("which", ["body", "stdout", "receipt"])
def test_symlinks_are_rejected(tmp_path, which):
    path, receipt = completed(tmp_path, "run", b"body")
    target = path if which == "stdout" else path.with_suffix(".json")
    if which == "body":
        (tmp_path / "manifest-objects-v1").symlink_to(tmp_path / "other")
    else:
        saved = target.with_suffix(".original")
        target.rename(saved)
        target.symlink_to(saved)
    with pytest.raises(ValueError, match="symlink"):
        retain(tmp_path, receipt, b"body")


@pytest.mark.parametrize("level", range(4))
def test_body_directory_sync_failure_is_retried_before_publishing(tmp_path, monkeypatch, level):
    raw = b"body"
    path, receipt = completed(tmp_path, "run", raw)
    previous = path.with_suffix(".json").read_bytes()
    body_dir = tmp_path / "manifest-objects-v1" / REPOSITORY / sha256(raw)
    directories = [body_dir, body_dir.parent, body_dir.parent.parent, tmp_path]
    real_sync, real_write = output.sync_directory, output.atomic_write
    failed = []

    def unavailable(directory):
        if directory == directories[level]:
            failed.append(directory)
            raise OSError("injected directory sync failure")
        real_sync(directory)

    with monkeypatch.context() as m:
        m.setattr(output, "sync_directory", unavailable)
        for _ in range(2):
            # The second call finds an existing body after the first failed sync.
            with pytest.raises(OSError, match="directory sync failure"):
                retain(tmp_path, receipt, raw)
            assert path.read_bytes() == raw
            assert path.with_suffix(".json").read_bytes() == previous
            assert "verified_stdout" not in receipt
    assert len(failed) == 2 and list(body_dir.glob("*.stdout"))
    synced = []

    def synchronize(directory):
        real_sync(directory)
        synced.append(directory)

    def publish(record, blob):
        assert synced[:4] == directories
        assert path.parent in synced
        real_write(record, blob)

    with monkeypatch.context() as m:
        m.setattr(output, "sync_directory", synchronize)
        m.setattr(output, "atomic_write", publish)
        retain(tmp_path, receipt, raw)
    assert json.loads(path.with_suffix(".json").read_bytes()) == receipt
    assert path.read_bytes() == raw


def test_existing_body_file_sync_failure_preserves_independent_output(tmp_path, monkeypatch):
    raw = b"body"
    _, first = completed(tmp_path, "first", raw)
    body = Path(retain(tmp_path, first, raw)["body"])
    path, receipt = completed(tmp_path, "second", raw)
    previous = path.with_suffix(".json").read_bytes()
    inode, real_sync = path.stat().st_ino, output.os.fsync

    def unavailable(fd):
        info = os.fstat(fd)
        if stat.S_ISREG(info.st_mode) and info.st_ino == body.stat().st_ino:
            raise OSError("injected body sync failure")
        real_sync(fd)

    with monkeypatch.context() as m:
        m.setattr(output.os, "fsync", unavailable)
        with pytest.raises(OSError, match="body sync failure"):
            retain(tmp_path, receipt, raw)
    assert path.stat().st_ino == inode and path.read_bytes() == raw
    assert path.with_suffix(".json").read_bytes() == previous
    retain(tmp_path, receipt, raw)
    assert path.stat().st_ino == body.stat().st_ino


def test_output_replace_sync_failure_is_retried_before_publishing(tmp_path, monkeypatch):
    raw = b"body"
    _, first = completed(tmp_path, "first", raw)
    body = Path(retain(tmp_path, first, raw)["body"])
    path, receipt = completed(tmp_path, "second", raw)
    previous = path.with_suffix(".json").read_bytes()
    real_sync, real_replace = output.sync_directory, output.os.replace
    replaced = False

    def replace(source, destination):
        nonlocal replaced
        real_replace(source, destination)
        replaced = True

    def unavailable(directory):
        if replaced and directory == path.parent:
            raise OSError("injected replacement sync failure")
        real_sync(directory)

    with monkeypatch.context() as m:
        m.setattr(output.os, "replace", replace)
        m.setattr(output, "sync_directory", unavailable)
        with pytest.raises(OSError, match="replacement sync failure"):
            retain(tmp_path, receipt, raw)
    assert replaced and path.stat().st_ino == body.stat().st_ino
    assert path.read_bytes() == raw and path.with_suffix(".json").read_bytes() == previous
    synced, real_write = [], output.atomic_write

    def synchronize(directory):
        real_sync(directory)
        synced.append(directory)

    def publish(record, blob):
        assert path.parent in synced
        real_write(record, blob)

    with monkeypatch.context() as m:
        m.setattr(output, "sync_directory", synchronize)
        m.setattr(output, "atomic_write", publish)
        retain(tmp_path, receipt, raw)
    assert json.loads(path.with_suffix(".json").read_bytes()) == receipt


@pytest.mark.parametrize("which", ["stdout_escape", "root_parent"])
def test_parent_components_are_rejected_before_any_output_changes(tmp_path, which):
    raw = b"body"
    root = tmp_path / "reports"
    root.mkdir()
    if which == "stdout_escape":
        path = tmp_path / "outside.stdout"
        path.write_bytes(raw)
        receipt = {
            "arguments": ["dump", SNAPSHOT, "/backup.json"],
            "returncode": 0,
            "finished_at": "finished",
            "stdout": str(root / ".." / path.name),
        }
        path.with_suffix(".json").write_bytes(json_bytes(receipt))
    else:
        (root / "child").mkdir()
        root = root / "child" / ".."
        path, receipt = completed(root, "run", raw)
    previous, inode = path.with_suffix(".json").read_bytes(), path.stat().st_ino
    with pytest.raises(ValueError, match="parent component"):
        retain(root, receipt, raw)
    assert path.read_bytes() == raw and path.stat().st_ino == inode
    assert path.with_suffix(".json").read_bytes() == previous
    assert not (root / "manifest-objects-v1").exists()
