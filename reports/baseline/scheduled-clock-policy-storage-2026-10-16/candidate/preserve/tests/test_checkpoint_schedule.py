import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from marketdata import backup, maintenance
from marketdata.domain import json_bytes, sha256
from marketdata.store import atomic_write
from test_checkpoint_retention import ready as ready  # noqa: F401
from test_restic_archive import agent, write_profile  # noqa: F401
from test_restic_archive import setup_archive as setup_archive

from scripts import archive_backups as archive
from scripts import checkpoint_schedule as schedule
from scripts import retire_checkpoints as retention


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))


def publish_daily(profile, value):
    start = archive.timestamp(value["started_at"])
    write_json(profile.store_root / "state/maintenance-daily-latest.json", value)
    write_json(
        profile.store_root / f"reports/maintenance/{start:%Y-%m-%d}/{value['id']}.json", value
    )


def timer_context():
    return {
        "scheduled": True,
        "service_verified": True,
        "classification": "verified_timer",
        "trigger_at": schedule.now()
        .replace(hour=6, minute=40, second=0, microsecond=0)
        .isoformat(),
    }


def grant(settings, enabled=True):
    value = schedule.proposal(settings)
    if enabled:
        value.update(
            enabled=True, approved_at=schedule.now(), approval_reference="isolated test grant"
        )
    write_json(settings.authorization_file, value)
    settings.authorization_file.chmod(0o600)
    return value


@pytest.fixture
def prepared(request, store, tmp_path, monkeypatch):
    clock = [datetime(2024, 1, 7, 5, tzinfo=UTC)]
    monkeypatch.setattr(archive, "now", lambda: clock[0])
    p, policy, cleaner = request.getfixturevalue("ready")
    monkeypatch.setattr(retention, "now", lambda: clock[0])
    monkeypatch.setattr(schedule, "now", lambda: clock[0])
    monkeypatch.setattr(
        schedule.restic_schedule,
        "service_context",
        lambda service, timer: (
            timer_context() if (service, timer) == (schedule.SERVICE, schedule.TIMER) else {}
        ),
    )
    record = max(
        archive.load_state(p)["checkpoints"].values(), key=lambda r: r["entry"]["checkpoint_at"]
    )
    result = backup.verify_backup(record["entry"]["path"])
    # Synthetic daily receipt tied to a real verified checkpoint, not a claimed timer invocation.
    daily = {
        "id": uuid.uuid4().hex,
        "mode": "daily",
        "phase": "finished",
        "state": "complete",
        "ok": True,
        "started_at": "2024-01-07T04:00:00Z",
        "finished_at": "2024-01-07T04:30:00Z",
        "validation": store.validate(),
        "backup": result,
    }
    publish_daily(p, daily)
    settings = schedule.Settings(
        tmp_path / "automatic-policy.toml", tmp_path / "recurring-grant.json"
    )
    write_profile(settings.retention_policy, policy)
    clock[0] = datetime(2024, 1, 7, 6, 40, tzinfo=UTC)
    grant(settings)
    return p, policy, cleaner, settings, clock, daily


def manifests(profile):
    return {str(p): sha256(p.read_bytes()) for p in profile.backup_dir.glob("*/*/backup.json")}


def test_preview_with_pending_grant_has_fresh_plans_and_never_deletes(prepared):
    p, _, _, settings, _, _ = prepared
    grant(settings, enabled=False)
    before, state = manifests(p), p.state_file.read_bytes()
    first, second = schedule.execute(settings, "preview"), schedule.execute(settings, "preview")
    assert first["state"] == second["state"] == "preview_ready"
    assert first["plan_id"] != second["plan_id"]
    assert len(first["plan"]["keep"]) == 3 and len(first["plan"]["candidates"]) == 2
    assert first["authorization"]["enabled"] is False
    assert not first["deletion_attempted"] and first["deleted_checkpoints"] == 0
    assert manifests(p) == before and p.state_file.read_bytes() == state
    assert all(e["protection"] and e["snapshot_id"] for e in first["plan"]["keep"])


