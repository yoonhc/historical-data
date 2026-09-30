import json
import os

import pytest
from test_restic_archive import setup_archive as setup_archive

from scripts import restic_schedule as schedule


def evidence():
    observation = {
        "version": 1,
        "started_at": "2026-09-27T12:00:00Z",
        "excluded_backup_ids": [],
        "required_distinct_dates": 3,
        "qualified_dates": {},
        "runs": [],
        "restore": {"status": "awaiting_new_snapshot"},
    }
    context = {
        "scheduled": True,
        "classification": "verified_timer",
        "environment": {"INVOCATION_ID": "a" * 32},
        "trigger_at": "2026-09-28T04:35:00Z",
    }
    receipt = {
        "action": "run",
        "run_id": "b" * 32,
        "ok": True,
        "result": {"processed": 1},
        "started_at": "2026-09-28T04:35:01Z",
        "finished_at": "2026-09-28T04:38:00Z",
    }
    record = {
        "entry": {
            "backup_id": "c" * 64,
            "checkpoint_at": "2026-09-28T04:20:00Z",
            "manifest_sha256": "d" * 64,
            "path": "/backup/2026-09-28/id",
        },
        "stored": {
            "snapshot_id": "e" * 64,
            "archive_started_at": "2026-09-28T04:36:00Z",
            "archive_finished_at": "2026-09-28T04:37:00Z",
        },
        "attempts": [
            {
                "snapshot_id": "e" * 64,
                "status": "stored",
                "returncode": 0,
                "started_at": "2026-09-28T04:35:59Z",
            }
        ],
    }
    state = {
        "checkpoints": {"c" * 64: record},
        "repository_check": {
            "ok": True,
            "read_data": True,
            "started_at": "2026-09-28T04:37:01Z",
            "finished_at": "2026-09-28T04:37:59Z",
            "snapshot_ids": ["e" * 64],
            "receipt": {"returncode": 0},
        },
    }
    return observation, context, receipt, state


@pytest.mark.parametrize(
    "reason",
    [
        "manual",
        "noop",
        "failed",
        "excluded",
        "old_checkpoint",
        "old_upload",
        "unknown_upload",
        "old_check",
        "check_failed",
        "not_read_data",
        "not_checked",
        "bad_attempt",
    ],
)
def test_only_new_timer_storage_and_full_check_count(reason):
    o, c, r, s = evidence()
    rec = s["checkpoints"]["c" * 64]
    if reason == "manual":
        c["scheduled"] = False
    elif reason == "noop":
        r["result"]["processed"] = 0
    elif reason == "failed":
        r["ok"] = False
    elif reason == "excluded":
        o["excluded_backup_ids"] = ["c" * 64]
    elif reason == "old_checkpoint":
        rec["entry"]["checkpoint_at"] = "2026-09-27T04:20:00Z"
    elif reason == "old_upload":
        rec["stored"]["archive_started_at"] = "2026-09-27T21:00:00Z"
    elif reason == "unknown_upload":
        rec["stored"]["archive_started_at"] = None
    elif reason == "old_check":
        s["repository_check"]["started_at"] = "2026-09-27T22:00:00Z"
    elif reason == "check_failed":
        s["repository_check"]["ok"] = False
    elif reason == "not_read_data":
        s["repository_check"]["read_data"] = False
    elif reason == "not_checked":
        s["repository_check"]["snapshot_ids"] = []
    else:
        rec["attempts"][0]["returncode"] = 3
    assert schedule.eligible_records(o, c, r, s) == []


def test_three_distinct_checkpoint_dates_and_restore_are_separate():
    o, c, r, s = evidence()
    first = schedule.eligible_records(o, c, r, s)
    assert schedule.add_dates(o, first) == ["2026-09-28"]
    assert schedule.add_dates(o, first) == []
    for day in ("2026-09-29", "2026-09-30"):
        record = {**first[0], "date_utc": day}
        schedule.add_dates(o, [record])
    assert schedule.progress(o)["completed_dates"] == 3
    assert not schedule.progress(o)["complete"]
    o["restore"]["status"] = "passed"
    assert schedule.progress(o)["complete"]
    assert schedule.add_dates(o, [{**first[0], "date_utc": "2026-10-01"}]) == []


