import json
import os
import subprocess
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from marketdata import backup
from marketdata.domain import DataError, json_bytes, sha256
from test_backup import populate

from scripts import archive_backups as archive

BINARY = Path(__file__).resolve().parents[1] / ".cache/restic/0.19.1/restic"


def write_profile(path, profile):
    path.write_text(
        "\n".join(
            f"{k} = {json.dumps(str(v) if isinstance(v, Path) else v)}"
            for k, v in vars(profile).items()
        )
        + "\n"
    )
    return path


def test_profile_and_timezone_guards(tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        archive.timestamp("2026-09-26T13:10:00")
    assert archive.timestamp("2026-09-26T13:10:00+09:00").isoformat() == "2026-09-26T04:10:00+00:00"


@pytest.fixture
def setup_archive(store, tmp_path, monkeypatch):
    if not BINARY.is_file():
        pytest.skip("Pinned restic binary required for real repository integration")
    populate(store, tmp_path)
    mount = tmp_path / "disk"
    repo = mount / "repository"
    repo.mkdir(parents=True)
    password = tmp_path / "password"
    password.write_text("isolated test password\n")
    password.chmod(0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith("RESTIC_")}
    args = [
        str(BINARY),
        "--repo",
        str(repo),
        "--password-file",
        str(password),
        "--no-cache",
        "--json",
    ]
    subprocess.run(args + ["init"], env=env, check=True, capture_output=True)
    identity = json.loads(subprocess.check_output(args + ["cat", "config"], env=env))["id"]
    p = archive.Profile(
        store.root,
        mount / "backups",
        mount,
        "test-uuid",
        repo,
        identity,
        BINARY,
        password,
        "isolated-source-v1",
        "2024-01-01",
        tmp_path / "state",
        tmp_path / "reports",
        tmp_path / "cache",
        mount / "recovery",
        1,
        3,
        2,
    )
    p.backup_dir.mkdir()
    monkeypatch.setattr(archive, "mount_identity", lambda path: "test-uuid")
    p = archive.Profile.load(write_profile(tmp_path / "profile.toml", p))
    previous = None
    for day in ("2024-01-01", "2024-01-02"):
        instant = datetime.fromisoformat(day + "T04:10:00.123456+00:00")
        with monkeypatch.context() as m:
            m.setattr(backup, "now", lambda: instant)
            root = p.backup_dir / day / "attempt"
            if previous:
                store.put_raw(b"another day's raw data", "test:increment", "test")
            backup.create_backup(store, root, min_free_gib=0, reuse_from=previous)
            previous = root
    return p


def agent(p, name):
    root = p.reports_root / name
    root.mkdir(parents=True, exist_ok=False)
    return archive.Archive(p, root)


@pytest.mark.parametrize(
    "kind", ["overlap", "secret", "symlink", "repository_id", "scope", "reserve"]
)
def test_invalid_profile(setup_archive, tmp_path, kind):
    p = setup_archive
    if kind == "overlap":
        p = replace(p, restic_repository=p.backup_dir / "nested")
    elif kind == "secret":
        p = replace(p, restic_password_file=p.reports_root / "secret")
    elif kind == "symlink":
        (tmp_path / "link").symlink_to(p.cache_root)
        p = replace(p, cache_root=tmp_path / "link")
    elif kind == "repository_id":
        p = replace(p, expected_repository_id="deadbeef")
    elif kind == "scope":
        p = replace(p, archive_scope="x,backup_id=bad")
    else:
        p = replace(p, min_free_gib=float("inf"))
    with pytest.raises((ValueError, TypeError)):
        archive.Profile.load(write_profile(tmp_path / "bad.toml", p))


@pytest.mark.parametrize(
    "kind", ["uuid", "mount", "repository", "password_mode", "password", "space"]
)
def test_host_failure_is_recorded_without_initializing(setup_archive, monkeypatch, kind):
    p = setup_archive
    if kind == "uuid":
        p = replace(p, backup_filesystem_uuid="wrong")
    elif kind == "mount":

        def absent(path):
            raise ValueError("Required backup mount is missing")

        monkeypatch.setattr(archive, "mount_identity", absent)
    elif kind == "repository":
        p = replace(p, expected_repository_id="0" * 64)
    elif kind == "password_mode":
        p.restic_password_file.chmod(0o644)
    elif kind == "password":
        p.restic_password_file.write_text("wrong password\n")
    else:
        p = replace(p, min_free_gib=10**12)
    with pytest.raises((ValueError, archive.CommandError)):
        archive.execute(p, "run")
    reports = list(p.reports_root.glob("*/run.json"))
    assert len(reports) == 1 and not json.loads(reports[0].read_bytes())["ok"]
    assert not archive.load_state(p)["checkpoints"]


def test_missing_repository_is_not_created(setup_archive):
    p = replace(setup_archive, restic_repository=setup_archive.backup_mount / "missing-repo")
    with pytest.raises(ValueError, match="automatic init"):
        archive.execute(p, "run")
    assert not p.restic_repository.exists()


def test_delayed_days_preserve_time_paths_scope_and_idempotency(setup_archive, monkeypatch):
    p = setup_archive
    # Deliberately hostile inherited settings cannot replace the profile or timezone.
    monkeypatch.setenv("RESTIC_REPOSITORY", "/wrong/repo")
    monkeypatch.setenv("RESTIC_PASSWORD_COMMAND", "false")
    monkeypatch.setenv("TZ", "Asia/Seoul")
    before = {path: sha256(path.read_bytes()) for path in p.backup_dir.glob("*/*/backup.json")}
    first = agent(p, "first")
    result = first.run()
    assert result["processed"] == 2
    snapshots = first.restic.snapshots()
    assert sorted(archive.timestamp(s["time"]).isoformat() for s in snapshots) == [
        "2024-01-01T04:10:00.123456+00:00",
        "2024-01-02T04:10:00.123456+00:00",
    ]
    assert len({tuple(s["paths"]) for s in snapshots}) == 2
    assert all(Path(s["paths"][0]).is_absolute() for s in snapshots)
    assert all("archive_scope=isolated-source-v1" in s["tags"] for s in snapshots)
    assert len({tuple(sorted(s["tags"])) for s in snapshots}) == 2
    records = list(first.state["checkpoints"].values())
    assert len({r["stored"]["archive_finished_at"][:10] for r in records}) == 1
    assert all(
        r["stored"]["archive_finished_at"][:10] != r["entry"]["checkpoint_at"][:10] for r in records
    )
    stored = {r["entry"]["backup_id"]: r["stored"] for r in records}
    checked = first.state["repository_check"]
    second = agent(p, "second")
    assert second.run()["processed"] == 0
    assert len(second.restic.snapshots()) == 2
    assert second.state["repository_check"] == checked
    assert {
        r["entry"]["backup_id"]: r["stored"] for r in second.state["checkpoints"].values()
    } == stored
    assert all(sha256(path.read_bytes()) == digest for path, digest in before.items())
    status = archive.overview(p)
    assert status["pending_count"] == 0 and status["daily_stale"]
    assert not status["repository_live_checked"]


def test_backlog_limit_and_incomplete_folders(setup_archive):
    p = replace(setup_archive, max_checkpoints_per_run=1)
    (p.backup_dir / "2024-01-02" / "unfinished").mkdir()
    first = agent(p, "first")
    assert first.run()["processed"] == 1
    assert archive.overview(p)["pending_count"] == 1
    assert archive.overview(p)["incomplete_folders"]
    assert agent(p, "next-wakeup").run()["processed"] == 1
    assert archive.overview(p)["pending_count"] == 0


@pytest.mark.parametrize("kind", ["foreign", "manifest", "file", "naive_time"])
def test_bad_checkpoint_never_stored(setup_archive, kind):
    p = setup_archive
    root = next(p.backup_dir.glob("*/*/backup.json")).parent
    manifest = json.loads((root / "backup.json").read_bytes())
    if kind == "file":
        f = next(f for f in manifest["files"] if f["kind"] == "raw")
        (root / f["path"]).write_bytes(b"damaged")
    else:
        if kind == "foreign":
            manifest["source_root"] = "/another/source"
        elif kind == "naive_time":
            manifest["checkpoint_finished_at"] = "2024-01-01T04:10:00.123456"
        else:
            manifest["completed_at"] = "changed without hashing"
        if kind != "manifest":
            manifest.pop("backup_id")
            manifest["backup_id"] = sha256(json_bytes(manifest))
        (root / "backup.json").write_bytes(json_bytes(manifest))
    a = agent(p, "run")
    with pytest.raises((ValueError, DataError)):
        a.run()
    assert a.restic.snapshots() == []


def test_repository_lock_blocks_run_and_verify(setup_archive):
    p = setup_archive
    with archive.repository_lock(p):
        assert archive.execute(p, "status")["lock_busy"]
        with pytest.raises(archive.Busy):
            archive.execute(p, "run")
        with pytest.raises(archive.Busy):
            archive.execute(p, "verify")
    receipts = [json.loads(f.read_bytes()) for f in p.reports_root.glob("*/run.json")]
    assert len(receipts) == 2 and all(r["status"] == "lock_busy" for r in receipts)


def test_missing_mount_status_preserves_record_and_reports_unknown_backlog(
    setup_archive, monkeypatch
):
    p = setup_archive

    def absent(path):
        raise ValueError("Required backup mount is missing")

    monkeypatch.setattr(archive, "mount_identity", absent)
    result = archive.execute(p, "status")
    assert not result["healthy"] and not result["environment"]["ok"]
    assert result["pending_count"] is None
    assert not p.state_file.exists()


def test_crash_after_snapshot_recovers_with_full_extraction(setup_archive, monkeypatch):
    p = replace(setup_archive, max_checkpoints_per_run=1)
    a = agent(p, "interrupted")
    original = a.restic.command

    def crash(args, **kwargs):
        result = original(args, **kwargs)
        if args[0] == "backup":
            raise KeyboardInterrupt("simulated loss of success receipt")
        return result

    monkeypatch.setattr(a.restic, "command", crash)
    with pytest.raises(KeyboardInterrupt):
        a.run()
    sid = original(["snapshots"])[0]
    expected = json.loads(sid)[0]["id"]
    b = agent(p, "resumed")
    result = b.run()
    assert result["stored_snapshots"] == [expected]
    assert len(b.restic.snapshots()) == 1
    record = next(iter(b.state["checkpoints"].values()))
    assert record["stored"]["evidence"] == "extracted_and_fully_verified"
    assert record["reconciliations"][0]["ok"]
    assert Path(record["reconciliations"][0]["extracted"], "backup.json").is_file()


def test_real_exit_three_is_retained_failed_then_retried(setup_archive, monkeypatch):
    p = replace(setup_archive, max_checkpoints_per_run=1)
    a = agent(p, "partial")
    original = a.restic.command

    def unreadable(args, **kwargs):
        if args[0] != "backup":
            return original(args, **kwargs)
        root = Path(kwargs["cwd"])
        manifest = json.loads((root / "backup.json").read_bytes())
        file = root / next(f["path"] for f in manifest["files"] if f["kind"] == "raw")
        mode = file.stat().st_mode & 0o777
        file.chmod(0)
        try:
            return original(args, **kwargs)
        finally:
            file.chmod(mode)

    monkeypatch.setattr(a.restic, "command", unreadable)
    with pytest.raises(archive.CommandError) as failed:
        a.run()
    assert failed.value.receipt["returncode"] == 3
    record = next(iter(a.state["checkpoints"].values()))
    assert record["stored"] is None and record["attempts"][0]["returncode"] == 3
    partial = a.restic.snapshots()[0]
    # The partial snapshot does contain a valid manifest. This alone cannot prove success.
    a.match(partial, record["entry"])
    b = agent(p, "retry")
    b.run()
    assert len(b.restic.snapshots()) == 2
    stored = next(r["stored"] for r in b.state["checkpoints"].values() if r["stored"])
    assert stored["snapshot_id"] != partial["id"]


def test_repository_check_failure_does_not_erase_stored_success(setup_archive, monkeypatch):
    p = replace(setup_archive, max_checkpoints_per_run=1)
    a = agent(p, "fail-check")
    original = a.restic.command

    def bad_check(args, **kwargs):
        if args[0] == "check":
            raise archive.CommandError(
                {"operation": "check", "returncode": 1, "stderr": "simulated"}
            )
        return original(args, **kwargs)

    monkeypatch.setattr(a.restic, "command", bad_check)
    with pytest.raises(archive.CommandError):
        a.run()
    assert len(a.restic.snapshots()) == 1
    assert any(r["stored"] for r in a.state["checkpoints"].values())
    assert not a.state["repository_check"]["ok"]
    b = agent(p, "blocked")
    with pytest.raises(ValueError, match="run verify"):
        b.run()
    assert len(b.restic.snapshots()) == 1
    b.verify()
    assert b.run()["processed"] == 1
    assert len(b.restic.snapshots()) == 2


def test_state_loss_duplicate_reconciliation_and_missing_snapshot(setup_archive):
    p = replace(setup_archive, max_checkpoints_per_run=1)
    a = agent(p, "initial")
    a.run()
    record = next(r for r in a.state["checkpoints"].values() if r["stored"])
    args = record["attempts"][0]["command"]["arguments"]
    duplicate_args = ["attempt_id=" + "f" * 32 if x.startswith("attempt_id=") else x for x in args]
    a.restic.command(duplicate_args, cwd=record["entry"]["path"])
    p.state_file.rename(p.state_file.with_suffix(".saved"))
    b = agent(p, "lost-state")
    b.run()
    assert len(b.restic.snapshots()) == 2
    record = next(r for r in b.state["checkpoints"].values() if r["stored"])
    assert len(record["stored"]["verified_duplicates"]) == 2
    sid = record["stored"]["snapshot_id"]
    # Simulate a lost repository object, retaining it outside the repository for the test.
    path = p.restic_repository / "snapshots" / sid
    path.rename(p.backup_mount / (sid + ".saved"))
    with pytest.raises(ValueError, match="snapshot is missing"):
        agent(p, "missing-snapshot").run()


def test_full_restore_uses_new_root_and_independent_first_backup(setup_archive):
    p = replace(setup_archive, max_checkpoints_per_run=1)
    a = agent(p, "store")
    sid = a.run()["stored_snapshots"][0]
    root = Path(next(r for r in a.state["checkpoints"].values() if r["stored"])["entry"]["path"])
    manifest = (root / "backup.json").read_bytes()
    proof = a.restore_check(sid)
    assert proof["ok"] and proof["queries"]["ok"] and proof["independent_file_checks"] > 0
    assert (root / "backup.json").read_bytes() == manifest
    assert (Path(proof["extracted"]) / "backup.json").read_bytes() == manifest
    new = json.loads((Path(proof["new_backup"]) / "backup.json").read_bytes())
    assert new["source_root"] == proof["restored"]
    assert new["backup_id"] != json.loads(manifest)["backup_id"]
    assert new["storage"]["mode"] == "independent_copy"
    assert not (Path(proof["extracted"]) / "state/file-layout.lock").exists()


def test_restore_without_original_folders_or_local_state(setup_archive):
    p = replace(setup_archive, max_checkpoints_per_run=1)
    a = agent(p, "initial")
    sid = a.run()["stored_snapshots"][0]
    p.backup_dir.rename(p.backup_mount / "offline-archives")
    p.state_file.rename(p.state_file.with_suffix(".saved"))
    b = agent(p, "disaster-recovery")
    proof = b.restore_check(sid)
    assert proof["ok"] and proof["queries"]["ok"]
    record = next(iter(b.state["checkpoints"].values()))
    assert record["stored"]["evidence"] == "explicit_restore_extracted_and_fully_verified"


def test_unknown_partial_candidate_with_manifest_cannot_be_adopted(setup_archive):
    p = replace(setup_archive, max_checkpoints_per_run=1)
    a = agent(p, "partial")
    entry = archive.discover(p)[0][0]
    raw = json.loads((Path(entry["path"]) / "backup.json").read_bytes())
    skipped = next(f["path"] for f in raw["files"] if f["kind"] == "raw")
    args = ["backup", ".", "--force", "--time", "2024-01-01 04:10:00.123456", "--exclude", skipped]
    for tag in archive.expected_tags(p, entry) | {"attempt_id=" + "b" * 32}:
        args += ["--tag", tag]
    a.restic.command(args, cwd=entry["path"])
    assert len(a.restic.snapshots()) == 1
    with pytest.raises(ValueError, match="Unverified recovery candidates"):
        a.run()
    assert len(a.restic.snapshots()) == 1
    assert not next(iter(a.state["checkpoints"].values()))["stored"]
