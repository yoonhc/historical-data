import json
from datetime import timedelta

import pytest
from marketdata import maintenance as ops
from marketdata.cli import main
from marketdata.domain import DataError, WriterBusy, now
from marketdata.packing import pack_events
from marketdata.store import Store
from test_packing import populate_events


def closed_events(store):
    populate_events(store)
    with store.db:
        store.db.execute(
            "UPDATE event_collections SET updated_at=?", ((now() - timedelta(hours=2)).isoformat(),)
        )


def test_hourly_and_daily_preserve_data_with_idempotent_backup(store):
    closed_events(store)
    expected = {r["id"]: store.raw(r["id"]) for r in store.db.execute("SELECT * FROM artifacts")}
    hourly = ops.run(store, min_free_gib=0)
    assert hourly["ok"] and hourly["packing"]["files"] == 8
    assert hourly["new_prune"]["removed"] == 8
    assert hourly["logical_files_deleted"] == 0
    assert not ops.status(store, min_free_gib=0)["healthy"]  # No daily run yet.
    daily = ops.run(store, mode="daily", min_free_gib=0)
    assert daily["ok"] and daily["validation"]["ok"] and not daily["backup"]["reused"]
    again = ops.run(store, mode="daily", min_free_gib=0)
    assert again["backup"]["reused"]
    assert daily["backup"]["backup_id"] == again["backup"]["backup_id"]
    assert len(list((store.root / "backups/automatic").glob("*/*/backup.json"))) == 1
    assert all(store.raw(identity) == blob for identity, blob in expected.items())
    assert ops.status(store, min_free_gib=0)["healthy"]


def test_interrupted_publication_is_pruned_on_next_run(store):
    closed_events(store)
    result = pack_events(store, apply=True, min_free_gib=0)
    assert len(ops.pending_prune(store)) == len(result["packs"])
    report = ops.run(store, min_free_gib=0)
    assert report["pending_prune"]["removed"] == 8
    assert report["packing"]["files"] == 0
    assert ops.pending_prune(store) == []
    assert store.validate()["ok"]


def test_empty_pack_selection_does_not_scan_verify_every_historical_pack(store, monkeypatch):
    closed_events(store)
    ops.run(store, min_free_gib=0)

    def unexpected(*args, **kwargs):
        pytest.fail("No pending/new packs must not become an empty 'all packs' filter")

    monkeypatch.setattr(ops, "prune_loose", unexpected)
    assert ops.run(store, min_free_gib=0)["ok"]


def test_jobs_serialize_without_overwriting_running_report(store):
    path = store.root / "state/maintenance-hourly-latest.json"
    path.write_bytes(b"prior report")
    with ops.maintenance_lock(store), pytest.raises(WriterBusy, match="maintenance"):
        ops.run(store, min_free_gib=0)
    assert path.read_bytes() == b"prior report"


def test_failed_daily_validation_is_durable_and_prevents_backup(store):
    closed_events(store)
    # A non-event source is not packed, so the failure is specifically in full validation.
    with store.writer():
        identity = store.put_raw(b"source", "test:source", "test")
    path = store.db.execute("SELECT path FROM artifacts WHERE id=?", (identity,)).fetchone()[0]
    (store.root / path).write_bytes(b"damaged")
    with pytest.raises(DataError, match="validation failed"):
        ops.run(store, mode="daily", min_free_gib=0)
    report = json.loads((store.root / "state/maintenance-daily-latest.json").read_bytes())
    assert report["state"] == "failed" and report["phase"] == "validate"
    assert not report["validation"]["ok"]
    assert not (store.root / "backups/automatic").exists()
    assert not ops.status(store, min_free_gib=0)["healthy"]


def test_failed_backup_attempt_is_retained_and_next_attempt_succeeds(store, monkeypatch):
    create = ops.create_backup

    def fail(store, destination, **kwargs):
        destination.mkdir(parents=True)
        (destination / "partial").write_bytes(b"retained")
        raise OSError("copy interrupted")

    with monkeypatch.context() as m:
        m.setattr(ops, "create_backup", fail)
        with pytest.raises(OSError, match="interrupted"):
            ops.run(store, mode="daily", min_free_gib=0)
    assert ops.create_backup is create
    assert ops.run(store, mode="daily", min_free_gib=0)["ok"]
    assert len(list((store.root / "backups/automatic").glob("*/*/partial"))) == 1
    assert len(list((store.root / "backups/automatic").glob("*/*/backup.json"))) == 1


def test_low_space_stops_without_deleting_logical_data(store):
    closed_events(store)
    with pytest.raises(DataError, match="capacity"):
        ops.run(store, min_free_gib=1_000_000)
    assert store.validate()["ok"]
    assert store.db.execute("SELECT count(*) FROM event_segments").fetchone()[0] == 4
    assert not store.db.execute("SELECT count(*) FROM file_packs").fetchone()[0]
    assert not ops.status(store, min_free_gib=1_000_000)["healthy"]


def test_status_rejects_stale_and_interrupted_reports(store, monkeypatch):
    ops.run(store, min_free_gib=0)
    ops.run(store, mode="daily", min_free_gib=0)
    current = now()
    monkeypatch.setattr(ops, "now", lambda: current + timedelta(hours=4))
    status = ops.status(store, min_free_gib=0)
    assert not status["healthy"] and not status["modes"]["hourly"]["healthy"]
    assert status["modes"]["daily"]["healthy"]
    monkeypatch.setattr(ops, "now", lambda: current + timedelta(hours=37))
    assert not ops.status(store, min_free_gib=0)["modes"]["daily"]["healthy"]


def test_maintenance_cli_requires_existing_non_archived_store(tmp_path):
    root = tmp_path / "missing"
    assert main(["--root", str(root), "maintenance"]) == 1
    assert not root.exists()
    store = Store(root)
    store.close()
    (root / "backup.json").write_text("{}")
    assert main(["--root", str(root), "maintenance"]) == 1
