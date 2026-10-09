"""Real file/flock/SIGKILL tests; provider/service access is an explicit test double."""

import copy
import importlib.util
import json
import multiprocessing
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "transition", Path(__file__).with_name("work") / "clock_config_transition.py"
)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def fixture_trust(path, **_):
    # Fixtures belong to the real non-root test user. Production CLI has no bypass.
    return Path(path)


class Host:
    def __init__(self):
        self.active = "original"
        self.ready = False
        self.busy = False

    def audit(self):
        if self.busy:
            raise m.Hold("business job active")

    def snapshot(self):
        return {
            "provider": {"invocation_id": "original", "pid": 1, "start_boottime": 1},
            "source_addresses": ["185.125.190.58", "91.189.91.157"],
        }

    def activation(self, record, direction):
        if self.active != direction:
            raise m.Hold("activation not yet verified")
        return {
            "activation": {"provider": {"invocation_id": direction}},
            "clock_at_commit": {
                "ready": self.ready,
                "reasons": [] if self.ready else ["reference_age"],
            },
        }


@pytest.fixture
def setup(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    paths = {r: tmp_path / (r + ".json" if r != "chrony" else "chrony.conf") for r in m.ROLES}
    old_c = "pool ntp.ubuntu.com iburst maxsources 4 minpoll 6 maxpoll 8\nrtcsync\nleapsecmode slew\nport 0\n"
    new_c = (
        "".join(
            f"server {ip} iburst minpoll 6 maxpoll 8\n"
            for ip in ("162.159.200.1", "133.243.238.163", "194.58.200.20", "192.53.103.108")
        )
        + "rtcsync\nleapsecmode slew\nport 0\n"
    )
    runtime = {
        "chronyc": "/usr/bin/chronyc",
        "chrony_config": str(paths["chrony"]),
        "chrony_config_sha256": m.sha(old_c.encode()),
        "policy": {"max_reference_age_seconds": 600},
        "release_root": "/opt/unchanged",
    }
    old_r = m.encode(runtime).decode()
    boot = {
        "runtime_profile": str(paths["runtime"]),
        "runtime_profile_sha256": m.sha(old_r.encode()),
        "state_dir": str(state),
        "policy": {"step_above_seconds": 0.1},
        "guard_files": {"fixed": "unchanged"},
    }
    before = {"chrony": old_c, "runtime": old_r, "bootstrap": m.encode(boot).decode()}
    new_r = m.encode({**runtime, "chrony_config_sha256": m.sha(new_c.encode())}).decode()
    new_b = m.encode({**boot, "runtime_profile_sha256": m.sha(new_r.encode())}).decode()
    after = {"chrony": new_c, "runtime": new_r, "bootstrap": new_b}
    ledger = {
        "schema": 1,
        "phase": "closed",
        "boot_id": "boot-A",
        "profile_sha256": m.gate.canonical_hash(boot),
        "step_attempts": 1,
        "step_result": "unknown",
        "first_boottime": 17.5,
        "closed_boottime": 22.5,
        "closed_observation": {"ready": True, "provider": "original"},
        "step_observation": {"raw": "old-step-evidence"},
    }
    m.atomic(state / "boot-state.json", m.encode(ledger))
    files = {}
    for role, path in paths.items():
        m.atomic(path, before[role].encode())
        files[role] = {
            "path": str(path),
            "uid": 0,
            "mode": 0o644,
            "before": before[role],
            "after": after[role],
            "before_sha256": m.sha(before[role].encode()),
            "after_sha256": m.sha(after[role].encode()),
        }
    plan = {
        "schema": 1,
        "kind": "chrony_source_configuration",
        "files": files,
        "boot_id": "boot-A",
        "recovery_policy": "new_boot_rollback_only_with_bootstrap_stopped_and_disabled",
        "state_dir": str(state),
        "ledger_before": ledger,
        "ledger_before_sha256": m.gate.canonical_hash(ledger),
        "tools_sha256": m.tool_hashes(),
        "chronyd_sha256": "fixture",
        "source_review_sha256": "fixture",
        "new_source_addresses": m.sources_and_body(new_c, candidate=True)[0],
    }
    plan["plan_id"] = m.gate.canonical_hash(plan)
    approval = {
        "schema": 1,
        "approved": True,
        "plan_id": plan["plan_id"],
        "approved_by": "isolated-test-only",
        "approval_reference": "synthetic fixture, not live authorization",
        "approved_at": "2026-10-09T00:00:00Z",
        "actions": ["stage", "commit", "rollback"],
    }
    return plan, approval, Host()


def tx(setup, **kw):
    plan, approval, host = setup
    return m.Transition(plan, approval, host, boot=lambda: "boot-A", trust=fixture_trust, **kw)


def assert_gate_holds(plan):
    ledger = m.gate.Ledger(plan["state_dir"])
    for side in ("before", "after"):
        p = json.loads(plan["files"]["bootstrap"][side])
        with pytest.raises(m.Hold):
            m.gate.check_gate(
                p, {}, ledger=ledger, checker=lambda _: {"ready": True}, current_boot="boot-A"
            )
    # Existing bootstrap cannot turn the configuration transition into step permission,
    # even on a different boot. The real legacy reader rejects schema 2 first.
    with pytest.raises(m.Hold):
        m.gate.attempt(p, {}, ledger, None, current_boot="boot-B", boottime=0)


def assert_preserved(plan, state):
    for k, v in plan["ledger_before"].items():
        if k != "profile_sha256":
            assert state[k] == v


def test_approval_config_activation_and_current_readiness_are_separate(setup):
    plan, _, host = setup
    tr = tx(setup)
    assert tr.stage()["work_allowed"] is False
    assert_gate_holds(plan)
    with pytest.raises(m.Hold, match="activation"):
        tr.commit()
    host.active = "apply"
    result = tr.commit()
    assert result["current_clock_ready"] is False
    new = m.gate.Ledger(plan["state_dir"]).read()
    assert_preserved(plan, new)
    p = json.loads(plan["files"]["bootstrap"]["after"])
    with pytest.raises(m.Hold, match="Current clock"):
        m.gate.check_gate(
            p,
            {},
            ledger=tr.ledger,
            checker=lambda _: {"ready": False, "reasons": ["reference_age"]},
            current_boot="boot-A",
        )
    assert m.gate.check_gate(
        p, {}, ledger=tr.ledger, checker=lambda _: {"ready": True}, current_boot="boot-A"
    )["ready"]
    raw = tr.ledger.path.read_bytes()
    assert tr.commit()["idempotent"]
    assert tr.ledger.path.read_bytes() == raw

    class NeverStep:
        def step(self, *_):
            pytest.fail("step reopened")

    assert not m.gate.attempt(p, {}, tr.ledger, NeverStep(), current_boot="boot-A")["step_reopened"]


def killed_child(setup, point, action):
    def fault(p):
        if p == point:
            os.kill(os.getpid(), signal.SIGKILL)

    tr = tx(setup, fault=fault)
    if action == "commit":
        tr.commit()
    else:
        tr.stage(action)


@pytest.mark.parametrize(
    "point",
    [
        "prepared",
        "pending",
        "file_chrony",
        "file_runtime",
        "file_bootstrap",
        "files_staged",
        "receipt",
        "committed",
    ],
)
@pytest.mark.parametrize("recovery", ["resume", "rollback"])
def test_sigkill_boundary_recovery_is_based_on_actual_files(setup, point, recovery):
    plan, _, host = setup
    tr = tx(setup)
    action = "apply"
    if point in {"receipt", "committed"}:
        tr.stage()
        host.active = "apply"
        action = "commit"
    child = multiprocessing.get_context("fork").Process(
        target=killed_child, args=(setup, point, action)
    )
    child.start()
    child.join(10)
    assert child.exitcode == -signal.SIGKILL
    if point not in {"prepared", "committed"}:
        assert_gate_holds(plan)
    if recovery == "resume":
        tr.stage()
        host.active = "apply"
        tr.commit()
        target = "after"
    else:
        tr.stage("rollback")
        assert_gate_holds(plan)
        host.active = "rollback"
        tr.commit()
        target = "before"
    for f in plan["files"].values():
        assert Path(f["path"]).read_text() == f[target]
    assert_preserved(plan, tr.ledger.read())
    assert tr.commit()["idempotent"]


@pytest.mark.parametrize(
    "point",
    [
        "rollback_intent",
        "pending",
        "file_chrony",
        "file_runtime",
        "file_bootstrap",
        "files_staged",
        "receipt",
        "committed",
    ],
)
def test_rollback_itself_survives_sigkill_without_losing_apply_history(setup, point):
    plan, _, host = setup
    tr = tx(setup)
    tr.stage()
    host.active = "apply"
    tr.commit()
    action = "rollback"
    if point in {"receipt", "committed"}:
        tr.stage("rollback")
        host.active = "rollback"
        action = "commit"
    child = multiprocessing.get_context("fork").Process(
        target=killed_child, args=(setup, point, action)
    )
    child.start()
    child.join(10)
    assert child.exitcode == -signal.SIGKILL
    tr.stage("rollback")
    host.active = "rollback"
    tr.commit()
    state = tr.ledger.read()
    assert_preserved(plan, state)
    assert [r["direction"] for r in state["configuration_history"]] == ["apply", "rollback"]
    assert Path(tr.directory / "apply-receipt.json").exists()


@pytest.mark.parametrize("change", ["clock_policy", "source", "rtc", "hash", "tools", "path"])
def test_scope_tampering_is_rejected_before_writes(setup, change):
    plan, approval, host = setup
    original = {r: Path(f["path"]).read_bytes() for r, f in plan["files"].items()}
    if change == "clock_policy":
        value = json.loads(plan["files"]["runtime"]["after"])
        value["policy"]["max_reference_age_seconds"] = 3600
        plan["files"]["runtime"]["after"] = m.encode(value).decode()
    elif change == "source":
        plan["files"]["chrony"]["after"] = plan["files"]["chrony"]["after"].replace(
            "162.159.200.1", "91.189.91.157"
        )
    elif change == "rtc":
        plan["files"]["chrony"]["after"] += "makestep 1 3\n"
    elif change == "tools":
        plan["tools_sha256"]["clock_gate.py"] = "bad"
    elif change == "path":
        plan["files"]["runtime"]["path"] = "/etc/unapproved"
    else:
        plan["files"]["bootstrap"]["after_sha256"] = "bad"
    for f in plan["files"].values():
        if change != "hash":
            f["after_sha256"] = m.sha(f["after"].encode())
    plan["plan_id"] = m.gate.canonical_hash({k: v for k, v in plan.items() if k != "plan_id"})
    approval["plan_id"] = plan["plan_id"]
    with pytest.raises((m.Hold, ValueError)):
        tx(setup).stage()
    for r, f in plan["files"].items():
        if change != "path" or r != "runtime":
            assert Path(f["path"]).read_bytes() == original[r]


def test_lock_unknown_file_old_approval_busy_and_changed_boot_hold(setup):
    plan, approval, host = setup
    tr = tx(setup)
    with tr.ledger.lock():
        with pytest.raises(m.Hold, match="lock"):
            tr.stage()
    host.busy = True
    with pytest.raises(m.Hold, match="active"):
        tr.stage()
    host.busy = False
    old = copy.deepcopy(approval)
    approval["approved"] = False
    with pytest.raises(m.Hold, match="approval"):
        tx(setup)
    approval.update(old)
    tr.stage()
    tr.boot = lambda: "boot-B"
    with pytest.raises(m.Hold, match="Boot changed"):
        tr.stage()
    assert_gate_holds(plan)
    tr.boot = lambda: "boot-A"
    path = Path(plan["files"]["chrony"]["path"])
    path.write_text("unexpected administrator edit\n")
    with pytest.raises(m.Hold, match="Unapproved file"):
        tr.stage("rollback")
    assert path.read_text() == "unexpected administrator edit\n"


def test_real_cli_refuses_non_root_apply(setup, tmp_path):
    plan, approval, _ = setup
    p, a = tmp_path / "plan.json", tmp_path / "approval.json"
    p.write_bytes(m.encode(plan))
    a.write_bytes(m.encode(approval))
    before = Path(plan["state_dir"], "boot-state.json").read_bytes()
    run = subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            str(Path(m.__file__)),
            "stage",
            "--plan",
            str(p),
            "--approval",
            str(a),
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 75
    assert "root operator" in run.stderr
    assert Path(plan["state_dir"], "boot-state.json").read_bytes() == before


def test_new_boot_allows_only_rollback_preserving_original_boot_and_evidence(setup):
    plan, _, host = setup
    tr = tx(setup)
    tr.stage()
    tr.boot = lambda: "boot-B"
    with pytest.raises(m.Hold, match="Boot changed"):
        tr.commit()
    tr.stage("rollback")
    host.active, host.ready = "rollback", True
    result = tr.commit()
    assert result["current_clock_ready"] is True
    assert result["current_boot_preparation_preserved"] is False
    assert_preserved(plan, tr.ledger.read())
    with pytest.raises(m.Hold, match="This boot"):
        m.gate.check_gate(
            tr.before_p,
            {},
            ledger=tr.ledger,
            checker=lambda _: {"ready": True},
            current_boot="boot-B",
        )


def test_corrupt_commit_receipt_and_boot_change_after_rollback_stage_hold(setup):
    plan, _, host = setup
    tr = tx(setup)
    tr.stage()
    tr.stage("rollback")
    tr.boot = lambda: "boot-B"
    with pytest.raises(m.Hold, match="Boot changed after staging"):
        tr.commit()
    tr.stage("rollback")
    host.active = "rollback"
    tr.commit()
    receipt = tr.directory / "rollback-receipt.json"
    value = json.loads(receipt.read_text())
    value["clock_at_commit"]["ready"] = True
    receipt.write_bytes(m.encode(value))
    with pytest.raises(m.Hold, match="receipt content/hash"):
        tr.commit()


@pytest.mark.parametrize(
    "case",
    [
        "good",
        "same_process",
        "early_process",
        "wrong_sources",
        "provider_changed",
        "wrong_path",
        "wrong_binary",
    ],
)
def test_live_activation_adapter_requires_actual_new_daemon_and_source_inventory(
    setup, monkeypatch, case
):
    plan, _, _ = setup
    tr = tx(setup)
    tr.stage()
    live = m.LiveHost(plan)
    provider = {
        "pid": 22,
        "invocation_id": "new",
        "argv": ["/usr/sbin/chronyd", "-f", plan["files"]["chrony"]["path"]],
    }
    original_provider = {**provider, "pid": 1, "invocation_id": "old", "start_boottime": 1}
    record = {
        "baseline_provider": {"provider": original_provider, "source_addresses": ["91.189.91.157"]},
        "files_verified_boottime": 10,
    }
    start = 11
    addresses = plan["new_source_addresses"]
    if case == "same_process":
        provider = {k: v for k, v in original_provider.items() if k != "start_boottime"}
        start = 1
    if case == "early_process":
        start = 9
    if case == "wrong_sources":
        addresses = ["91.189.91.157"]
    if case == "wrong_path":
        provider["argv"][-1] = "/etc/chrony/chrony.conf"
    monkeypatch.setattr(m.rt, "provider_identity", lambda: copy.deepcopy(provider))
    monkeypatch.setattr(m.rt, "process_start", lambda _: str(int(start * os.sysconf("SC_CLK_TCK"))))
    monkeypatch.setattr(
        m.rt,
        "command",
        lambda argv: (
            argv[-1] + "\n"
            if "sourcename" in argv
            else "".join(f"^,+,{ip},1,8,377,12,0,0,0.01\n" for ip in addresses)
        ),
    )
    original_digest = m.rt.digest
    monkeypatch.setattr(
        m.rt,
        "digest",
        lambda p: (
            ("changed" if case == "wrong_binary" else "fixture")
            if str(p) == "/usr/sbin/chronyd"
            else original_digest(p)
        ),
    )
    observed = copy.deepcopy(provider)
    if case == "provider_changed":
        observed["invocation_id"] = "another"
    monkeypatch.setattr(
        m.rt,
        "probe",
        lambda _: {"provider": observed, "ready": False, "reasons": ["reference_age"]},
    )
    if case == "good":
        assert live.activation(record, "apply")["clock_at_commit"]["ready"] is False
    else:
        with pytest.raises(m.Hold):
            live.activation(record, "apply")


def test_live_audit_requires_bootstrap_stopped_disabled_for_new_boot_recovery(setup, monkeypatch):
    plan, _, _ = setup
    live = m.LiveHost(plan)
    monkeypatch.setattr(m.gate.Host, "audit_quiescent", lambda _: None)
    monkeypatch.setattr(m.rt, "boot_id", lambda: "boot-B")
    monkeypatch.setattr(
        m.rt,
        "command",
        lambda _: "MainPID=0\nControlPID=0\nLoadState=loaded\nUnitFileState=enabled\n",
    )
    with pytest.raises(m.Hold, match="bootstrap stopped and disabled"):
        live.audit()
    monkeypatch.setattr(
        m.rt,
        "command",
        lambda _: (
            "MainPID=0\nControlPID=0\nLoadState=loaded\nUnitFileState=disabled\nActiveState=inactive\n"
        ),
    )
    live.audit()


def test_failed_fsync_before_pending_cannot_change_files_or_old_gate(setup, monkeypatch):
    plan, _, _ = setup
    tr = tx(setup)
    old = tr.ledger.path.read_bytes()

    def fail(_):
        raise OSError("disk full at fsync")

    monkeypatch.setattr(m.os, "fsync", fail)
    with pytest.raises(OSError):
        tr.stage()
    assert tr.ledger.path.read_bytes() == old
    for f in plan["files"].values():
        assert Path(f["path"]).read_text() == f["before"]


def test_progress_corruption_never_silently_resumes(setup):
    tr = tx(setup)
    tr.stage()
    path = tr.directory / "progress.json"
    value = json.loads(path.read_text())
    value["files_verified_boottime"] = 0
    path.write_bytes(m.encode(value))
    with pytest.raises(m.Hold, match="progress content/hash"):
        tr.stage()
    assert_gate_holds(setup[0])


@pytest.mark.parametrize("name,allowed", [("ntp.ubuntu.com", True), ("unapproved.example", False)])
def test_rollback_verifies_original_association_not_past_dns_answer(
    setup, monkeypatch, name, allowed
):
    plan, _, _ = setup
    live = m.LiveHost(plan)
    provider = {
        "pid": 22,
        "invocation_id": "rollback",
        "start_boottime": 11,
        "argv": ["/usr/sbin/chronyd", "-f", plan["files"]["chrony"]["path"]],
    }
    monkeypatch.setattr(
        live,
        "snapshot",
        lambda: {
            "provider": provider,
            "source_addresses": ["185.125.190.56"],
            "source_names": {"185.125.190.56": name},
        },
    )
    monkeypatch.setattr(m.rt, "digest", lambda _: "fixture")
    monkeypatch.setattr(m.rt, "probe", lambda _: {"provider": provider, "ready": False})
    record = {"baseline_provider": {"provider": {"pid": 1}}, "files_verified_boottime": 10}
    if allowed:
        assert live.activation(record, "rollback")["clock_at_commit"]["ready"] is False
    else:
        with pytest.raises(m.Hold, match="original configuration"):
            live.activation(record, "rollback")


def test_new_boot_recovery_preserves_guarded_unit_instead_of_masking_it(
    setup, tmp_path, monkeypatch
):
    plan, _, _ = setup
    unit = tmp_path / "bootstrap.service"
    unit.write_text("[Service]\nExecStart=/opt/unchanged/bootstrap\n")
    p = {"guard_files": {str(unit): m.rt.digest(unit)}, "user_units": {}, "system_collectors": []}
    live = m.LiveHost(plan)
    live.before_p = p
    monkeypatch.setattr(m.gate, "trusted", fixture_trust)
    monkeypatch.setattr(m.gate.Host, "user_command", lambda *args: "")
    monkeypatch.setattr(m.rt, "boot_id", lambda: "boot-B")
    monkeypatch.setattr(
        m.rt,
        "command",
        lambda _: (
            "MainPID=0\nControlPID=0\nLoadState=loaded\nUnitFileState=disabled\nActiveState=inactive\n"
        ),
    )
    live.audit()  # Uses the real audit_quiescent guard-file check.
    unit.write_text("MASKED OR REPLACED")
    with pytest.raises(m.Hold, match="Guard file changed"):
        live.audit()
