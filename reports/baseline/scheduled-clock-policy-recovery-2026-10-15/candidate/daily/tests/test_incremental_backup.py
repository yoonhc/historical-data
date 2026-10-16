import errno
import json
import os
import shutil
from datetime import timedelta
from types import SimpleNamespace

import pytest
from marketdata import backup
from marketdata import maintenance as ops
from marketdata.cli import main
from marketdata.domain import DataError, now
from marketdata.packing import pack_events, prune_loose
from marketdata.store import Store
from test_backup import populate
from test_packing import populate_events


def marker(path):
    return json.loads((path / "backup.json").read_bytes())


def test_linked_snapshot_adds_only_new_data_and_restores_without_previous_path(store, tmp_path):
    key, snapshot = populate(store, tmp_path)
    previous, current = tmp_path / "first", tmp_path / "second"
    first = backup.create_backup(store, previous, min_free_gib=0)
    with store.writer():
        added = store.put_raw(b"new evidence", "test:increment", "test")
    result = backup.create_backup(store, current, min_free_gib=0, reuse_from=previous)
    assert result["storage"]["reused_files"] == first["files"] - 1
    assert result["storage"]["new_files"] == 2  # New raw file and independent catalog.
    assert result["storage"]["new_bytes"] + result["storage"]["reused_bytes"] == result["bytes"]
    assert result["storage"]["reuse_backup_id"] == first["backup_id"]
    for record in marker(previous)["files"]:
        relative = record["path"]
        assert not (store.root / relative).samefile(current / relative)
        assert (previous / relative).samefile(current / relative) == (record["kind"] != "catalog")
    # No parent-chain dependency: each directory has every file required to restore.
    shutil.rmtree(previous)
    assert backup.verify_backup(current)["ok"]
    restored = tmp_path / "restore"
    backup.restore_backup(current, restored, min_free_gib=0)
    restored_store = Store(restored)
    try:
        assert restored_store.raw(added) == store.raw(added)
        assert restored_store.snapshot_manifest(snapshot) == store.snapshot_manifest(snapshot)
        assert restored_store.read(key, "2024-01-01", "2024-01-01T00:01").equals(
            store.read(key, "2024-01-01", "2024-01-01T00:01")
        )
        assert restored_store.validate()["ok"]
    finally:
        restored_store.close()
    for record in marker(current)["files"]:
        assert not (current / record["path"]).samefile(restored / record["path"])


@pytest.mark.parametrize("damaged", ["live", "previous"])
def test_linking_never_hides_corruption_in_live_or_reused_bytes(store, tmp_path, damaged):
    populate(store, tmp_path)
    previous = tmp_path / "first"
    backup.create_backup(store, previous, min_free_gib=0)
    record = next(r for r in marker(previous)["files"] if r["kind"] == "raw")
    path = (store.root if damaged == "live" else previous) / record["path"]
    path.write_bytes(b"x" * path.stat().st_size)
    with pytest.raises(DataError, match="hash mismatch"):
        backup.create_backup(store, tmp_path / "bad", min_free_gib=0, reuse_from=previous)
    assert not (tmp_path / "bad/backup.json").exists()


def test_shared_archive_corruption_is_detected_in_both_checkpoints_but_not_live(store, tmp_path):
    populate(store, tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    backup.create_backup(store, a, min_free_gib=0)
    backup.create_backup(store, b, min_free_gib=0, reuse_from=a)
    raw = next(r for r in marker(a)["files"] if r["kind"] == "raw")
    (a / raw["path"]).write_bytes(b"damaged shared archive")
    for path in (a, b):
        with pytest.raises(DataError):
            backup.verify_backup(path)
    assert store.validate()["ok"]


def test_existing_archive_linked_to_live_input_is_rejected(store, tmp_path):
    populate(store, tmp_path)
    previous = tmp_path / "previous"
    backup.create_backup(store, previous, min_free_gib=0)
    raw = next(r for r in marker(previous)["files"] if r["kind"] == "raw")
    archived = previous / raw["path"]
    archived.unlink()
    os.link(store.root / raw["path"], archived)
    with pytest.raises(DataError, match="live store"):
        backup.create_backup(store, tmp_path / "bad", min_free_gib=0, reuse_from=previous)
    assert not (tmp_path / "bad/backup.json").exists()


def test_link_failure_retains_incomplete_attempt_and_does_not_fallback_to_full_copy(
    store, tmp_path, monkeypatch
):
    populate(store, tmp_path)
    previous = tmp_path / "previous"
    backup.create_backup(store, previous, min_free_gib=0)
    original = os.link
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EXDEV, "simulated unsupported link")
        return original(*args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(backup.os, "link", interrupted)
        with pytest.raises(DataError, match="hard-link"):
            backup.create_backup(store, tmp_path / "partial", min_free_gib=0, reuse_from=previous)
    assert not (tmp_path / "partial/backup.json").exists()
    assert any((tmp_path / "partial/data").rglob("*"))
    assert backup.verify_backup(previous)["ok"]
    assert backup.create_backup(store, tmp_path / "retry", min_free_gib=0, reuse_from=previous)[
        "ok"
    ]


def test_capacity_reserves_only_new_file_bytes_for_incremental_backup(store, tmp_path, monkeypatch):
    with store.writer():
        store.put_raw(os.urandom(1024 * 1024), "test:large", "test")
    previous = tmp_path / "first"
    backup.create_backup(store, previous, min_free_gib=0)
    free = (store.root / backup.CATALOG).stat().st_size + 16384
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: SimpleNamespace(free=free))
    with pytest.raises(DataError, match="Insufficient"):
        backup.create_backup(store, tmp_path / "full", min_free_gib=0)
    result = backup.create_backup(store, tmp_path / "linked", min_free_gib=0, reuse_from=previous)
    assert result["storage"]["new_files"] == 1
    assert result["storage"]["reused_bytes"] > free


