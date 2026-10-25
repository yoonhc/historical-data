import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from marketdata import backup
from marketdata import checkpoint_evidence as evidence
from marketdata.domain import json_bytes, sha256
from test_checkpoint_schedule import prepared as prepared  # noqa: F401
from test_checkpoint_schedule import setup_archive as setup_archive  # noqa: F401
from test_restic_archive import agent, write_profile
from test_retirement_clock import Samples, provider

from scripts import archive_backups as archive
from scripts import checkpoint_code, render_retirement_units
from scripts import clock_retirement as safe
from scripts import retire_checkpoints as retention
from scripts import retirement_clock as clock


@pytest.fixture
def ready(request, store, tmp_path, monkeypatch):
    p = request.getfixturevalue("setup_archive")
    a = agent(p, "baseline-archive")
    a.run()
    reg = evidence.Registry(
        tmp_path / "t05-state/evidence", p.identity, checkpoint_code.current_code()["code_id"]
    )
    samples = Samples(datetime(2024, 1, 3, 5, tzinfo=UTC).timestamp())

    def observe():
        import uuid

        return clock.Guard(
            lambda: provider(samples), tmp_path / (uuid.uuid4().hex + ".jsonl"), sampler=samples
        ).check(fresh=True)

    entries, _ = archive.discover(p)
    with reg.lock(create=True):
        ids = [evidence.identity(e["path"], backup.load_backup(e["path"])[1]) for e in entries]
        reg.baseline(ids)
        for ident in ids:
            reg.observe(ident, observe)
    previous = p.backup_dir / "2024-01-02/attempt"
    for day in ("2024-01-05", "2024-01-06", "2024-01-07"):
        instant = datetime.fromisoformat(day + "T04:10:00+00:00")
        samples.wall = instant.timestamp()
        with monkeypatch.context() as m:
            m.setattr(backup, "now", lambda: instant)
            store.put_raw(day.encode(), "test:daily", "test")
            target = p.backup_dir / day / "attempt"
            result = backup.create_backup(
                store,
                target,
                min_free_gib=0,
                reuse_from=previous,
                evidence=evidence.Producer(reg, observe),
            )
            assert result["clock_evidence_state"] == "available", result
            previous = target
    a = agent(p, "new-archive")
    assert a.run()["processed"] == 3
    latest = a.state["checkpoints"][result["backup_id"]]
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
    return p, policy, retention.Retirement(policy, reports)


@pytest.fixture
def case(prepared, tmp_path, monkeypatch):
    p, policy, _, _, wall, daily = prepared
    monkeypatch.setattr(safe, "now", lambda: wall[0])
    s = Samples(wall[0].timestamp(), mono=1000000)
    real_guard = clock.Guard
    guards = []

    def guard(query, path):
        value = real_guard(query, path, sampler=s)
        guards.append(value)
        return value

    monkeypatch.setattr(clock, "Guard", guard)
    units = tmp_path / "units"
    units.mkdir()
    for name, content in render_retirement_units.render(
        "/opt/test",
        "/etc/test/settings.toml",
        "/var/lib/test",
        {k: str(v) for k, v in vars(p).items()},
    ).items():
        (units / name).write_text(content)
    binding = tmp_path / "binding.json"
    binding.write_bytes(
        json_bytes(
            {"bootstrap_profile": "/etc/test/bootstrap.json", "bootstrap_profile_sha256": "c" * 64}
        )
    )
    settings = safe.Settings(
        tmp_path / "policy.toml",
        tmp_path / "recurring.json",
        tmp_path / "manual.json",
        binding,
        tmp_path / "t05-state",
        units,
    )
    write_profile(settings.retention_policy, policy)
    settings.authorization_file.write_bytes(json_bytes(safe.proposal(settings)))

    class FakeHost:
        def __init__(self, binding):
            self.binding = binding

        @contextmanager
        def lock(self):
            yield self

        def query(self):
            return provider(s, age=900)

    return p, policy, settings, wall, s, real_guard, guards, FakeHost


