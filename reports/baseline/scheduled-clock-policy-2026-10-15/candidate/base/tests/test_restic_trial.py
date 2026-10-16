import json
import os
import runpy
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from marketdata import backup, maintenance
from marketdata.domain import DataError, json_bytes
from marketdata.store import Store
from test_backup import populate
from test_depth import capture, snapshot, update
from test_packing import apply_pack, populate_events

TRIAL = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/restic_trial.py"))
Profile, Trial = TRIAL["Profile"], TRIAL["Trial"]
BINARY = Path(__file__).resolve().parents[1] / ".cache/restic/0.19.1/restic"


def profile_at(tmp_path, store_root):
    mount = tmp_path / "disk"
    return Profile(
        store_root=store_root,
        backup_dir=mount / "archives",
        backup_mount=mount,
        restic_binary=BINARY,
        restic_repository=mount / "repo",
        restic_password_file=tmp_path / "password",
        work_root=mount / "work",
        reports_root=tmp_path / "reports",
        min_free_gib=1,
        restic_threads=2,
    )


def write_profile(path, profile):
    path.write_text(
        "\n".join(
            f"{k} = {json.dumps(str(v) if isinstance(v, Path) else v)}"
            for k, v in vars(profile).items()
        )
        + "\n"
    )
    return path


@pytest.mark.parametrize("unsafe", ["overlap", "password", "relative", "symlink"])
def test_profile_rejects_unsafe_paths_before_writing(tmp_path, unsafe):
    profile = profile_at(tmp_path, tmp_path / "store")
    if unsafe == "overlap":
        profile = replace(profile, restic_repository=profile.backup_dir / "repo")
    elif unsafe == "password":
        profile = replace(profile, restic_password_file=profile.work_root / "password")
    elif unsafe == "relative":
        profile = replace(profile, backup_mount=Path("relative"))
    else:
        link = tmp_path / "link"
        link.symlink_to(tmp_path / "target")
        profile = replace(profile, work_root=link / "work")
    with pytest.raises(ValueError):
        Profile.load(write_profile(tmp_path / "host.toml", profile))
    assert not profile.restic_repository.exists()


def test_missing_mount_fails_before_repo_or_cache_creation(tmp_path):
    profile = profile_at(tmp_path, tmp_path / "store")
    profile.backup_mount.mkdir()
    with pytest.raises(ValueError, match="mount is missing"):
        profile.check()
    assert not profile.restic_repository.exists() and not profile.work_root.exists()


def test_physical_accounting_counts_shared_files_once(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "shared").write_bytes(b"data" * 4096)
    os.link(a / "shared", b / "shared")
    (b / "new").write_bytes(b"new")
    union = TRIAL["disk_usage"]([a, b])
    objects = [a, b, a / "shared", b / "new"]
    assert union["allocated_bytes"] == sum(p.stat().st_blocks * 512 for p in objects)
    assert union["file_paths"] == 3 and union["unique_inodes"] == 4
    assert union["apparent_file_bytes"] == 2 * (a / "shared").stat().st_size + 3


@pytest.mark.parametrize("day", ["2024-01-01", "2024-01-02"])
def test_relocated_store_cannot_reuse_original_automatic_folder(store, tmp_path, day):
    populate(store, tmp_path)
    directory = tmp_path / "automatic"
    directory.mkdir()
    first = maintenance.daily_backup(
        store, "2024-01-01", 0, None, directory=directory, incremental=True
    )
    original = Path(first["path"])
    before = (original / "backup.json").read_bytes()
    relocated = tmp_path / "relocated"
    backup.restore_backup(original, relocated, min_free_gib=0)
    other = Store(relocated)
    try:
        with pytest.raises(DataError, match="another source"):
            maintenance.daily_backup(other, day, 0, None, directory=directory, incremental=True)
        dedicated = tmp_path / "new-automatic"
        dedicated.mkdir()
        result = maintenance.daily_backup(
            other, day, 0, None, directory=dedicated, incremental=True
        )
        manifest = json.loads((Path(result["path"]) / "backup.json").read_bytes())
        assert manifest["source_root"] == str(relocated)
        assert manifest["backup_id"] != first["backup_id"]
        assert result["storage"]["mode"] == "independent_copy"
        assert backup.verify_backup(Path(result["path"]))["ok"]
    finally:
        other.close()
    assert (original / "backup.json").read_bytes() == before
    assert len(list(directory.glob("*/*/backup.json"))) == 1