def test_disabled_old_manual_and_changed_scope_never_authorize_run(prepared):
    p, _, cleaner, settings, _, _ = prepared
    before = manifests(p)
    grant(settings, enabled=False)
    r = schedule.execute(settings, "run")
    assert r["state"] == "deferred" and r["reason"] == "authorization_pending"
    manual = cleaner.plan()
    write_json(settings.authorization_file, manual)
    assert schedule.execute(settings, "run")["reason"] == "authorization_scope_changed"
    for field in (
        "uid",
        "policy_sha256",
        "profile_sha256",
        "identity",
        "paths",
        "code",
        "schedule",
    ):
        value = grant(settings)
        value["scope"][field] = "changed"
        value["scope_id"] = sha256(json_bytes(value["scope"]))
        write_json(settings.authorization_file, value)
        report = schedule.execute(settings, "run")
        assert report["reason"] == "authorization_scope_changed" and report["needs_attention"]
        assert report["plan_id"] is None
    assert manifests(p) == before


def test_pending_predecessors_and_invalid_evidence_are_distinguished(prepared):
    p, _, _, settings, _, original_daily = prepared
    original_state = p.state_file.read_bytes()
    cases = [
        ("previous_day", "deferred"),
        ("daily_running", "deferred"),
        ("daily_failed", "deferred"),
        ("unarchived", "deferred"),
        ("check_pending", "deferred"),
        ("check_missing_snapshot", "deferred"),
        ("wrong_path", "failed"),
        ("wrong_check_command", "failed"),
        ("wrong_archive_id", "failed"),
        ("archive_unfinished", "deferred"),
        ("archive_failed", "deferred"),
    ]
    before = manifests(p)
    for kind, expected in cases:
        daily = json.loads(json_bytes(original_daily))
        state = json.loads(original_state)
        bid = daily["backup"]["backup_id"]
        if kind == "previous_day":
            daily["started_at"] = "2024-01-06T04:00:00Z"
        elif kind == "daily_running":
            daily.update(state="running", ok=False)
        elif kind == "daily_failed":
            daily.update(state="failed", ok=False)
        elif kind == "unarchived":
            state["checkpoints"][bid]["stored"] = None
        elif kind == "check_pending":
            state["repository_check"]["read_data"] = False
        elif kind == "check_missing_snapshot":
            state["repository_check"]["snapshot_ids"] = []
        elif kind == "wrong_path":
            daily["backup"]["path"] += "/wrong"
        elif kind == "wrong_check_command":
            state["repository_check"]["receipt"]["arguments"] = ["check"]
        elif kind == "wrong_archive_id":
            state["checkpoints"][bid]["stored"]["snapshot_id"] = "f" * 64
        elif kind == "archive_unfinished":
            state["active_operation"] = {"phase": "checking"}
        elif kind == "archive_failed":
            state["last_operation"] = {"ok": False}
        publish_daily(p, daily)
        atomic_write(p.state_file, json_bytes(state))
        result = schedule.execute(settings, "preview")
        assert result["state"] == expected, (kind, result)
        assert result["plan_id"] is None and result["deleted_checkpoints"] == 0
    assert manifests(p) == before


