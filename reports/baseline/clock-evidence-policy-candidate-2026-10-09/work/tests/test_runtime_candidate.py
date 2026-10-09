"""Clock control is never exercised; probes and child data paths are isolated."""

import copy
import importlib.util
import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "md_runtime", ROOT / "integrations/runtime/marketdata_runtime.py"
)
rt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rt)


@pytest.fixture
def profile(tmp_path):
    p = rt.load_profile(ROOT / "integrations/runtime/profile.example.json")
    p["evidence_root"] = str(tmp_path / "evidence")
    p["data_root"] = str(tmp_path / "data")
    p["chrony_config"] = str(ROOT / "integrations/runtime/chrony.conf")
    return p


def sample(t, wall=None, boot=None):
    return {
        "wall_ns": int((t if wall is None else wall) * 1e9),
        "monotonic_ns": int(t * 1e9),
        "boottime_ns": int((t if boot is None else boot) * 1e9),
        "sampling_span_ns": 1000,
    }


def good_tracking(now=1000):
    return rt.tracking(
        f"CB00710F,203.0.113.15,3,{now - 1},0.001,0.002,0.003,1,0,0.2,0.01,0.02,64,Normal\n"
    )


def ready():
    return {"ready": True, "reasons": [], "provider": {"pid": 42, "invocation_id": "a"}}


def test_csv_and_conditional_bound(profile):
    t = good_tracking()
    result = rt.assess_clock(t, profile["policy"], 1000)
    assert result["ready"]
    assert result["conditional_error_bound_seconds"] == pytest.approx(0.026)
    assert t["skew_ppm"] == 0.2


@pytest.mark.parametrize(
    "text", ["", "1,2,3", "\n".join(["x"] * 14), "A,1.2.3.4,2,999,nan,0,0,0,0,0,0,0,0,Normal"]
)
def test_malformed_csv_refused(text):
    with pytest.raises(rt.NotReady):
        rt.tracking(text)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("reference_address", "127.127.1.1", "no_external_reference"),
        ("reference_id", "7F7F0101", "no_external_reference"),
        ("stratum", 0, "not_synchronized_or_leap_pending"),
        ("leap", "Not synchronised", "not_synchronized_or_leap_pending"),
        ("leap", "Insert second", "not_synchronized_or_leap_pending"),
        ("correction", 0.101, "remaining_correction"),
        ("root_dispersion", 0.6, "clock_error_bound"),
        ("root_dispersion", -0.1, "clock_error_bound"),
        ("reference_time", 399, "reference_age"),
        ("reference_time", 1001, "reference_age"),
        ("skew_ppm", 101, "frequency_uncertainty"),
    ],
)
def test_actual_quality_not_merely_daemon_active(profile, field, value, reason):
    t = good_tracking()
    t[field] = value
    assert reason in rt.assess_clock(t, profile["policy"], 1000)["reasons"]


def test_probe_rejects_changed_instance_or_configuration(profile):
    identity = {
        "pid": 42,
        "invocation_id": "a",
        "argv": ["chronyd", "-f", profile["chrony_config"]],
    }
    rows = iter([identity, {**identity, "invocation_id": "b"}])
    result = rt.probe(
        profile,
        runner=lambda _: good_tracking(time.time())["raw_csv"],
        identity=lambda _: next(rows),
    )
    assert result["ready"] is False
    assert "restarted" in result["reasons"][0]
    profile["chrony_config_sha256"] = "bad"
    result = rt.probe(
        profile,
        runner=lambda _: pytest.fail("must not query changed config"),
        identity=lambda _: identity,
    )
    assert "configuration changed" in result["reasons"][0]


@pytest.mark.parametrize("options", [["-x"], ["-dx"], ["-q"], ["-Q"], ["-s"]])
def test_clock_disabled_or_stepping_startup_mode_refused(monkeypatch, options):
    monkeypatch.setattr(Path, "read_bytes", lambda _: "\0".join(["chronyd", *options]).encode())
    with pytest.raises(rt.NotReady):
        rt.provider_identity(lambda _: "MainPID=42\nActiveState=active\nInvocationID=a\n")


