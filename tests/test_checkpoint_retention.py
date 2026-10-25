import json
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from marketdata import backup, maintenance
from marketdata.checkpoint_locks import CheckpointBusy
from marketdata.domain import WriterBusy, json_bytes, sha256
from marketdata.store import atomic_write
from test_restic_archive import agent, setup_archive, write_profile  # noqa: F401

from scripts import archive_backups as archive
from scripts import checkpoint_code
from scripts import retire_checkpoints as retention


def test_selection_uses_checkpoint_order_completion_age_and_union():
    policy = retention.Policy(Path("/unused"), Path("/unused-receipt"), "0" * 64)
    instant = datetime(2026, 10, 3, 12, tzinfo=UTC)
    entries = [
        {
            "backup_id": str(i),
            "checkpoint_at": (instant - timedelta(days=6 - i)).isoformat(),
            "completed_at": (instant - timedelta(days=6 - i)).isoformat(),
        }
        for i in range(6)
    ]
    keep, old = retention.selection(entries, policy, instant)
    assert {e["backup_id"] for e in keep} == {"3", "4", "5"}
    assert {e["backup_id"] for e in old} == {"0", "1", "2"}
    # An old checkpoint that completed late is independently protected.
    entries[0]["completed_at"] = (
        instant - timedelta(hours=72) + timedelta(microseconds=1)
    ).isoformat()
    keep, old = retention.selection(entries, policy, instant)
    assert len(keep) == 4 and len(old) == 2
    entries[0]["completed_at"] = (instant - timedelta(hours=72)).isoformat()
    keep, candidates = retention.selection(entries, policy, instant)
    assert len(keep) == 3
    assert next(e for e in candidates if e["backup_id"] == "0")["age_seconds"] == 72 * 3600