def cold(case):
    p, _, settings, _, _, _, _, host = case
    return safe.execute(settings, "preview", host_factory=host)


def seed_old_verified_observations(case):
    # Already witnessed before the three actual later creations; not backdated here.
    p = case[0]
    assert (
        sum(
            bool(r["stored"].get("checkpoint_evidence"))
            for r in archive.load_state(p)["checkpoints"].values()
        )
        == 3
    )


def approve_manual(settings, plan, *, resume=False):
    value = safe.manual_proposal(settings, plan["plan_id"], resume=resume)
    value.update(
        enabled=True,
        approved_at=safe.now(),
        approval_reference="isolated exact-plan approval",
        action="apply-plan",
        grant_kind="exact_plan",
        approved_boot_id=clock.boot_id(),
        resume_interrupted=resume,
        plan_id=plan["plan_id"],
    )
    settings.manual_authorization_file.write_bytes(json_bytes(value))


def allow_test_system_context(monkeypatch):
    # These injected ownership/manager providers belong only to isolated
    # temporary repositories. Real manager provenance is a separate root probe.
    monkeypatch.setattr(clock, "trusted", lambda p, **k: Path(p))
    monkeypatch.setattr(safe, "installed_service", lambda *a: None)
    monkeypatch.setattr(
        safe,
        "system_context",
        lambda s, t: {
            "manager": "system",
            "service_verified": True,
            "scheduled": False,
            "classification": "manual_service_or_unknown",
            "start_job": {"activation_details": {}},
        },
    )


def test_cold_and_warm_preview_preserve_operating_inputs_and_old_grants(case, monkeypatch):
    p, _, settings, _, _, _, _, host = case
    old_state = archive.load_state(p)
    for r in old_state["checkpoints"].values():
        r["stored"].pop("checkpoint_evidence", None)
    p.state_file.write_bytes(json_bytes(old_state))
    before = {str(f): sha256(f.read_bytes()) for f in p.backup_dir.glob("*/*/backup.json")}
    archived = p.state_file.read_bytes()
    first, second = cold(case), cold(case)
    assert first["state"] == second["state"] == "no_candidates", first
    assert first["plan_id"] != second["plan_id"]
    assert (
        len(first["plan"]["keep"]) == 5
        and len(first["plan"]["clock_safety"]["completion_bounds"]) == 5
    )
    assert first["plan"]["clock_safety"]["observation"]["reference_age_seconds"] == 900
    assert all(
        "completion_evidence_missing_or_unarchived" in e["protection"]
        for e in first["plan"]["keep"]
    )
    assert p.state_file.read_bytes() == archived
    assert before == {str(f): sha256(f.read_bytes()) for f in p.backup_dir.glob("*/*/backup.json")}
    assert not first["deletion_attempted"]
    # Shared source must not move T05 command evidence into the archiver's
    # output pool. These are fresh real dumps in the separate T05 state tree.
    receipts = [
        json.loads(path.read_bytes())
        for path in (settings.state_root / "runs").glob("*/commands/*.json")
    ]
    dumps = [
        r
        for r in receipts
        if r.get("arguments", [None])[0] == "dump" and r["arguments"][-1] == "/backup.json"
    ]
    assert dumps
    assert all(r["returncode"] == 0 and "verified_stdout" not in r for r in dumps)
    assert all(Path(r["stdout"]).is_relative_to(settings.state_root) for r in dumps)
    old = safe.proposal(settings)
    old["purpose"] = safe.legacy.PURPOSE
    settings.authorization_file.write_bytes(json_bytes(old))
    allow_test_system_context(monkeypatch)
    denied = safe.execute(settings, "run", host_factory=host)
    assert denied["reason"] == "authorization_scope_changed" and not denied["deletion_attempted"]