def test_runner_maintenance_repository_and_reader_contention(prepared, store):
    p, _, _, settings, _, _ = prepared
    before = manifests(p)
    with schedule.runner_lock(p.state_file.parent / "working-retention/automatic"):
        assert schedule.execute(settings, "run")["reason"] == "runner_busy"
    with maintenance.maintenance_lock(store):
        assert schedule.execute(settings, "run")["reason"] == "maintenance_busy"
    with archive.repository_lock(p):
        assert schedule.execute(settings, "run")["reason"] == "archive_busy"
    source = p.backup_dir / "2024-01-01/attempt"
    code = """
import sys
from marketdata.checkpoint_locks import checkpoint_locks
with checkpoint_locks([sys.argv[1]]):
    print('held', flush=True)
    sys.stdin.readline()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(source)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "held"
        result = schedule.execute(settings, "run")
        assert result["state"] == "deferred" and result["reason"] == "checkpoint_busy"
        assert result["plan_id"] and result["deleted_checkpoints"] == 0
    finally:
        proc.communicate("done\n", timeout=10)
    assert manifests(p) == before


def test_authorized_run_then_noop_and_next_real_daily_archive(prepared, store, monkeypatch):
    p, _, _, settings, clock, _ = prepared
    original = {p: sha256(p.read_bytes()) for p in (store.root / "data").rglob("*") if p.is_file()}
    state = p.state_file.read_bytes()
    completed = schedule.execute(settings, "run")
    assert completed["state"] == "complete" and completed["deleted_checkpoints"] == 2
    assert all(backup.verify_backup(e["path"])["ok"] for e in completed["plan"]["keep"])
    noop = schedule.execute(settings, "run")
    assert noop["state"] == "no_candidates" and noop["plan_id"] != completed["plan_id"]
    assert not noop["deletion_attempted"] and noop["deleted_checkpoints"] == 0
    assert p.state_file.read_bytes() == state
    assert all(sha256(p.read_bytes()) == digest for p, digest in original.items())
    clock[0] = datetime(2024, 1, 8, 4, 10, tzinfo=UTC)
    monkeypatch.setattr(backup, "now", lambda: clock[0])
    monkeypatch.setattr(maintenance, "now", lambda: clock[0])
    store.put_raw(b"after scheduled cleanup", "test", "test")
    daily = maintenance.run(
        store, mode="daily", min_free_gib=0, backup_dir=p.backup_dir, incremental=True
    )
    assert daily["ok"] and daily["backup"]["storage"]["reused_files"] > 0
    clock[0] = datetime(2024, 1, 8, 5, tzinfo=UTC)
    archiver = agent(p, "next-real-daily")
    assert archiver.run()["processed"] == 1
    assert archiver.run()["processed"] == 0
    clock[0] = datetime(2024, 1, 8, 6, 40, tzinfo=UTC)
    result = schedule.execute(settings, "run")
    assert result["state"] == "complete" and result["deleted_checkpoints"] == 1
    assert result["daily_evidence"]["backup_id"] == daily["backup"]["backup_id"]
    assert len(archiver.restic.snapshots()) == 6


def test_revoked_grant_between_plan_and_apply_preserves_candidates(prepared, monkeypatch):
    p, _, _, settings, _, _ = prepared
    original = schedule.ScheduledRetirement.plan

    def revoke(self):
        result = original(self)
        value = json.loads(settings.authorization_file.read_bytes())
        value["enabled"] = False
        write_json(settings.authorization_file, value)
        return result

    monkeypatch.setattr(schedule.ScheduledRetirement, "plan", revoke)
    before = manifests(p)
    result = schedule.execute(settings, "run")
    assert result["reason"] == "authorization_pending" and result["state"] == "deferred"
    assert result["plan_id"] and result["deleted_checkpoints"] == 0
    assert manifests(p) == before


def test_sigkill_like_exit_blocks_new_plan_and_manual_resume_is_explicit(prepared, tmp_path):
    p, _, cleaner, settings, _, _ = prepared
    config = write_profile(tmp_path / "settings.toml", settings)
    code = """
import os,sys
from datetime import UTC,datetime
from scripts import archive_backups as archive, retire_checkpoints as retention
from scripts import checkpoint_schedule as schedule
archive.mount_identity=lambda _: 'test-uuid'
instant=datetime(2024,1,7,6,40,tzinfo=UTC)
archive.now=retention.now=schedule.now=lambda: instant
schedule.restic_schedule.service_context=lambda *a: dict(scheduled=True,service_verified=True,trigger_at=instant.isoformat())
original=retention.Retirement.retire_item
def retire(self,journal,item,hook):
    def die(phase,item):
        if phase=='after_move': os._exit(77)
    return original(self,journal,item,die)