@pytest.mark.parametrize(
    "kind",
    [
        "timer",
        "stale_env",
        "missing_env",
        "manual_service",
        "manual_stale_env",
        "cli",
        "wrong_pid",
        "wrong_stamp",
        "wrong_unit",
        "wrong_trigger",
        "missing_job",
        "job_changed",
        "waiting_job",
        "missing_stamps",
        "duplicate_details",
    ],
)
def test_trigger_provenance_is_checked_against_manager(monkeypatch, kind):
    for k in schedule.TRIGGER_KEYS:
        monkeypatch.delenv(k, raising=False)
    env = {
        "INVOCATION_ID": "a" * 32,
        "TRIGGER_UNIT": schedule.TIMER,
        "TRIGGER_TIMER_REALTIME_USEC": "1790568000000000",
        "TRIGGER_TIMER_MONOTONIC_USEC": "5000000",
    }
    if kind != "cli":
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    if kind in ("manual_service", "missing_env"):
        for key in schedule.TRIGGER_KEYS[1:]:
            monkeypatch.delenv(key, raising=False)
    if kind in ("stale_env", "manual_stale_env"):
        monkeypatch.setenv("TRIGGER_TIMER_REALTIME_USEC", "123")
        monkeypatch.setenv("TRIGGER_TIMER_MONOTONIC_USEC", "456")
    job_queries = 0

    def query(args, **kwargs):
        nonlocal job_queries
        if args[0] == "systemctl":
            pid = 0 if kind == "wrong_pid" else os.getpid()
            return f"Id={schedule.SERVICE}\nInvocationID={'a' * 32}\nMainPID={pid}\nType=oneshot\n"
        prop = args[-1]
        if prop == "Job":
            job_queries += 1
            value = [15, "/org/freedesktop/systemd1/job/15"]
            if kind == "missing_job" or (kind == "job_changed" and job_queries == 2):
                value = [0, "/"]
            signature = "(uo)"
        elif prop == "Unit":
            value = [schedule.SERVICE, schedule.unit_bus_path(schedule.SERVICE)]
            if kind == "wrong_unit":
                value[0] = "other.service"
            signature = "(so)"
        elif prop in ("JobType", "State"):
            value = "start" if prop == "JobType" else "running"
            if prop == "State" and kind == "waiting_job":
                value = "waiting"
            signature = "s"
        elif prop == "ActivationDetails":
            value = [
                ["trigger_unit", schedule.TIMER],
                ["trigger_timer_realtime_usec", env["TRIGGER_TIMER_REALTIME_USEC"]],
                ["trigger_timer_monotonic_usec", env["TRIGGER_TIMER_MONOTONIC_USEC"]],
            ]
            if kind in ("manual_service", "manual_stale_env"):
                value = []
            elif kind == "wrong_trigger":
                value[0][1] = "other.timer"
            elif kind == "missing_stamps":
                value = value[:1]
            elif kind == "duplicate_details":
                value.append(value[0])
            signature = "a(ss)"
        else:
            value = int(
                env[
                    "TRIGGER_TIMER_REALTIME_USEC"
                    if prop == "LastTriggerUSec"
                    else "TRIGGER_TIMER_MONOTONIC_USEC"
                ]
            )
            if kind == "wrong_stamp":
                value = 1
            signature = "t"
        return json.dumps({"type": signature, "data": value})

    monkeypatch.setattr(schedule.subprocess, "check_output", query)
    context = schedule.service_context()
    assert context["scheduled"] == (kind in ("timer", "stale_env", "missing_env"))
    assert context["service_verified"] == (kind not in ("cli", "wrong_pid"))
    if context["scheduled"]:
        assert context["environment_matches_job"] == (kind == "timer")
        assert context["start_job"]["id"] == 15
        assert context["manager_evidence"]


def test_restoration_crash_adopts_only_matching_finished_proof():
    o, _, _, _ = evidence()
    o["restore"] = {
        "status": "running",
        "snapshot_id": "e" * 64,
        "selected_at": "2026-09-28T05:00:00Z",
        "automatic_attempts": 1,
    }
    s = {
        "restore_checks": [
            {
                "snapshot_id": "f" * 64,
                "ok": True,
                "started_at": "2026-09-28T05:00:01Z",
                "finished_at": "2026-09-28T05:20:00Z",
            }
        ]
    }
    schedule.reconcile_restore(o, s)
    assert o["restore"]["status"] == "interrupted_or_failed"
    s["restore_checks"][0]["snapshot_id"] = "e" * 64
    schedule.reconcile_restore(o, s)
    assert o["restore"]["status"] == "passed" and o["restore"]["automatic_attempts"] == 1


def test_observation_initialization_does_not_reset_previous_evidence(setup_archive):
    p = setup_archive
    o = schedule.start_observation(p)
    assert len(o["excluded_backup_ids"]) == 2 and schedule.progress(o)["completed_dates"] == 0
    before = schedule.observation_path(p).read_bytes()
    with pytest.raises(ValueError, match="do not reset"):
        schedule.start_observation(p)
    assert schedule.observation_path(p).read_bytes() == before


