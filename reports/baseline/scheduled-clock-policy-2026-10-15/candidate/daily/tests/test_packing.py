import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from marketdata import aggtrades as agg
from marketdata import backup, packing
from marketdata.cli import main
from marketdata.domain import DataError, WriterBusy, now
from marketdata.fileio import layout_lock
from marketdata.store import Store
from test_aggtrades import BASE, insert, trade
from test_depth import capture, snapshot, update


def populate_events(store):
    reports = []
    for identity in range(4):
        report, _, _ = insert(store, [trade(identity)])
        report = agg.save_collection(store, {**report, "complete": True}, "complete")
        reports.append(report)
    return reports


def apply_pack(store, **kwargs):
    return packing.pack_events(
        store, apply=True, before=now() + timedelta(seconds=1), min_free_gib=0, **kwargs
    )


def test_byte_preserving_pack_prune_idempotency_and_backup_roundtrip(store, tmp_path):
    populate_events(store)
    before = agg.read_trades(store, BASE, BASE + timedelta(seconds=1))
    raw = {r["id"]: store.raw(r["id"]) for r in store.db.execute("SELECT * FROM artifacts")}
    refs = [dict(r) for r in store.db.execute("SELECT * FROM event_segments ORDER BY id")]
    plan = packing.pack_events(store, before=now() + timedelta(seconds=1))
    assert not plan["applied"] and plan["files"] == 8
    assert not (store.root / "data/packs").exists()
    result = apply_pack(store, max_members=3)
    assert len(result["packs"]) == 3 and result["loose_files_removed"] == 0
    assert packing.verify_packs(store) == {"ok": True, "packs": 3, "members": 8}
    prune = packing.prune_loose(store)
    assert len(prune["files"]) == 8 and prune["removed"] == 0
    pruned = packing.prune_loose(store, apply=True)
    assert pruned["removed"] == 8 and pruned["logical_files_deleted"] == 0
    assert all(not (store.root / r["path"]).exists() for r in prune["files"])
    assert all(store.raw(identity) == expected for identity, expected in raw.items())
    assert agg.read_trades(store, BASE, BASE + timedelta(seconds=1)) == before
    assert [dict(r) for r in store.db.execute("SELECT * FROM event_segments ORDER BY id")] == refs
    assert packing.prune_loose(store, apply=True)["removed"] == 0
    assert apply_pack(store)["packs"] == []
    assert store.validate()["ok"]
    archived, restored = tmp_path / "backup", tmp_path / "restored"
    result = backup.create_backup(store, archived, min_free_gib=0)
    assert result["files"] == 4  # Three packs and one catalog; logical files remain eight.
    assert json.loads((archived / "backup.json").read_bytes())["format_version"] == 2
    assert backup.verify_backup(archived)["ok"]
    backup.restore_backup(archived, restored, min_free_gib=0)
    with_store = Store(restored)
    try:
        assert agg.read_trades(with_store, BASE, BASE + timedelta(seconds=1)) == before
        assert all(with_store.raw(identity) == expected for identity, expected in raw.items())
        assert with_store.validate()["ok"]
    finally:
        with_store.close()


def test_depth_replay_and_raw_evidence_unchanged_after_packing(store, monkeypatch):
    report = capture(store, monkeypatch, [[update(100), update(101)]], [snapshot()])
    from marketdata.depth import replay

    before = replay(store, report["id"])
    assert before["valid"]
    apply_pack(store)
    packing.prune_loose(store, apply=True)
    assert replay(store, report["id"]) == before
    assert store.validate()["ok"]


def test_running_recent_and_unknown_collections_are_not_packed(store):
    running, _, _ = insert(store, [trade(10)])
    closed = populate_events(store)[0]
    assert packing.pack_events(store)["files"] == 0  # Default one-hour settling period.
    assert apply_pack(store, collection_ids=[running["id"]])["files"] == 0
    assert apply_pack(store, collection_ids=[closed["id"]])["files"] == 2
    with pytest.raises(DataError, match="Unknown"):
        apply_pack(store, collection_ids=["absent"])


@pytest.mark.parametrize("damage", ["input", "pack", "duplicate", "mapping"])
def test_corruption_never_authorizes_loose_removal(store, damage):
    populate_events(store)
    if damage == "input":
        path = (
            store.root / store.db.execute("SELECT path FROM event_segments LIMIT 1").fetchone()[0]
        )
        path.write_bytes(b"corrupted")
        with pytest.raises(DataError, match="input hash"):
            apply_pack(store)
        assert not store.db.execute("SELECT 1 FROM file_packs").fetchone()
        assert path.exists()
        return
    result = apply_pack(store)
    all_paths = [store.root / r[0] for r in store.db.execute("SELECT path FROM packed_files")]
    if damage == "pack":
        (store.root / result["packs"][0]["path"]).write_bytes(b"corrupted")
    elif damage == "duplicate":
        all_paths[0].write_bytes(b"corrupted")
    else:
        with store.db:
            store.db.execute(
                "UPDATE packed_files SET sha256=? WHERE path=?",
                ("0" * 64, str(all_paths[0].relative_to(store.root))),
            )
    with pytest.raises(DataError):
        packing.prune_loose(store, apply=True)
    assert all(p.exists() for p in all_paths)
    if damage != "duplicate":
        assert not store.validate()["ok"]