@pytest.mark.parametrize(
    "current,reason",
    [
        (sample(1, wall=-40), "wall_clock_discontinuity"),
        (sample(1, wall=42), "wall_clock_discontinuity"),
        (sample(1, wall=31, boot=31), "suspend_or_boottime_discontinuity"),
        (sample(8), "observation_gap"),
    ],
)
def test_clock_anomalies_latched_without_rewriting_input(profile, current, reason):
    initial = copy.deepcopy(current)
    m = rt.ClockMonitor(profile["policy"], ready(), sample(0))
    result = m.observe(current, ready())
    assert reason in result["reasons"] and not result["clock_usable"]
    assert current == initial
    later = {k: v + int(1e9) if k != "sampling_span_ns" else v for k, v in current.items()}
    assert not m.observe(later, ready())["clock_usable"]


def test_provider_recovery_keeps_session_history(profile):
    m = rt.ClockMonitor(profile["policy"], ready(), sample(0))
    assert m.observe(sample(1), ready())["clock_usable"]
    fail = {"ready": False, "provider": None, "reasons": ["timeout"]}
    assert "provider_unready" in m.observe(sample(2), fail)["reasons"]
    assert not m.observe(sample(3), ready())["clock_usable"]
    changed = {**ready(), "provider": {"pid": 43, "invocation_id": "b"}}
    assert "provider_instance_changed" in m.observe(sample(4), changed)["reasons"]


class FakeWait:
    def __init__(self):
        self.current = 0
        self.cancel = False

    def is_set(self):
        return self.cancel

    def wait(self, value):
        self.current += value


class MemoryEvidence:
    run_id = "test"

    def __init__(self):
        self.records = []

    def write(self, state, **extra):
        self.records.append({"state": state, **extra})


def test_wait_recovers_and_does_not_use_wall_deadline(profile, monkeypatch):
    stop, evidence = FakeWait(), MemoryEvidence()
    values = iter([{"ready": False}, {"ready": False}, ready()])
    monkeypatch.setattr(rt.time, "time", lambda: -1e9)
    result = rt.wait_ready(
        profile,
        evidence,
        stop,
        checker=lambda _: next(values),
        env_check=lambda _: "release",
        monotonic=lambda: stop.current,
    )
    assert result["ready"] and stop.current == 10
    assert [r["state"] for r in evidence.records] == ["waiting", "waiting", "ready"]
    assert all(r["child_pid"] is None and not r["clock_usable"] for r in evidence.records)


def test_wait_expiry_never_starts_child(profile):
    stop, evidence = FakeWait(), MemoryEvidence()
    with pytest.raises(rt.NotReady, match="timeout"):
        rt.wait_ready(
            profile,
            evidence,
            stop,
            checker=lambda _: {"ready": False},
            env_check=lambda _: "release",
            monotonic=lambda: stop.current,
        )
    assert stop.current == 120
    assert all(r["state"] == "waiting" for r in evidence.records)


def test_ready_not_reused_for_run(profile):
    evidence = MemoryEvidence()
    calls = iter([ready(), {"ready": False}])
    with pytest.raises(rt.NotReady, match="changed"):
        rt.supervise(
            profile,
            "depth",
            evidence,
            threading.Event(),
            checker=lambda _: next(calls),
            env_check=lambda _: "release",
            spawn=lambda *a, **k: pytest.fail("must not launch collector"),
        )


def test_supervisor_raw_child_continues_when_time_untrusted(profile, tmp_path):
    profile["policy"].update(sample_seconds=0.02, provider_seconds=0.02, heartbeat_seconds=0.02)
    evidence, stop = MemoryEvidence(), threading.Event()
    child = None
    calls = 0

    def checker(_):
        nonlocal calls
        calls += 1
        if calls < 4:
            return ready()
        return {"ready": False, "provider": None, "reasons": ["clock step"]}

    def spawn(*_, **kwargs):
        nonlocal child
        script = (
            "import signal,time; signal.signal(signal.SIGINT,lambda *a:exit(0)); time.sleep(10)"
        )
        child = subprocess.Popen([sys.executable, "-c", script], **kwargs)
        return child

    def stopper():
        time.sleep(0.3)
        assert child.poll() is None  # raw collection is not stopped by clock-quality loss
        stop.set()

    thread = threading.Thread(target=stopper)
    thread.start()
    assert (
        rt.supervise(
            profile,
            "depth",
            evidence,
            stop,
            checker=checker,
            env_check=lambda _: "release",
            spawn=spawn,
        )
        == 0
    )
    thread.join()
    running = [r for r in evidence.records if r["state"] == "running"]
    assert any(r["clock_usable"] for r in running)
    assert any(not r["clock_usable"] for r in running)
    assert evidence.records[-1]["session_clock_clean"] is False
    assert child.poll() == 0