retention.Retirement.retire_item=retire
schedule.execute(schedule.Settings.load(sys.argv[1]),'run')
"""
    proc = subprocess.run(
        [sys.executable, "-c", code, str(config)], capture_output=True, timeout=60
    )
    assert proc.returncode == 77, proc.stderr
    pending = cleaner.journals()
    assert len(pending) == 1 and pending[0]["status"] == "applying"
    plans_before = set((cleaner.root / "plans").glob("*.json"))
    result = schedule.execute(settings, "run")
    assert result["state"] == "deferred" and result["reason"] == "unfinished_retirement"
    assert result["needs_attention"] and result["plan_id"] is None
    assert set((cleaner.root / "plans").glob("*.json")) == plans_before
    assert cleaner.apply(pending[0]["plan_id"])["ok"]
    assert schedule.execute(settings, "run")["state"] == "no_candidates"


def test_real_validation_failure_and_timer_date_rejection(prepared, monkeypatch):
    p, _, _, settings, _, _ = prepared
    before = manifests(p)
    for kind in ("manual", "previous_day", "wrong_time"):
        context = timer_context()
        if kind == "manual":
            context["scheduled"] = False
        else:
            trigger = archive.timestamp(context["trigger_at"])
            trigger += timedelta(days=-1) if kind == "previous_day" else timedelta(minutes=-1)
            context["trigger_at"] = trigger.isoformat()
        with monkeypatch.context() as m:
            m.setattr(schedule.restic_schedule, "service_context", lambda *a: context)
            result = schedule.execute(settings, "run")
            assert result["state"] == "deferred" and result["needs_attention"]
    original = backup.verify_backup

    def fail(path):
        raise OSError("isolated retained-copy read failure")

    monkeypatch.setattr(backup, "verify_backup", fail)
    result = schedule.execute(settings, "run")
    assert result["state"] == "failed" and result["exit_code"] == 1
    assert result["deleted_checkpoints"] == 0 and result["needs_attention"]
    monkeypatch.setattr(backup, "verify_backup", original)
    assert manifests(p) == before


def test_abrupt_exit_before_retirement_journal_also_blocks_new_plan(prepared, tmp_path):
    p, _, cleaner, settings, _, _ = prepared
    config = write_profile(tmp_path / "settings.toml", settings)
    code = """
import os,sys
from datetime import UTC,datetime
from scripts import archive_backups as archive, retire_checkpoints as retention
from scripts import checkpoint_schedule as schedule
archive.mount_identity=lambda _: 'test-uuid'
instant=datetime(2024,1,7,6,40,tzinfo=UTC)
archive.now=retention.now=schedule.now=lambda: instant
schedule.restic_schedule.service_context=lambda *a: dict(scheduled=True,service_verified=True,trigger_at=instant.isoformat())
retention.Retirement.apply=lambda *a,**k: os._exit(77)
schedule.execute(schedule.Settings.load(sys.argv[1]),'run')
"""
    before = manifests(p)
    proc = subprocess.run(
        [sys.executable, "-c", code, str(config)], capture_output=True, timeout=60
    )
    assert proc.returncode == 77, proc.stderr
    assert not cleaner.journals()
    plans = set((cleaner.root / "plans").glob("*.json"))
    result = schedule.execute(settings, "run")
    assert result["state"] == "deferred" and result["reason"] == "unfinished_automatic_run"
    assert result["needs_attention"] and result["plan_id"] is None
    assert set((cleaner.root / "plans").glob("*.json")) == plans
    assert manifests(p) == before


def test_release_launcher_ignores_editable_package_and_units_have_no_retry(prepared, tmp_path):
    _, _, _, settings, _, _ = prepared
    config = write_profile(tmp_path / "settings.toml", settings)
    root = Path(schedule.__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH="/nonexistent", PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/run_checkpoint_schedule.py"),
            "--settings",
            str(config),
            "propose",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    value = json.loads(proc.stdout)
    assert value["scope"]["code"]["code_root"] == str(root) and value["enabled"] is False
    service = (root / "integrations/systemd/marketdata-checkpoint-retirement.service").read_text()
    timer = (root / "integrations/systemd/marketdata-checkpoint-retirement.timer").read_text()
    assert "Restart=no" in service and "SuccessExitStatus=75" in service
    assert "Persistent=false" in timer and "OnActiveSec" not in timer
    assert "OnCalendar=" + schedule.CALENDAR in timer
