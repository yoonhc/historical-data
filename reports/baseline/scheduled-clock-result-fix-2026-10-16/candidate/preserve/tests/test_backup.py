import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from conftest import kline, source_file
from marketdata import backup
from marketdata.cli import main
from marketdata.domain import DataError, Key, json_bytes, sha256
from marketdata.pipeline import Collector
from marketdata.store import Store


def populate(store, tmp_path):
    key = Key("trade_bars")
    collector = Collector(store)
    try:
        path = source_file(tmp_path, [kline()])
        collector.import_archive(path, key, "2024-01-01", "2024-01-01T00:01")
        snapshot = store.create_snapshot([key], "2024-01-01", "2024-01-01T00:01")
        source_file(tmp_path, [kline(price="102")])
        collector.import_archive(path, key, "2024-01-01", "2024-01-01T00:01", accept_revisions=True)
        raw = store.put_raw(b"quarantined evidence", "test:bad", "test")
        store.issue(key, "2024-01-02", raw, "test_quarantine", {"why": "preserve"})
        store.record_fetch(raw, "test:repeat", "2024-01-03", {"observation": 2})
    finally:
        collector.fetcher.close()
    return key, snapshot


def test_checkpoint_preserves_revisions_snapshots_issues_and_receipts(store, tmp_path, monkeypatch):
    key, snapshot = populate(store, tmp_path)
    initial_artifacts = store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0]
    initial_fetches = store.db.execute("SELECT count(*) FROM fetches").fetchone()[0]
    original_copy = backup.copy_verified
    mutated = False

    def copy_with_live_writer(*args):
        nonlocal mutated
        if not mutated:
            mutated = True
            with store.writer():  # Slow copying must not block collection.
                store.put_raw(b"arrived after checkpoint", "test:new", "test")
        return original_copy(*args)

    monkeypatch.setattr(backup, "copy_verified", copy_with_live_writer)
    archived = tmp_path / "backup"
    result = backup.create_backup(store, archived, min_free_gib=0)
    assert result["catalog_counts"]["revisions"] == 2
    assert result["catalog_counts"]["artifacts"] == initial_artifacts
    assert store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == initial_artifacts + 1
    assert backup.verify_backup(archived)["backup_id"] == result["backup_id"]
    restored = tmp_path / "restored"
    receipt = backup.restore_backup(archived, restored, min_free_gib=0)
    assert receipt["catalog_counts"] == result["catalog_counts"]
    instance = Store(restored)
    try:
        assert instance.read(key, "2024-01-01", "2024-01-01T00:01")["open"][0].as_py() == Decimal(
            "102"
        )
        assert instance.read(key, "2024-01-01", "2024-01-01T00:01", snapshot)["open"][
            0
        ].as_py() == Decimal("100")
        assert instance.db.execute("SELECT count(*) FROM issues").fetchone()[0] == 1
        assert instance.db.execute("SELECT count(*) FROM fetches").fetchone()[0] == initial_fetches
        assert instance.validate()["ok"]
    finally:
        instance.close()
    relative = json.loads((archived / "backup.json").read_bytes())["files"][0]["path"]
    assert (archived / relative).stat().st_ino != (restored / relative).stat().st_ino
    assert (store.root / relative).stat().st_ino != (archived / relative).stat().st_ino


@pytest.mark.parametrize("kind", ["raw", "canonical", "snapshots", "catalog", "manifest"])
def test_corrupted_backup_is_rejected(store, tmp_path, kind):
    populate(store, tmp_path)
    archived = tmp_path / "backup"
    backup.create_backup(store, archived, min_free_gib=0)
    manifest = json.loads((archived / "backup.json").read_bytes())
    if kind == "manifest":
        manifest["source_root"] = "tampered"
        (archived / "backup.json").write_bytes(json_bytes(manifest))
    else:
        record = next(item for item in manifest["files"] if item["kind"] == kind)
        (archived / record["path"]).write_bytes(b"corrupted")
    with pytest.raises(DataError, match="mismatch|truncated"):
        backup.verify_backup(archived)
    with pytest.raises(DataError, match="mismatch|truncated"):
        backup.restore_backup(archived, tmp_path / "restored", min_free_gib=0)
    assert not (tmp_path / "restored/restore.json").exists()