def test_evidence_failure_stops_unmonitored_child(profile):
    class BrokenEvidence(MemoryEvidence):
        def write(self, state, **extra):
            if state == "running":
                raise OSError("injected disk full")
            super().write(state, **extra)

    children = []

    def spawn(*_, **kwargs):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)
        children.append(child)
        return child

    with pytest.raises(OSError, match="disk full"):
        rt.supervise(
            profile,
            "depth",
            BrokenEvidence(),
            threading.Event(),
            checker=lambda _: ready(),
            env_check=lambda _: "release",
            spawn=spawn,
        )
    assert children[0].poll() is not None


def test_duplicate_supervisor_and_stale_status(profile, monkeypatch):
    evidence = rt.Evidence(profile, "depth")
    try:
        with pytest.raises(rt.NotReady, match="Another"):
            rt.Evidence(profile, "depth")
        record = evidence.write(
            "running",
            clock_usable=True,
            child_pid=os.getpid(),
            child_start_ticks=rt.process_start(os.getpid()),
        )
        assert rt.status(profile, "depth")["clock_usable"]
        old = record["sample"]
        monkeypatch.setattr(
            rt,
            "stamp",
            lambda: {
                **old,
                "boottime_ns": old["boottime_ns"] + int(60e9),
                "wall_ns": old["wall_ns"] + int(60e9),
            },
        )
        assert not rt.status(profile, "depth")["clock_usable"]
        monkeypatch.setattr(rt, "stamp", lambda: {**old, "wall_ns": old["wall_ns"] - int(41e9)})
        assert not rt.status(profile, "depth")["clock_usable"]
    finally:
        evidence.close()


def test_atomic_latest_failure_keeps_append_only_record(profile, monkeypatch):
    evidence = rt.Evidence(profile, "depth")
    try:
        monkeypatch.setattr(rt, "atomic_json", lambda *a: (_ for _ in ()).throw(OSError("rename")))
        with pytest.raises(OSError):
            evidence.write("failed", clock_usable=False)
        assert json.loads(evidence.path.read_text())["state"] == "failed"
    finally:
        evidence.close()


def test_collector_options_and_interpreter_isolation(profile):
    for stream, rotate in [("depth", "900"), ("aggtrade", "3600")]:
        args = rt.collector_command(profile, stream)
        assert args[1:3] == ["-I", "-c"]
        assert args[args.index("--root") + 1] == profile["data_root"]
        assert args[args.index("--rotate-seconds") + 1] == rotate
        assert "--fresh-start" not in args
        assert ("--verify-seconds" in args) == (stream == "depth")


@pytest.mark.parametrize("stream", ["depth", "aggtrade"])
def test_existing_collector_lock_blocks_duplicate_cli(stream, tmp_path):
    from marketdata import aggtrade_stream, depth_stream
    from marketdata.store import Store

    store = Store(tmp_path / "store")
    module = depth_stream if stream == "depth" else aggtrade_stream
    try:
        with module.stream_lock(store):
            result = subprocess.run(
                [
                    str(ROOT / ".venv/bin/python"),
                    "-I",
                    "-c",
                    "from marketdata.cli import main; raise SystemExit(main())",
                    "--root",
                    str(store.root),
                    f"{stream}-stream",
                    "--seconds",
                    "1",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        assert result.returncode == 1
        assert "Another" in result.stderr and "stream" in result.stderr
    finally:
        store.close()


def test_unknown_policy_and_root_uid_refused(profile, tmp_path):
    path = tmp_path / "profile.json"
    profile["uid"] = 0
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="non-root"):
        rt.load_profile(path)


def test_units_use_system_manager_and_normal_account(profile):
    spec = importlib.util.spec_from_file_location(
        "render", ROOT / "integrations/runtime/render_units.py"
    )
    render = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render)
    unit = render.render(profile, "/etc/marketdata/runtime.json")
    for text in [
        "User=",
        "WantedBy=multi-user.target",
        "ProtectClock=yes",
        "KillMode=mixed",
        "ExecStartPre=",
        "Restart=always",
        "StartLimitIntervalSec=0",
    ]:
        assert text in unit
    assert "ExecCondition=" not in unit and "User=root" not in unit