def test_manual_approval_deletes_only_proven_candidates_and_preserves_restic(case, monkeypatch):
    p, _, settings, _, _, _, _, host = case
    seed_old_verified_observations(case)
    preview = cold(case)
    assert preview["state"] == "preview_ready", preview
    plan = preview["plan"]
    assert len(plan["candidates"]) == 2 and len(plan["keep"]) == 3
    archived = p.state_file.read_bytes()
    raw = {
        str(f): sha256(f.read_bytes()) for f in (p.store_root / "data").rglob("*") if f.is_file()
    }
    allow_test_system_context(monkeypatch)
    no_approval = safe.execute(settings, "apply-plan", plan["plan_id"], host_factory=host)
    assert no_approval["state"] == "deferred" and not no_approval["deletion_attempted"]
    approve_manual(settings, plan)
    # A one-plan approval cannot become a recurring grant by copying its file.
    recurring = settings.authorization_file.read_bytes()
    settings.authorization_file.write_bytes(settings.manual_authorization_file.read_bytes())
    denied = safe.execute(settings, "run", host_factory=host)
    assert denied["reason"] == "authorization_scope_changed" and not denied["deletion_attempted"]
    settings.authorization_file.write_bytes(recurring)
    events = []
    real_guard = case[5]
    original_check, original_verify = real_guard.check, backup.verify_backup
    original_retire = retention.Retirement.retire_item

    def check(self, **kwargs):
        result = original_check(self, **kwargs)
        if kwargs.get("fresh"):
            events.append("fresh")
        return result

    def verified(path):
        result = original_verify(path)
        events.append("kept_verified")
        return result

    def retiring(self, journal, item, hook):
        def checked(phase, value):
            hook(phase, value)
            if phase == "before_move":
                assert events.index("kept_verified") < len(events) - 1
                assert events[-1] == "fresh", events

        return original_retire(self, journal, item, checked)

    monkeypatch.setattr(real_guard, "check", check)
    monkeypatch.setattr(backup, "verify_backup", verified)
    monkeypatch.setattr(retention.Retirement, "retire_item", retiring)
    result = safe.execute(settings, "apply-plan", plan["plan_id"], host_factory=host)
    assert result["state"] == "complete" and result["deleted_checkpoints"] == 2, result
    assert all(not Path(c["entry"]["path"]).exists() for c in plan["candidates"])
    assert all(backup.verify_backup(e["path"])["ok"] for e in plan["keep"])
    assert raw == {
        str(f): sha256(f.read_bytes()) for f in (p.store_root / "data").rglob("*") if f.is_file()
    }
    assert archived == p.state_file.read_bytes()
    later = cold(case)
    assert later["state"] == "no_candidates", later
    # The new state namespace still reconciles previously deleted checkpoints
    # with the unchanged archive state and the durable new journals.
    assert len(later["plan"]["keep"]) == 3
    repeated = safe.execute(settings, "apply-plan", plan["plan_id"], host_factory=host)
    assert repeated["state"] == "complete" and repeated["result"]["already_complete"], repeated
    assert repeated["already_complete"] and repeated["deleted_this_run"] == 0
    assert result["deleted_this_run"] == 2
    monkeypatch.setattr(real_guard, "check", original_check)
    monkeypatch.setattr(backup, "verify_backup", original_verify)
    monkeypatch.setattr(retention.Retirement, "retire_item", original_retire)

    # The old local copies are truly gone. Hide the ENTIRE original control
    # state (registry, plans, journals and observations), then restore evidence
    # from one explicit, fully checked repository snapshot.
    hidden = settings.state_root.with_name("control-unavailable")
    settings.state_root.rename(hidden)
    latest = max(
        archive.load_state(p)["checkpoints"].values(), key=lambda r: r["entry"]["checkpoint_at"]
    )
    sid = latest["stored"]["snapshot_id"]
    restored = p.restore_work_root / "proof-without-control"
    a = agent(p, "proof-reconstruction")
    a.restic.command(["restore", sid, "--target", str(restored), "--verify"])
    bundle = evidence.read_bundle(restored, backup.load_backup(restored)[1])
    graph = evidence.Graph(bundle["bundle"]["objects"], p.identity)
    original_entries = [c["entry"] for c in plan["candidates"]] + plan["keep"]
    keep, candidates, proofs = evidence.select(
        original_entries, graph, plan["clock_safety"]["observation"]["lower_utc_ns"]
    )
    assert {e["backup_id"] for e in candidates} == {
        c["entry"]["backup_id"] for c in plan["candidates"]
    }
    assert proofs == plan["clock_safety"]["completion_bounds"]
    next_keep, next_candidates, _ = evidence.select(
        keep, graph, plan["clock_safety"]["observation"]["lower_utc_ns"]
    )
    assert len(next_keep) == 3 and not next_candidates and not settings.state_root.exists()
    receipt = backup.restore_backup(restored, p.restore_work_root / "proof-store", min_free_gib=0)
    kept = Path(receipt["path"]) / receipt["checkpoint_evidence"]["path"]
    assert evidence.decode_bundle(kept.read_bytes())["sha256"] == bundle["sha256"]
    assert not receipt["checkpoint_evidence"]["deletion_authority_restored"]


