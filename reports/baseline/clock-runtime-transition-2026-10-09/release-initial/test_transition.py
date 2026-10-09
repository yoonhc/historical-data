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
    "transition", Path(__file__).with_name("work") / "collector_transition.py"
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
    previous = Path(__file__).with_name("fixtures") / "deployment"
    state = tmp_path / "state"
    state.mkdir()
    paths = {r: tmp_path / (r + ".json") for r in m.ROLES}
    runtime = json.loads((previous / "runtime.before").read_text())
    boot = json.loads((previous / "bootstrap.before").read_text())
    old_unit = (
        (previous / "collector_unit.before")
        .read_text()
        .replace(boot["runtime_profile"], str(paths["runtime"]))
        .replace("/etc/marketdata/clock-bootstrap.json", str(paths["bootstrap"]))
    )
    old_r = m.encode(runtime).decode()
    boot["runtime_profile"] = str(paths["runtime"])
    boot["state_dir"] = str(state)
    boot["runtime_profile_sha256"] = m.sha(old_r.encode())
    del boot["guard_files"]["/etc/systemd/system/marketdata-collector@.service"]
    boot["guard_files"][str(paths["collector_unit"])] = m.sha(old_unit.encode())
    before = {"collector_unit": old_unit, "runtime": old_r, "bootstrap": m.encode(boot).decode()}
    new_runtime = {
        **runtime,
        "release_root": "/opt/marketdata/releases/" + m.pins()["after"]["release_id"],
    }
    new_r = m.encode(new_runtime).decode()
    new_unit = m.expected_unit(
        old_unit, runtime["release_root"], new_runtime["release_root"], str(paths["bootstrap"])
    )
    new_boot = copy.deepcopy(boot)
    new_boot["runtime_profile_sha256"] = m.sha(new_r.encode())
    new_boot["guard_files"][str(paths["collector_unit"])] = m.sha(new_unit.encode())
    after = {"collector_unit": new_unit, "runtime": new_r, "bootstrap": m.encode(new_boot).decode()}
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
        "step_observation": {"raw": "preserved"},
        "configuration_history": [{"plan_id": "older-approved-change", "direction": "apply"}],
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
        "kind": "collector_runtime_transition",
        "files": files,
        "boot_id": "boot-A",
        "recovery_policy": "same_boot_only_other_boot_hold_for_review",
        "state_dir": str(state),
        "ledger_before": ledger,
        "ledger_before_sha256": m.gate.canonical_hash(ledger),
        "tools_sha256": m.tool_hashes(),
        "release_pins": m.pins(),
    }
    plan["plan_id"] = m.gate.canonical_hash(plan)
    approval = {
        "schema": 1,
        "approved": True,
        "plan_id": plan["plan_id"],
        "approved_by": "isolated-test-only",
        "approval_reference": "not an operating approval",
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
        if k not in {"profile_sha256", "configuration_history"}:
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
        "file_collector_unit",
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
        "file_collector_unit",
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
    assert state["configuration_history"][:1] == plan["ledger_before"]["configuration_history"]
    assert [r["direction"] for r in state["configuration_history"][-2:]] == ["apply", "rollback"]
    assert Path(tr.directory / "apply-receipt.json").exists()


@pytest.mark.parametrize(
    "change",
    [
        "clock_policy",
        "source",
        "release",
        "unit_protection",
        "unit_timeout",
        "ledger",
        "hash",
        "tools",
        "path",
        "scope",
    ],
)
def test_scope_tampering_rejected_before_writes(setup, change):
    plan, approval, host = setup
    original = {r: Path(f["path"]).read_bytes() for r, f in plan["files"].items()}
    if change in ("clock_policy", "source", "release"):
        value = json.loads(plan["files"]["runtime"]["after"])
        if change == "clock_policy":
            value["policy"]["max_reference_age_seconds"] = 3600
        if change == "source":
            value["chrony_config_sha256"] = "different-source"
        if change == "release":
            value["release_root"] = "/opt/unreviewed"
        plan["files"]["runtime"]["after"] = m.encode(value).decode()
    elif change.startswith("unit_"):
        f = plan["files"]["collector_unit"]
        f["after"] = (
            f["after"].replace("ProtectClock=yes", "ProtectClock=no")
            if change == "unit_protection"
            else f["after"].replace("330s", "360s")
        )
    elif change == "ledger":
        plan["ledger_before"]["step_attempts"] = 0
    elif change == "tools":
        plan["tools_sha256"]["clock_gate.py"] = "bad"
    elif change == "path":
        plan["files"]["runtime"]["path"] = "/etc/unapproved"
    elif change == "scope":
        plan["kind"] = "chrony_source_configuration"
    else:
        plan["files"]["bootstrap"]["after_sha256"] = "bad"
    if change != "hash":
        for f in plan["files"].values():
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
    path = Path(plan["files"]["collector_unit"]["path"])
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


@pytest.mark.parametrize("action", ["apply", "rollback", "commit"])
def test_any_changed_boot_holds_without_reopening_or_overwriting(setup, action):
    plan, _, host = setup
    tr = tx(setup)
    tr.stage()
    raw = tr.ledger.path.read_bytes()
    tr.boot = lambda: "boot-B"
    with pytest.raises(m.Hold, match="Boot changed"):
        tr.commit() if action == "commit" else tr.stage(action)
    assert tr.ledger.path.read_bytes() == raw
    assert_gate_holds(plan)


def test_corrupt_commit_receipt_never_reused(setup):
    plan, _, host = setup
    tr = tx(setup)
    tr.stage()
    host.active = "apply"
    tr.commit()
    path = tr.directory / "apply-receipt.json"
    value = json.loads(path.read_text())
    value["clock_at_commit"]["ready"] = True
    path.write_bytes(m.encode(value))
    with pytest.raises(m.Hold, match="receipt content/hash"):
        tr.commit()


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


def effective_unit(plan, side, stream):
    boot = json.loads(plan["files"]["bootstrap"][side])
    runtime = json.loads(plan["files"]["runtime"][side])
    text = plan["files"]["collector_unit"][side].replace("%i", stream)
    starts = [line.split("=", 1)[1] for line in text.splitlines() if line.startswith("ExecStart=")]
    pres = [line.split("=", 1)[1] for line in text.splitlines() if line.startswith("ExecStartPre=")]
    return {
        "LoadState": "loaded",
        "NeedDaemonReload": "no",
        "DropInPaths": "",
        "FragmentPath": plan["files"]["collector_unit"]["path"],
        "ExecStart": " ".join("{ argv[]=" + s + " ; }" for s in starts),
        "ExecStartPre": " ".join("{ argv[]=" + s + " ; }" for s in pres),
        "User": boot["user"],
        "WorkingDirectory": runtime["release_root"],
        "RuntimeMaxUSec": "infinity",
        "Restart": "always",
        "KillMode": "mixed",
        "KillSignal": "2",
        "TimeoutStopUSec": "5min 30s" if side == "after" else "2min 30s",
        "NoNewPrivileges": "yes",
        "ProtectClock": "yes",
        "ProtectSystem": "strict",
        "ProtectHome": "read-only",
        "CapabilityBoundingSet": "",
        "PrivateTmp": "yes",
        "ReadWritePaths": runtime["data_root"] + " " + runtime["evidence_root"],
        "RestrictAddressFamilies": "AF_INET AF_INET6 AF_UNIX AF_NETLINK",
    }


@pytest.mark.parametrize("direction", ["apply", "rollback"])
def test_live_activation_adapter_separates_readiness_and_immutable_provider(
    setup, monkeypatch, direction
):
    plan, _, _ = setup
    side = "after" if direction == "apply" else "before"
    host = m.LiveHost(plan)
    provider = {"pid": 10, "invocation_id": "unchanged"}
    monkeypatch.setattr(host, "snapshot", lambda: {"provider": provider})
    monkeypatch.setattr(
        host, "unit", lambda name: effective_unit(plan, side, name.split("@")[1].split(".")[0])
    )
    rid = m.pins()[side]["release_id"]
    monkeypatch.setattr(m, "check_release", lambda r, s: {"release_id": rid})
    commands = []

    def command(argv):
        commands.append(argv)
        assert (
            argv[0] == "/usr/sbin/runuser"
            and "-I" in argv
            and "environment_check" in argv[argv.index("-c") + 1]
        )
        return json.dumps({"release_id": rid})

    monkeypatch.setattr(m.rt, "command", command)
    monkeypatch.setattr(
        m.rt,
        "probe",
        lambda _: {"ready": False, "reasons": ["reference_age"], "provider": provider},
    )
    record = {"baseline_provider": {"provider": provider}}
    result = host.activation(record, direction)
    assert not result["clock_at_commit"]["ready"] and len(commands) == 1
    with pytest.raises(m.Hold, match="Provider changed"):
        host.activation({"baseline_provider": {"provider": {"pid": 11}}}, direction)


@pytest.mark.parametrize(
    "field,value",
    [
        ("RuntimeMaxUSec", "1h"),
        ("KillMode", "control-group"),
        ("TimeoutStopUSec", "2min 30s"),
        ("NoNewPrivileges", "no"),
        ("ProtectSystem", "full"),
        ("NeedDaemonReload", "yes"),
        ("DropInPaths", "/unexpected.conf"),
        ("ReadWritePaths", "/"),
        ("ExecStartPre", ""),
        ("ExecStart", "old executable"),
        ("RestrictAddressFamilies", "AF_UNIX"),
    ],
)
def test_live_activation_adapter_rejects_effective_configuration_drift(
    setup, monkeypatch, field, value
):
    plan, _, _ = setup
    host = m.LiveHost(plan)
    monkeypatch.setattr(host, "snapshot", lambda: {"provider": {}})
    monkeypatch.setattr(m, "check_release", lambda *a: {})

    def unit(name):
        result = effective_unit(plan, "after", name.split("@")[1].split(".")[0])
        result[field] = value
        return result

    monkeypatch.setattr(host, "unit", unit)
    with pytest.raises(m.Hold):
        host.activation({"baseline_provider": {"provider": {}}}, "apply")


def test_release_receipt_cannot_omit_installed_module(tmp_path, monkeypatch):
    root = tmp_path / "release"
    root.mkdir()
    sources = {
        "packages/marketdata/src/marketdata/__init__.py": b"module",
        "integrations/runtime/capture_runtime.py": b"runtime",
    }
    hashes = {}
    for name, blob in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        hashes[name] = m.sha(blob)
    installed = root / ".venv/lib/python3.12/site-packages/marketdata/__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"module")
    (root / "candidate.json").write_bytes(b"{}")
    pin = {
        "release_id": "fixture",
        "candidate_json_sha256": m.sha(b"{}"),
        "source_files_sha256": hashes,
    }
    monkeypatch.setattr(m, "pins", lambda: {"after": pin})
    monkeypatch.setattr(m.gate, "trusted", fixture_trust)
    manifest = {
        "release_id": "fixture",
        "editable": False,
        "runtime_files_sha256": {
            str(installed.relative_to(root)): m.sha(b"module"),
            "integrations/runtime/capture_runtime.py": m.sha(b"runtime"),
        },
    }
    (root / "runtime-manifest.json").write_bytes(m.encode(manifest))
    assert m.check_release({"release_root": str(root)}, "after") == manifest
    manifest["runtime_files_sha256"].pop("integrations/runtime/capture_runtime.py")
    (root / "runtime-manifest.json").write_bytes(m.encode(manifest))
    with pytest.raises(m.Hold, match="omits"):
        m.check_release({"release_root": str(root)}, "after")