def test_steady_chrony_has_rtc_sync_but_never_automatically_steps():
    lines = (ROOT / "integrations/runtime/chrony.conf").read_text().splitlines()
    commands = {line.split()[0] for line in lines if line.strip() and not line.startswith("#")}
    assert not commands & {
        "makestep",
        "initstepslew",
        "rtcfile",
        "local",
        "include",
        "confdir",
        "sourcedir",
    }
    assert "rtcsync" in commands
    assert "leapsecmode slew" in lines


@pytest.fixture
def fake_installation(profile, tmp_path, monkeypatch):
    from types import SimpleNamespace

    root = tmp_path / "release"
    package = root / ".venv/site-packages/marketdata/__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text("# immutable fixture\n")
    profile["release_root"] = str(root)
    data = Path(profile["data_root"])
    (data / "state").mkdir(parents=True)
    (data / "data").mkdir()
    (data / "state/catalog.sqlite").touch()
    manifest = {
        "python": platform.python_version(),
        "release_id": "test-release",
        "runtime_files_sha256": {str(package.relative_to(root)): rt.digest(package)},
    }
    (root / "runtime-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(rt.sys, "prefix", str(root / ".venv"))
    monkeypatch.setattr(
        rt.importlib.util, "find_spec", lambda _: SimpleNamespace(origin=str(package))
    )
    return profile, root, package


def test_installed_origin_hash_uuid_dns_and_data_paths(fake_installation):
    p, _, _ = fake_installation
    calls = []

    def runner(args):
        calls.append(args)
        return p["mount_uuid"] if "findmnt" in args[0] else ""

    assert rt.environment_check(p, runner) == "test-release"
    assert calls[1][1:3] == ["-I", "-c"]
    assert calls[1][-2:] == p["dns_hosts"]


@pytest.mark.parametrize(
    "fault,match",
    [
        ("wrong_mount", "UUID"),
        ("tampered_package", "file changed"),
        ("checkpoint_root", "Checkpoint"),
        ("missing_catalog", "catalog"),
        ("wrong_python", "Python version"),
        ("dns_failure", "DNS"),
    ],
)
def test_environment_fail_closed(fake_installation, fault, match):
    p, root, package = fake_installation
    if fault == "tampered_package":
        package.write_text("modified\n")
    if fault == "checkpoint_root":
        (Path(p["data_root"]) / "backup.json").write_text("{}")
    if fault == "missing_catalog":
        (Path(p["data_root"]) / "state/catalog.sqlite").unlink()
    if fault == "wrong_python":
        path = root / "runtime-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["python"] = "0.0.0"
        path.write_text(json.dumps(manifest))

    def runner(args):
        if "findmnt" in args[0]:
            return "wrong" if fault == "wrong_mount" else p["mount_uuid"]
        if fault == "dns_failure":
            raise rt.NotReady("DNS failed")
        return ""

    with pytest.raises(rt.NotReady, match=match):
        rt.environment_check(p, runner)


def test_editable_import_refused(fake_installation, monkeypatch):
    from types import SimpleNamespace

    p, _, _ = fake_installation
    monkeypatch.setattr(
        rt.importlib.util,
        "find_spec",
        lambda _: SimpleNamespace(origin="/tmp/development/marketdata/__init__.py"),
    )
    with pytest.raises(rt.NotReady, match="Editable"):
        rt.environment_check(p)


def test_foreign_boot_dead_pid_and_changed_profile_are_unusable(profile, monkeypatch):
    evidence = rt.Evidence(profile, "depth")
    try:
        record = evidence.write(
            "running",
            clock_usable=True,
            child_pid=os.getpid(),
            child_start_ticks=rt.process_start(os.getpid()),
        )
        assert rt.status(profile, "depth")["clock_usable"]
        profile["policy"]["max_correction_seconds"] = 0.2
        assert not rt.status(profile, "depth")["clock_usable"]
        profile["policy"]["max_correction_seconds"] = 0.1
        monkeypatch.setattr(rt, "process_start", lambda _: "reused-pid")
        assert not rt.status(profile, "depth")["clock_usable"]
        monkeypatch.setattr(rt, "process_start", lambda _: record["supervisor_start_ticks"])
        monkeypatch.setattr(rt, "boot_id", lambda: "different-boot")
        assert not rt.status(profile, "depth")["clock_usable"]
    finally:
        evidence.close()