def test_mid_deletion_clock_fault_leaves_journal_and_blocks_new_plan(case, monkeypatch):
    p, _, settings, _, samples, _, _, host = case
    seed_old_verified_observations(case)
    plan = cold(case)["plan"]
    approve_manual(settings, plan)
    allow_test_system_context(monkeypatch)
    original = retention.Retirement.retire_item

    def inject(self, journal, item, hook):
        def fault(phase, target):
            hook(phase, target)
            if phase == "after_move":
                samples.wall += 41

        return original(self, journal, item, fault)

    monkeypatch.setattr(retention.Retirement, "retire_item", inject)
    result = safe.execute(settings, "apply-plan", plan["plan_id"], host_factory=host)
    assert result["state"] == "failed" and result["unfinished_retirement"], result
    journal = json.loads(Path(result["retirement_journal"]).read_bytes())
    assert sum(i["unlinked_files"] for i in journal["items"].values()) == 0
    next_run = cold(case)
    assert next_run["state"] == "deferred" and next_run["reason"] == "unfinished_retirement", (
        next_run
    )
    assert next_run["needs_attention"]
    # Reviewing the existing plan and issuing an explicit recovery approval
    # permits only that plan's remainder; old records are never reset/deleted.
    monkeypatch.setattr(retention.Retirement, "retire_item", original)
    approve_manual(settings, plan, resume=True)
    resumed = safe.execute(settings, "apply-plan", plan["plan_id"], host_factory=host)
    assert resumed["state"] == "complete", resumed
    new_journal = json.loads(Path(resumed["retirement_journal"]).read_bytes())
    assert len(new_journal["attempts"]) == 2
    assert not new_journal["attempts"][0]["ok"] and new_journal["attempts"][1]["ok"]


def test_changed_completion_evidence_and_targets_refuse_application(case, monkeypatch):
    p, _, settings, _, _, _, _, host = case
    seed_old_verified_observations(case)
    plan = cold(case)["plan"]
    approve_manual(settings, plan)
    allow_test_system_context(monkeypatch)
    path = p.state_file
    original = path.read_bytes()
    bad = json.loads(original)
    entry = next(r for r in bad["checkpoints"].values() if r["stored"].get("checkpoint_evidence"))
    entry["stored"]["checkpoint_evidence"]["sha256"] = "0" * 64
    path.write_bytes(json_bytes(bad))
    result = safe.execute(settings, "apply-plan", plan["plan_id"], host_factory=host)
    assert result["state"] == "failed" and not result["deletion_attempted"], result
    path.write_bytes(original)
    value = json.loads(settings.manual_authorization_file.read_bytes())
    value["plan_id"] = "f" * 64
    settings.manual_authorization_file.write_bytes(json_bytes(value))
    result = safe.execute(settings, "apply-plan", plan["plan_id"], host_factory=host)
    assert result["state"] == "failed" and not result["deletion_attempted"], result