def test_live_monitor_reads_wal_without_mutating_store(store):
    report = {"id": "test", "state": "running", "last_received_at": "2024-01-01T00:00:00Z"}
    with store.db:
        store.db.execute(
            "INSERT INTO event_collections VALUES(?,?,?,?,?,?,?)",
            ("test", "book_depth", "websocket", "2024", "2024", "running", json.dumps(report)),
        )
    assert (store.root / (backup.CATALOG + "-wal")).exists()
    assert TRIAL["sample_streams"](store.root)["depth"]["id"] == "test"


@pytest.mark.skipif(
    not BINARY.is_file(), reason="Pinned restic binary is an opt-in integration tool"
)
def test_real_restic_two_checkpoints_restore_relocate_and_new_backup(store, tmp_path, monkeypatch):
    populate(store, tmp_path)
    populate_events(store)
    with monkeypatch.context() as m:
        capture(store, m, [[update(100), update(101)]], [snapshot()])
    apply_pack(store)
    from marketdata.packing import prune_loose

    prune_loose(store, apply=True)
    profile = profile_at(tmp_path, store.root)
    profile.backup_dir.mkdir(parents=True)
    profile.restic_password_file.write_text("temporary isolated test password\n")
    profile.restic_password_file.chmod(0o600)
    specs, previous = [], None
    for label in ("first", "second"):
        if previous:
            store.put_raw(b"incremental data", "test:increment", "test")
        root = profile.backup_dir / label
        result = backup.create_backup(store, root, min_free_gib=0, reuse_from=previous)
        specs.append({"label": label, "relative_path": label, "backup_id": result["backup_id"]})
        previous = root
    config = tmp_path / "inputs.toml"
    config.write_text(
        "\n".join(
            "[[checkpoints]]\n" + "\n".join(f"{k} = {json.dumps(v)}" for k, v in item.items())
            for item in specs
        )
    )
    profile = Profile.load(write_profile(tmp_path / "host.toml", profile))
    # Only the filesystem mount is simulated; real restic and every marketdata guard run.
    original_is_mount = Path.is_mount
    monkeypatch.setattr(
        Path, "is_mount", lambda p: p == profile.backup_mount or original_is_mount(p)
    )
    trial = Trial(profile, TRIAL["checkpoints"](profile, config))
    trial.prepare()
    trial.back_up(0)
    trial.back_up(1)
    trial.check()
    trial.recover(0)
    trial.recover(1)
    # A newly started live session may not have published report.state yet.
    sample = TRIAL["sample_streams"](store.root)
    sample["depth"]["state"] = None
    (profile.reports_root / "monitor.jsonl").write_bytes(json_bytes(sample) + b"\n")
    summary_path = tmp_path / "summary.json"
    summarizer = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/summarize_restic_trial.py")
    )
    with monkeypatch.context() as m:
        m.setattr(
            sys, "argv", ["summarize", str(profile.reports_root), "--output", str(summary_path)]
        )
        summarizer["main"]()
    summary = json.loads(summary_path.read_bytes())
    assert len(summary["recoveries"]) == 2
    assert summary["monitor"]["streams"]["depth"]["states"] == ["unreported"]
    assert summary["monitor"]["streams"]["depth"]["unreported_state_samples"] == 1
    assert summary["storage"]["hardlink_increment_allocated_bytes"] > 0
    for label in ("first", "second"):
        assert trial.read("recovery-" + label)["ok"]
        readback = trial.read("readback-" + label)
        assert readback["snapshots"] == 1
        assert readback["aggtrade"]["equal"]
        assert readback["depth_replay"]["replay_matches_live"]
        extracted = profile.work_root / (label + "-extracted")
        assert not (extracted / "state/layout.lock").exists()
        assert not (extracted / (backup.CATALOG + "-wal")).exists()
    # The recovered archive works even when the original store/checkpoint paths are gone.
    store.close()
    store.root.rename(tmp_path / "offline-store")
    profile.backup_dir.rename(tmp_path / "offline-archives")
    extracted = profile.work_root / "second-extracted"
    extra = tmp_path / "without-originals"
    backup.restore_backup(extracted, extra, min_free_gib=0)
    assert TRIAL["compare_readers"](extracted, extra)["ok"]