@pytest.fixture
def ready(request, store, tmp_path, monkeypatch):
    p = request.getfixturevalue("setup_archive")
    previous = p.backup_dir / "2024-01-02/attempt"
    for day in ("2024-01-05", "2024-01-06", "2024-01-07"):
        instant = datetime.fromisoformat(day + "T04:10:00+00:00")
        with monkeypatch.context() as m:
            m.setattr(backup, "now", lambda: instant)
            store.put_raw(day.encode(), "test:daily", "test")
            new = p.backup_dir / day / "attempt"
            backup.create_backup(store, new, min_free_gib=0, reuse_from=previous)
            previous = new
    a = agent(p, "archive")
    original_command = a.restic.command

    def verify_pin(args, **kwargs):
        if args[0] == "backup":
            code = """
import sys
from marketdata.checkpoint_locks import checkpoint_locks, CheckpointBusy
try:
    with checkpoint_locks([sys.argv[1]], exclusive=True, wait=False):
        pass
except CheckpointBusy:
    sys.exit(23)
"""
            check = subprocess.run(
                [sys.executable, "-c", code, str(kwargs["cwd"])], capture_output=True, timeout=10
            )
            assert check.returncode == 23, check.stderr
            with store.writer(timeout=0):
                pass
        return original_command(args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(a.restic, "command", verify_pin)
        assert a.run()["processed"] == 3
    assert a.run()["processed"] == 2
    latest = max(a.state["checkpoints"].values(), key=lambda r: r["entry"]["checkpoint_at"])
    recovery = a.restore_check(latest["stored"]["snapshot_id"])
    receipt = tmp_path / "recovery-proof.json"
    receipt.write_bytes(
        json_bytes(
            {
                "ok": True,
                "action": "restore-check",
                "finished_at": archive.now(),
                "result": recovery,
            }
        )
    )
    policy = retention.Policy(
        tmp_path / "profile.toml", receipt, sha256(receipt.read_bytes()), apply_enabled=True
    )
    reports = p.reports_root / "retention"
    reports.mkdir()
    monkeypatch.setattr(retention, "now", lambda: datetime(2024, 1, 7, 12, tzinfo=UTC))
    return p, policy, retention.Retirement(policy, reports)


def test_actual_cleanup_next_daily_archive_no_duplicate_and_missing_snapshot(
    ready, store, monkeypatch, tmp_path
):
    p, policy, cleaner = ready
    source_files = {
        f: sha256(f.read_bytes()) for f in (store.root / "data").rglob("*") if f.is_file()
    }
    state = archive.load_state(p)
    # A resolved failed attempt stays in history and must not permanently veto retirement.
    first = next(iter(state["checkpoints"].values()))
    first["attempts"].insert(0, {"status": "failed", "error": "old recovered failure"})
    state["restore_checks"].insert(
        0,
        {
            "snapshot_id": state["restore_checks"][0]["snapshot_id"],
            "ok": False,
            "error": "resolved by later successful restore",
        },
    )
    atomic_write(p.state_file, json_bytes(state))
    plan = cleaner.plan()
    assert len(plan["keep"]) == 3 and len(plan["candidates"]) == 2
    assert plan["schema"] == 2
    # A frozen exported release has no Git checkout; its exact source identity
    # must still be present and match the running implementation.
    assert plan["code"] == checkpoint_code.current_code()
    assert len(plan["code"]["source_sha256"]) == 64 and plan["code"]["source_files"]
    assert all(e["snapshot_id"] for e in plan["keep"])
    for candidate in plan["candidates"]:
        age = archive.timestamp(plan["assessed_at"]) - archive.timestamp(
            candidate["entry"]["completed_at"]
        )
        assert candidate["age_seconds"] == age.total_seconds()
    retired_source = Path(plan["candidates"][0]["entry"]["path"])
    held_copy = tmp_path / "manual-held-copy"
    shutil.copytree(retired_source, held_copy)
    assert plan["space"]["estimated_reclaim_file_bytes"] < plan["space"]["logical_file_bytes"]
    result = cleaner.apply(plan["plan_id"])
    assert result["ok"] and result["journal"]["status"] == "complete"
    space = result["journal"]["attempts"][0]["space_observation"]
    assert space["before"]["ok"] and space["after"]["ok"]
    assert (
        space["observed_available_change_bytes"]
        == space["after"]["available_bytes"] - space["before"]["available_bytes"]
    )
    assert space["exact_reclaimed_bytes"] is None and space["includes_concurrent_activity"]
    assert cleaner.apply(plan["plan_id"])["already_complete"]
    assert all(not Path(c["entry"]["path"]).exists() for c in plan["candidates"])
    assert all(backup.verify_backup(e["path"])["ok"] for e in plan["keep"])
    assert {f: sha256(f.read_bytes()) for f in source_files} == source_files
    assert archive.load_state(p) == state  # Retirement never rewrites archiver history.
    held_copy.rename(retired_source)
    try:
        with pytest.raises(ValueError, match="unexpectedly reappeared"):
            cleaner.plan()
    finally:
        retired_source.rename(held_copy)
    # The actual daily checkpoint entrypoint can still reuse a surviving copy.
    instant = datetime(2024, 1, 8, 4, 10, tzinfo=UTC)
    monkeypatch.setattr(backup, "now", lambda: instant)
    monkeypatch.setattr(retention, "now", lambda: instant + timedelta(hours=1))
    store.put_raw(b"after cleanup daily", "test", "test")
    with maintenance.maintenance_lock(store):
        daily = maintenance.daily_backup(
            store,
            instant.date().isoformat(),
            min_free_gib=0,
            progress=None,
            directory=p.backup_dir,
            incremental=True,
        )
    assert daily["storage"]["reused_files"] > 0
    next_run = agent(p, "next-daily-archive")
    assert next_run.run()["processed"] == 1
    assert next_run.run()["processed"] == 0
    assert len(next_run.restic.snapshots()) == 6
    assert cleaner.plan()["deleted_checkpoints"] == 0
    # Missing local retired copies are explained; missing restic snapshots are not.
    sid = plan["candidates"][0]["snapshot_id"]
    (p.restic_repository / "snapshots" / sid).rename(tmp_path / "isolated-snapshot-held")
    with pytest.raises(ValueError, match="snapshot is missing"):
        agent(p, "missing-snapshot").run()
    with pytest.raises(ValueError, match="snapshot is missing"):
        cleaner.plan()


def test_interrupted_retirement_resumes_only_authorized_remaining_files(ready, monkeypatch):
    p, policy, cleaner = ready
    plan = cleaner.plan()
    policy_file = write_profile(p.store_root.parent / "retention.toml", policy)
    # Simulate abrupt death without Python exception/finally journaling, at each phase.
    for stop in (
        "before_move",
        "after_move",
        "after_unlink",
        "before_root_remove",
        "after_root_remove",
    ):
        code = """
import os, sys
from datetime import datetime, UTC
from pathlib import Path
from scripts import archive_backups as archive, retire_checkpoints as retention
archive.mount_identity = lambda _: 'test-uuid'  # Isolated temporary test filesystem only.
retention.now = lambda: datetime(2024, 1, 7, 12, tzinfo=UTC)
def kill(phase, item):
    if phase == sys.argv[3]:
        os._exit(77)
cleaner = retention.Retirement(retention.Policy.load(sys.argv[1]), Path(sys.argv[4]))
cleaner.apply(sys.argv[2], hook=kill)
"""
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(policy_file),
                plan["plan_id"],
                stop,
                str(cleaner.a.reports),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 77, (stop, proc.stderr)
        pending = cleaner.journals()[0]
        assert pending["status"] == "applying"
        assert not pending["attempts"][-1]["ok"]
        assert pending["attempts"][-1]["space_observation"]["after"] is None
        # No state deletion, no partial checkpoint left inside daily discovery.
        assert not archive.discover(p)[1]
        if stop == "after_unlink":
            item = next(iter(pending["items"].values()))
            unknown = Path(item["staged"]) / "unexpected.txt"
            unknown.write_text("must never be removed by cleanup")
            with pytest.raises(ValueError, match="Unexpected/changed"):
                cleaner.apply(plan["plan_id"])
            assert unknown.read_text() == "must never be removed by cleanup"
            unknown.rename(unknown.parent.parent / "held-unexpected.txt")
    final = cleaner.apply(plan["plan_id"])
    assert final["ok"]
    assert len(final["journal"]["attempts"]) == 7
    assert all(backup.verify_backup(e["path"])["ok"] for e in plan["keep"])
    assert agent(p, "post-crash-archive").run()["processed"] == 0


def test_guards_preserve_candidates_and_failure_history(ready, monkeypatch, tmp_path, store):
    p, policy, cleaner = ready
    original = p.state_file.read_bytes()
    plan = cleaner.plan()
    with maintenance.maintenance_lock(store):
        with pytest.raises(WriterBusy):
            cleaner.plan()
    with archive.repository_lock(p):
        with pytest.raises(archive.Busy):
            cleaner.plan()
    code = """
import sys
from marketdata.checkpoint_locks import checkpoint_locks
with checkpoint_locks([sys.argv[1]]):
    print('pinned', flush=True)
    sys.stdin.readline()
"""
    reader = subprocess.Popen(
        [sys.executable, "-c", code, plan["candidates"][0]["entry"]["path"]],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert reader.stdout.readline().strip() == "pinned"
        with pytest.raises(CheckpointBusy):
            cleaner.apply(plan["plan_id"])
        assert not cleaner.journals()
    finally:
        reader.communicate("release\n", timeout=10)
    for kind in ("error", "unchecked", "repository_check", "restore", "active"):
        state = json.loads(original)
        if kind == "error":
            next(iter(state["checkpoints"].values()))["error"] = {"message": "unresolved"}
        elif kind == "unchecked":
            state["repository_check"]["snapshot_ids"] = []
        elif kind == "repository_check":
            state["repository_check"]["ok"] = False
        elif kind == "restore":
            state["restore_checks"].append(
                {"snapshot_id": state["restore_checks"][-1]["snapshot_id"], "ok": False}
            )
        else:
            state["active_operation"] = {"action": "run", "started_at": "interrupted"}
        atomic_write(p.state_file, json_bytes(state))
        with pytest.raises(ValueError, match="Unresolved|Unchecked|incomplete"):
            cleaner.apply(plan["plan_id"])
        assert all(Path(c["entry"]["path"]).exists() for c in plan["candidates"])
    atomic_write(p.state_file, original)
    retained_catalog = Path(plan["keep"][0]["path"]) / backup.CATALOG
    catalog_bytes = retained_catalog.read_bytes()
    retained_catalog.write_bytes(catalog_bytes[:-1] + bytes([catalog_bytes[-1] ^ 1]))
    try:
        with pytest.raises(ValueError, match="hash mismatch"):
            cleaner.apply(plan["plan_id"])
        assert not cleaner.journals()
    finally:
        retained_catalog.write_bytes(catalog_bytes)
    disabled = retention.Retirement(replace(policy, apply_enabled=False), cleaner.a.reports)
    with pytest.raises(ValueError, match="disabled"):
        disabled.apply(plan["plan_id"])
    for failure in ("uuid", "repo", "proof"):
        with monkeypatch.context() as m:
            if failure == "uuid":
                m.setattr(archive, "mount_identity", lambda _: "wrong-uuid")
            elif failure == "repo":
                cleaner.p = replace(p, expected_repository_id="0" * 64)
                cleaner.a = archive.Archive(cleaner.p, cleaner.a.reports)
            else:
                cleaner.policy = replace(policy, recovery_receipt_sha256="0" * 64)
            with pytest.raises(ValueError):
                cleaner.plan()
            cleaner.p, cleaner.policy = p, policy
            cleaner.a = archive.Archive(p, cleaner.a.reports)
    candidate = Path(plan["candidates"][0]["entry"]["path"])
    candidate.rename(tmp_path / "unexpectedly-missing-input")
    with pytest.raises(ValueError, match="Unexplained missing"):
        cleaner.plan()
    (tmp_path / "unexpectedly-missing-input").rename(candidate)
    incomplete = p.backup_dir / "2024-01-07" / "incomplete"
    incomplete.mkdir()
    with pytest.raises(ValueError, match="Incomplete"):
        cleaner.plan()
    incomplete.rename(tmp_path / "review-incomplete")
    with monkeypatch.context() as m:
        m.setattr(backup, "now", lambda: datetime(2023, 12, 31, 4, tzinfo=UTC))
        old = p.backup_dir / "2023-12-31" / "before-enrollment"
        backup.create_backup(store, old, min_free_gib=0)
    assert cleaner.plan()["excluded"][0]["path"] == str(old)
    # Same size bytes with a changed mtime/inode invalidate the approved plan.
    file = candidate / "backup.json"
    original_file = file.read_bytes()
    file.write_bytes(original_file)
    with pytest.raises(ValueError, match="tree changed"):
        cleaner.apply(plan["plan_id"])
    assert not cleaner.journals()
    assert all(Path(c["entry"]["path"]).exists() for c in plan["candidates"])


def test_tree_inventory_rejects_unlisted_files_and_symlinks(store, tmp_path):
    root = tmp_path / "archive"
    backup.create_backup(store, root, min_free_gib=0)
    manifest = json.loads((root / "backup.json").read_bytes())
    extra = root / "extra"
    extra.write_text("not registered")
    with pytest.raises(ValueError, match="Unexpected"):
        retention.tree_inventory(root, manifest)
    extra.rename(tmp_path / "held-extra")
    extra.symlink_to(tmp_path / "held-extra")
    with pytest.raises(ValueError, match="Symlink"):
        retention.tree_inventory(root, manifest)