def test_real_wrapper_stores_checks_selects_full_id_and_restores_once(setup_archive, monkeypatch):
    p = setup_archive
    o = schedule.start_observation(p)
    # The fixture's synthetic checkpoints are in 2024; no production clock/manifest is modified.
    o.update(started_at="2023-12-31T00:00:00Z", excluded_backup_ids=[])
    schedule.save(p, o)
    _, context, _, _ = evidence()
    monkeypatch.setattr(schedule, "service_context", lambda: context)
    first = schedule.run(p)
    assert first["ok"] and len(first["counted_dates"]) == 2
    progress = first["progress"]
    assert progress["completed_dates"] == 2 and not progress["complete"]
    assert progress["restore"]["status"] == "passed"
    sid = progress["restore"]["snapshot_id"]
    assert len(sid) == 64 and first["restore"]["result"]["snapshot_id"] == sid
    proof = first["restore"]["result"]
    assert proof["queries"]["ok"] and proof["independent_file_checks"] > 0
    outputs = set(p.restore_work_root.iterdir())
    second = schedule.run(p)
    assert second["counted_dates"] == [] and second["archive"]["result"]["processed"] == 0
    assert second["restore"] is None and set(p.restore_work_root.iterdir()) == outputs


def test_failed_drill_is_not_automatically_repeated(setup_archive, monkeypatch):
    p = setup_archive
    o = schedule.start_observation(p)
    new_o, context, receipt, state = evidence()
    o.update(started_at=new_o["started_at"], excluded_backup_ids=[])
    schedule.save(p, o)
    monkeypatch.setattr(schedule, "service_context", lambda: context)
    monkeypatch.setattr(schedule, "load_state", lambda p: state)
    calls = []

    def fake_execute(p, action, sid=None):
        calls.append((action, sid))
        if action == "restore-check":
            raise OSError("simulated failed extraction")
        return receipt

    monkeypatch.setattr(schedule, "execute", fake_execute)
    with pytest.raises(OSError):
        schedule.run(p)
    assert schedule.load(p)["restore"]["status"] == "failed"
    schedule.run(p)
    assert sum(action == "restore-check" for action, sid in calls) == 1


@pytest.mark.parametrize(
    "reason", ["short_id", "unknown_id", "old_checkpoint", "excluded", "unchecked"]
)
def test_manual_drill_requires_new_checked_snapshot(setup_archive, monkeypatch, reason):
    p = setup_archive
    schedule.start_observation(p)
    o, _, _, state = evidence()
    o["identity"] = p.identity
    schedule.save(p, o)
    sid = "e" * 64
    if reason == "short_id":
        sid = sid[:8]
    elif reason == "unknown_id":
        sid = "f" * 64
    elif reason == "old_checkpoint":
        state["checkpoints"]["c" * 64]["entry"]["checkpoint_at"] = "2026-09-26T00:00:00Z"
    elif reason == "excluded":
        o["excluded_backup_ids"] = ["c" * 64]
        schedule.save(p, o)
    else:
        state["repository_check"]["ok"] = False
    before = schedule.observation_path(p).read_bytes()
    monkeypatch.setattr(schedule, "load_state", lambda p: state)
    with pytest.raises(ValueError):
        schedule.restore_observation(p, sid)
    assert schedule.observation_path(p).read_bytes() == before


def test_explicit_real_manual_drill_never_awards_a_scheduled_date(setup_archive, monkeypatch):
    p = setup_archive
    o = schedule.start_observation(p)
    o.update(started_at="2023-12-31T00:00:00Z", excluded_backup_ids=[])
    schedule.save(p, o)
    stored = schedule.execute(p, "run")
    sid = stored["result"]["stored_snapshots"][0]
    result = schedule.restore_observation(p, sid)
    assert result["ok"] and result["progress"]["completed_dates"] == 0
    restored = result["progress"]["restore"]
    assert restored["status"] == "passed" and restored["snapshot_id"] == sid
    assert restored["manual_attempts"] == 1 and restored["automatic_attempts"] == 0
    assert restored["proof"]["queries"]["ok"] and restored["proof"]["independent_file_checks"] > 0
    before = schedule.observation_path(p).read_bytes()
    with pytest.raises(ValueError, match="already passed"):
        schedule.restore_observation(p, sid)
    assert schedule.observation_path(p).read_bytes() == before
    _, context, _, _ = evidence()
    monkeypatch.setattr(schedule, "service_context", lambda: context)
    outputs = set(p.restore_work_root.iterdir())
    noop = schedule.run(p)
    assert noop["restore"] is None and noop["counted_dates"] == []
    assert set(p.restore_work_root.iterdir()) == outputs