def test_incomplete_copy_never_has_completion_marker(store, tmp_path, monkeypatch):
    populate(store, tmp_path)

    def interrupted(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(backup, "copy_verified", interrupted)
    archived = tmp_path / "backup"
    with pytest.raises(OSError, match="disk failure"):
        backup.create_backup(store, archived, min_free_gib=0)
    assert not (archived / "backup.json").exists()
    with pytest.raises(DataError, match="incomplete"):
        backup.verify_backup(archived)


def test_damaged_source_is_not_certified_as_backup(store, tmp_path):
    populate(store, tmp_path)
    record = store.db.execute("SELECT path FROM artifacts LIMIT 1").fetchone()
    (store.root / record["path"]).write_bytes(b"broken source")
    with pytest.raises(DataError, match="Hash mismatch"):
        backup.create_backup(store, tmp_path / "backup", min_free_gib=0)
    assert not (tmp_path / "backup/backup.json").exists()


def test_insufficient_capacity_and_existing_destination_are_safe(store, tmp_path, monkeypatch):
    populate(store, tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep").write_text("untouched")
    with pytest.raises(DataError, match="already exists"):
        backup.create_backup(store, existing, min_free_gib=0)
    assert (existing / "keep").read_text() == "untouched"
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    with pytest.raises(DataError, match="Insufficient"):
        backup.create_backup(store, tmp_path / "no-space", min_free_gib=0)
    assert not (tmp_path / "no-space/backup.json").exists()


@pytest.mark.parametrize("destination", ["data/backup", "state/backup"])
def test_backup_cannot_be_nested_in_store_data(store, destination):
    with pytest.raises(DataError, match="outside"):
        backup.create_backup(store, store.root / destination, min_free_gib=0)


def test_catalog_path_traversal_and_symlink_rejected(store, tmp_path):
    source_id = store.put_raw(b"evidence", "test", "test")
    original = store.db.execute("SELECT path FROM artifacts").fetchone()[0]
    with store.db:
        store.db.execute("UPDATE artifacts SET path=?", ("data/raw/../../../outside",))
    with pytest.raises(DataError, match="Unsafe"):
        backup.create_backup(store, tmp_path / "traversal", min_free_gib=0)
    with store.db:
        store.db.execute("UPDATE artifacts SET path=?", (original,))
    (store.root / original).unlink()
    (store.root / original).symlink_to(tmp_path / "outside")
    (tmp_path / "outside").write_bytes(b"evidence")
    with pytest.raises(DataError, match="Symlink"):
        backup.create_backup(store, tmp_path / "symlink", min_free_gib=0)
    assert source_id == sha256((tmp_path / "outside").read_bytes())


def test_inventory_cannot_omit_catalog_file_even_with_rehashed_manifest(store, tmp_path):
    populate(store, tmp_path)
    archived = tmp_path / "backup"
    backup.create_backup(store, archived, min_free_gib=0)
    manifest = json.loads((archived / "backup.json").read_bytes())
    manifest["files"] = [item for item in manifest["files"] if item["kind"] != "raw"]
    manifest.pop("backup_id")
    manifest["backup_id"] = sha256(json_bytes(manifest))
    (archived / "backup.json").write_bytes(json_bytes(manifest))
    with pytest.raises(DataError, match="inventory"):
        backup.verify_backup(archived)


def test_restore_never_overwrites_existing_store_or_follows_destination_symlink(store, tmp_path):
    populate(store, tmp_path)
    archived = tmp_path / "backup"
    backup.create_backup(store, archived, min_free_gib=0)
    for destination in (store.root, archived):
        with pytest.raises(DataError, match="already exists|outside"):
            backup.restore_backup(archived, destination, min_free_gib=0)
    link = tmp_path / "restore-link"
    link.symlink_to(tmp_path / "absent", target_is_directory=True)
    with pytest.raises(DataError, match="already exists"):
        backup.restore_backup(archived, link, min_free_gib=0)
    assert not (tmp_path / "absent").exists()
    assert store.validate()["ok"]
    assert backup.verify_backup(archived)["ok"]


def test_storage_capacity_warning_does_not_create_catalog(tmp_path, monkeypatch, capsys):
    (tmp_path / "data/raw").mkdir(parents=True)
    (tmp_path / "data/raw/example").write_bytes(b"123")
    monkeypatch.setattr(
        backup.shutil, "disk_usage", lambda p: SimpleNamespace(total=100, used=99, free=1)
    )
    assert main(["--root", str(tmp_path), "storage", "--min-free-gib", "1"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["warnings"] == ["low_free_space"]
    assert result["directories"]["data/raw"]["bytes"] == 3
    assert not (tmp_path / "state").exists()


def test_cli_backup_restore_and_verify_do_not_initialize_unused_root(store, tmp_path, capsys):
    populate(store, tmp_path)
    archived, restored = tmp_path / "backup", tmp_path / "restored"
    assert (
        main(
            ["--root", str(store.root), "backup", "--output", str(archived), "--min-free-gib", "0"]
        )
        == 0
    )
    unused = tmp_path / "unused"
    assert main(["--root", str(unused), "backup-verify", str(archived)]) == 0
    assert (
        main(
            [
                "--root",
                str(unused),
                "restore",
                str(archived),
                "--output",
                str(restored),
                "--min-free-gib",
                "0",
            ]
        )
        == 0
    )
    assert not unused.exists()
    assert main(["--root", str(unused), "backup", "--output", str(tmp_path / "bad")]) == 1
    assert not unused.exists()
    assert not (tmp_path / "bad").exists()