def test_packed_snapshot_links_packs_and_preserves_old_format_compatibility(store, tmp_path):
    populate_events(store)
    pack_events(store, apply=True, before=now() + timedelta(seconds=1), min_free_gib=0)
    prune_loose(store, apply=True)
    first, second = tmp_path / "first", tmp_path / "second"
    backup.create_backup(store, first, min_free_gib=0)
    # A genuine legacy manifest without optional storage accounting remains usable.
    from marketdata.domain import json_bytes, sha256

    old = marker(first)
    old.pop("storage")
    old.pop("backup_id")
    old["backup_id"] = sha256(json_bytes(old))
    (first / "backup.json").write_bytes(json_bytes(old))
    result = backup.create_backup(store, second, min_free_gib=0, reuse_from=first)
    assert marker(second)["format_version"] == 2
    assert result["storage"]["new_files"] == 1
    restored = tmp_path / "restored"
    backup.restore_backup(second, restored, min_free_gib=0)
    other = Store(restored)
    try:
        assert other.validate()["ok"]
        for r in store.db.execute("SELECT id FROM artifacts"):
            assert other.raw(r[0]) == store.raw(r[0])
    finally:
        other.close()


def test_storage_reports_unique_backups_without_counting_shared_inodes_twice(store, tmp_path):
    populate(store, tmp_path)
    first, second = store.root / "backups/first", store.root / "backups/second"
    backup.create_backup(store, first, min_free_gib=0)
    backup.create_backup(store, second, min_free_gib=0, reuse_from=first)
    sizes = backup.storage_report(store.root, min_free_gib=0)["directories"]["backups"]
    assert sizes["hardlink_duplicate_paths"] == len(marker(first)["files"]) - 1
    assert sizes["allocated_bytes"] > sizes["unique_allocated_bytes"]


def test_daily_external_destination_incremental_success_retry_and_unavailable_status(
    store, tmp_path, monkeypatch
):
    populate(store, tmp_path)
    directory = tmp_path / "external"
    directory.mkdir()
    moment = now()
    first = ops.run(store, mode="daily", backup_dir=directory, incremental=True, min_free_gib=0)
    assert first["backup"]["storage"]["mode"] == "independent_copy"
    with store.writer():
        store.put_raw(b"tomorrow", "test:tomorrow", "test")
    monkeypatch.setattr(ops, "now", lambda: moment + timedelta(days=1))
    second = ops.run(store, mode="daily", backup_dir=directory, incremental=True, min_free_gib=0)
    assert second["backup"]["storage"]["reuse_backup_id"] == first["backup"]["backup_id"]
    assert second["backup"]["storage"]["new_files"] == 2
    retry = ops.run(store, mode="daily", backup_dir=directory, incremental=True, min_free_gib=0)
    assert retry["backup"]["reused"]
    assert retry["backup"]["backup_id"] == second["backup"]["backup_id"]
    assert not (store.root / "backups/automatic").exists()
    ops.run(store, min_free_gib=0)
    assert ops.status(store, min_free_gib=0)["healthy"]
    directory.rename(tmp_path / "disconnected")
    status = ops.status(store, min_free_gib=0)
    assert not status["healthy"] and not status["backup_capacity"]["healthy"]


@pytest.mark.parametrize("issue", ["missing_directory", "missing_mount", "data_directory"])
def test_invalid_backup_destination_never_falls_back_to_local(store, tmp_path, issue):
    directory = tmp_path / "external"
    kwargs = {"backup_dir": directory}
    if issue == "missing_mount":
        directory.mkdir()
        kwargs["backup_mount"] = directory
    elif issue == "data_directory":
        directory = store.root / "data/backup"
        directory.mkdir(parents=True)
        kwargs["backup_dir"] = directory
    with pytest.raises(DataError):
        ops.run(store, mode="daily", min_free_gib=0, incremental=True, **kwargs)
    assert not (store.root / "backups").exists()
    assert not list(directory.glob("*/*/backup.json"))
    report = json.loads((store.root / "state/maintenance-daily-latest.json").read_text())
    assert report["state"] == "failed" and report["phase"] == "backup_configuration"


def test_backup_folder_cannot_reuse_another_stores_same_day_snapshot(store, tmp_path):
    directory = tmp_path / "shared"
    directory.mkdir()
    other = Store(tmp_path / "other")
    try:
        ops.run(other, mode="daily", min_free_gib=0, backup_dir=directory)
    finally:
        other.close()
    with pytest.raises(DataError, match="another source"):
        ops.run(store, mode="daily", min_free_gib=0, backup_dir=directory, incremental=True)


def test_cli_linked_backup_and_archived_root_guard(store, tmp_path, capsys):
    populate(store, tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    backup.create_backup(store, first, min_free_gib=0)
    assert (
        main(
            [
                "--root",
                str(store.root),
                "backup",
                "--output",
                str(second),
                "--reuse-from",
                str(first),
                "--min-free-gib",
                "0",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["storage"]["reused_files"] > 0
    before = (second / backup.CATALOG).read_bytes()
    assert main(["--root", str(second), "backup", "--output", str(tmp_path / "bad")]) == 1
    assert (second / backup.CATALOG).read_bytes() == before