def test_interrupted_prune_is_resumable_without_data_loss(store, monkeypatch):
    populate_events(store)
    before = agg.read_trades(store, BASE, BASE + timedelta(seconds=1))
    apply_pack(store)
    original = Path.unlink
    removed = 0

    def interrupt(path, *args, **kwargs):
        nonlocal removed
        if removed == 1:
            raise OSError("interrupted unlink")
        original(path, *args, **kwargs)
        removed += 1

    with monkeypatch.context() as m:
        m.setattr(Path, "unlink", interrupt)
        with pytest.raises(OSError, match="interrupted"):
            packing.prune_loose(store, apply=True)
    assert agg.read_trades(store, BASE, BASE + timedelta(seconds=1)) == before
    assert packing.prune_loose(store, apply=True)["removed"] == 7
    assert store.validate()["ok"]


def test_failed_publication_retains_loose_inputs_and_reuses_verified_orphan(store):
    populate_events(store)
    with layout_lock(store.root):
        with pytest.raises(WriterBusy, match="layout"):
            apply_pack(store)
    assert not store.db.execute("SELECT 1 FROM packed_files").fetchone()
    assert store.validate()["ok"]
    files = list((store.root / "data/packs").rglob("*.zip"))
    assert len(files) == 1
    result = apply_pack(store)
    assert result["packs"][0]["path"] == str(files[0].relative_to(store.root))


def test_reader_pin_blocks_prune_but_not_normal_writer(store):
    populate_events(store)
    apply_pack(store)
    with layout_lock(store.root):
        with pytest.raises(WriterBusy, match="layout"):
            packing.prune_loose(store, apply=True)
        with store.writer():
            store.put_raw(b"new observation", "test:new", "test")
    assert packing.prune_loose(store, apply=True)["removed"] == 8


def test_reader_with_older_catalog_snapshot_resolves_new_physical_layout(store):
    populate_events(store)
    reader = Store(store.root)
    try:
        reader.db.execute("BEGIN")
        record = reader.db.execute("SELECT * FROM event_segments LIMIT 1").fetchone()
        expected = reader._revision_table(record)
        apply_pack(store)
        packing.prune_loose(store, apply=True)
        assert reader.db.execute("SELECT count(*) FROM packed_files").fetchone()[0] == 0
        assert reader._revision_table(record).equals(expected)
    finally:
        reader.close()


def test_backup_pins_loose_layout_while_collectors_can_write(store, tmp_path, monkeypatch):
    populate_events(store)
    original = backup.copy_verified
    checked = False

    def copy(*args):
        nonlocal checked
        if not checked:
            checked = True
            with pytest.raises(WriterBusy):
                apply_pack(store)
            with store.writer():
                store.put_raw(b"new observation", "test:new", "test")
        return original(*args)

    monkeypatch.setattr(backup, "copy_verified", copy)
    archived = tmp_path / "backup"
    backup.create_backup(store, archived, min_free_gib=0)
    assert checked and backup.verify_backup(archived)["ok"]
    assert not store.db.execute("SELECT 1 FROM packed_files").fetchone()


def test_repeated_publication_does_not_recreate_packed_loose_files(store):
    reports = populate_events(store)
    source = store.db.execute("SELECT * FROM artifacts ORDER BY path LIMIT 1").fetchone()
    blob = store.raw(source["id"])[0]
    apply_pack(store)
    packing.prune_loose(store, apply=True)
    assert store.put_raw(blob, "test:again", "test") == source["id"]
    row = store.db.execute(
        "SELECT * FROM event_segments WHERE source_id=?", (source["id"],)
    ).fetchone()
    rows = store._revision_table(row).to_pylist()
    agg.publish_segment(store, row["collection_id"], row["kind"], rows, source["id"])
    assert not (store.root / source["path"]).exists()
    assert not (store.root / row["path"]).exists()
    assert reports and store.validate()["ok"]


def test_low_space_leaves_inputs_and_catalog_untouched(store, monkeypatch):
    populate_events(store)
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    with pytest.raises(DataError, match="Insufficient"):
        apply_pack(store)
    assert not store.db.execute("SELECT 1 FROM packed_files").fetchone()
    assert store.validate()["ok"]


def test_pack_durability_failure_never_publishes_or_removes_inputs(store, monkeypatch):
    populate_events(store)

    def fail(path):
        raise OSError("fsync failed")

    monkeypatch.setattr(packing, "sync_parent", fail)
    with pytest.raises(OSError, match="fsync failed"):
        apply_pack(store)
    assert not store.db.execute("SELECT 1 FROM file_packs").fetchone()
    assert store.validate()["ok"]


def test_cli_defaults_to_plan_and_refuses_archived_backup_before_open(store, tmp_path, capsys):
    populate_events(store)
    args = [
        "--root",
        str(store.root),
        "pack-events",
        "--before",
        (now() + timedelta(seconds=1)).isoformat(),
    ]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is False
    assert main(args + ["--apply", "--min-free-gib", "0"]) == 0
    assert main(["--root", str(store.root), "pack-verify"]) == 0
    assert main(["--root", str(store.root), "pack-prune"]) == 0
    assert main(["--root", str(store.root), "pack-prune", "--apply"]) == 0
    archived = tmp_path / "backup"
    backup.create_backup(store, archived, min_free_gib=0)
    before = (archived / "state/catalog.sqlite").read_bytes()
    assert main(["--root", str(archived), "pack-events"]) == 1
    assert (archived / "state/catalog.sqlite").read_bytes() == before


def test_backup_verifies_pack_member_index_not_just_zip_hash(store, tmp_path):
    populate_events(store)
    apply_pack(store)
    archived = tmp_path / "backup"
    with store.db:
        store.db.execute("UPDATE packed_files SET bytes=bytes+1")
    with pytest.raises(DataError, match="size mismatch"):
        backup.create_backup(store, archived, min_free_gib=0)
    assert not (archived / "backup.json").exists()
