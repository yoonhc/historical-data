import importlib.util
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest
from marketdata import aggtrades, backup, depth
from marketdata import clock_evidence as ce
from marketdata.domain import DataError
from marketdata.store import Store

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "capture_v2", ROOT / "integrations/runtime/capture_runtime.py"
)
cr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cr)


@pytest.fixture
def profile(tmp_path):
    p = cr.rt.load_profile(ROOT / "integrations/runtime/profile.example.json")
    p["data_root"] = str(tmp_path / "store")
    p["evidence_root"] = str(tmp_path / "evidence")
    return p


def stamp(t, wall=None, boot=None):
    return {
        "wall_ns": int((100000 + t if wall is None else wall) * 1e9),
        "monotonic_ns": int(t * 1e9),
        "boottime_ns": int((t if boot is None else boot) * 1e9),
        "sampling_span_ns": 500,
    }


def clock(profile, t, age=1, **fields):
    track = cr.rt.tracking(
        f"CB00710F,203.0.113.15,3,{100000 + t - age},0.001,0,0,1,0,0.2,0.01,0.02,64,Normal"
    )
    track.update(fields)
    return {
        **cr.rt.assess_clock(track, profile["policy"], 100000 + t),
        "provider": {"pid": 42, "invocation_id": "original", "argv": ["chronyd"]},
        "sample": stamp(t),
    }


def monitor(profile):
    return cr.Monitor(profile, clock(profile, 1), stamp(1), {"ready": True, "phase": "closed"})


@pytest.mark.parametrize("age", [600.001, 900, 3600, 14400])
def test_old_reference_only_is_warning_not_child_ban(profile, age):
    m = monitor(profile)
    r = m.observe(stamp(2), clock(profile, 2, age=age))
    assert r["decision"]["reference_stale"]
    assert r["decision"]["current_estimate_usable"] and r["decision"]["raw_start_allowed"]
    assert r["clock_usable"] is False  # Existing cumulative meaning is not rewritten.
    assert r["history"]["reference_stale_seen"] and not r["history"]["hard_fault_seen"]
    r = m.observe(stamp(3), clock(profile, 3))
    assert r["decision"]["raw_start_allowed"] and not r["decision"]["reference_stale"]
    assert r["history"]["reference_stale_seen"] and r["clock_usable"] is False


@pytest.mark.parametrize(
    "fields",
    [
        {"reference_time": 100003},
        {"correction": 0.101},
        {"root_dispersion": 0.6},
        {"root_dispersion": -1},
        {"stratum": 0},
        {"leap": "Not synchronised"},
        {"reference_id": "00000000"},
        {"reference_address": "127.0.0.1"},
        {"skew_ppm": 101},
    ],
)
def test_no_exception_for_other_clock_faults(profile, fields):
    m = monitor(profile)
    r = m.observe(stamp(2), clock(profile, 2, age=900, **fields))
    assert not r["decision"]["raw_start_allowed"]
    assert r["history"]["hard_fault_seen"]
    # A subsequent age-only sample cannot erase the earlier fault.
    r = m.observe(stamp(3), clock(profile, 3, age=900))
    assert not r["decision"]["raw_start_allowed"]


@pytest.mark.parametrize("failure", ["query", "expired", "provider", "gap", "step", "suspend"])
def test_monitor_or_provider_failure_blocks_stale_exception(profile, failure):
    m, c, s = monitor(profile), clock(profile, 2, age=900), stamp(2)
    if failure == "query":
        c = {"ready": False, "reasons": ["query failed"], "provider": None, "sample": s}
    elif failure == "expired":
        c["sample"] = stamp(-100)
    elif failure == "provider":
        c["provider"] = {"pid": 43, "invocation_id": "new"}
    elif failure == "gap":
        s = stamp(10)
        c = clock(profile, 10, age=900)
    elif failure == "step":
        s = stamp(2, wall=99900)
    elif failure == "suspend":
        s = stamp(2, boot=10)
    r = m.observe(s, c)
    assert not r["decision"]["raw_start_allowed"]
    assert r["history"]["hard_fault_seen"]


def test_first_start_stays_strict(profile):
    with pytest.raises(cr.rt.NotReady):
        cr.Monitor(profile, clock(profile, 1, age=900), stamp(1), {"ready": True})
    with pytest.raises(cr.rt.NotReady):
        cr.Monitor(profile, clock(profile, 1), stamp(1), {"ready": False})


class Timer:
    def __init__(self):
        self.t, self.stopped = 1.0, False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.t += seconds
        assert self.t < 200


class Memory:
    run_id = "a" * 32
    context = {"run_id": run_id}

    def __init__(self):
        self.rows, self.flushes = [], 0

    def write(self, state, **extra):
        self.rows.append({"state": state, **extra})

    def flush(self):
        self.flushes += 1


def test_next_child_starts_stale_while_supervisor_observation_continues(profile, monkeypatch):
    timer, evidence, starts = Timer(), Memory(), []

    class Child:
        pid = 123
        returncode = 0

        def __init__(self):
            self.end = timer.t + 3

        def poll(self):
            return 0 if timer.t >= self.end else None

    def spawn(*args, **kw):
        starts.append((timer.t, kw["env"]["MARKETDATA_CLOCK_CONTEXT"]))
        return Child()

    monkeypatch.setattr(cr.rt, "process_start", lambda pid: "123")
    result = cr.supervise(
        profile,
        "depth",
        evidence,
        timer,
        {"ready": True},
        checker=lambda _: clock(profile, timer.t, age=1 if timer.t < 10 else 900),
        env_check=lambda _: "fixture",
        spawn=spawn,
        stamp=lambda: stamp(timer.t),
        monotonic=lambda: timer.t,
        max_children=2,
    )
    assert result == 0 and len(starts) == 2 and starts[1][0] >= 19
    assert starts[0][1] == starts[1][1]
    second = [r for r in evidence.rows if r["state"] == "starting_child"][1]
    assert second["decision"]["reference_stale"] and second["decision"]["raw_start_allowed"]
    assert any(
        r["state"] == "between_sessions" and r["clock_usable"] is False for r in evidence.rows
    )


def base(stream="depth"):
    return {
        "run_id": uuid.uuid4().hex,
        "boot_id": "11111111-1111-1111-1111-111111111111",
        "stream": stream,
        "policy_version": ce.POLICY,
        "schema": 2,
    }


def recorded_collection(store, tmp_path, monkeypatch, stream="depth", close=True):
    b = base(stream)
    monkeypatch.setenv("MARKETDATA_CLOCK_CONTEXT", json.dumps(b))
    monkeypatch.setattr(ce, "sample", lambda: stamp(2))
    start = (
        depth.start_collection
        if stream == "depth"
        else lambda s, d: aggtrades.start_collection(s, "websocket", d)
    )
    report = start(store, {})
    j = ce.Journal(store, tmp_path / "spool", b, min_free_bytes=0)
    d = {
        "reference_stale": True,
        "current_estimate_usable": True,
        "raw_start_allowed": True,
        "reasons": [],
    }
    for t in (1, 2, 3):
        j.write(
            "running",
            sample_value=stamp(t),
            decision=d,
            history={"reference_stale_seen": True, "hard_fault_seen": False},
        )
    if close:
        monkeypatch.setattr(ce, "sample", lambda: stamp(3))
        (depth.save_collection if stream == "depth" else aggtrades.save_collection)(
            store, report, "partial"
        )
        j.write("finished", sample_value=stamp(4), decision=d)
    j.flush()
    return report, j, b


@pytest.mark.parametrize("stream", ["depth", "aggtrade"])
def test_evidence_backup_restore_query_and_new_backup(store, tmp_path, monkeypatch, stream):
    report, j, b = recorded_collection(store, tmp_path, monkeypatch, stream)
    original = ce.query(store, report["id"])
    assert original["evidence_state"] == "available"
    assert original["intervals"][0]["reference_stale"]
    checkpoint = tmp_path / "checkpoint"
    backup.create_backup(store, checkpoint, min_free_gib=0)
    assert backup.verify_backup(checkpoint)["ok"]
    assert ce.query_root(checkpoint, report["id"]) == original
    restored = tmp_path / "restored"
    backup.restore_backup(checkpoint, restored, min_free_gib=0)
    assert ce.query_root(restored, report["id"]) == original
    with_context = json.loads(
        backup.open_catalog(checkpoint / "state/catalog.sqlite")
        .execute("SELECT report FROM event_collections WHERE id=?", (report["id"],))
        .fetchone()[0]
    )
    assert with_context["state"] == "partial"  # Clock evidence does not repair market quality.
    restored_store = Store(restored)
    try:
        assert restored_store.validate()["ok"]
        backup.create_backup(restored_store, tmp_path / "independent", min_free_gib=0)
        assert backup.verify_backup(tmp_path / "independent")["ok"]
    finally:
        restored_store.close()
    assert not list(j.root.glob("*.jsonl"))
    cp_manifest = json.loads((checkpoint / "backup.json").read_text())
    assert cp_manifest["source_root"] == str(store.root)
    assert json.loads((tmp_path / "independent/backup.json").read_text())["source_root"] == str(
        restored
    )


def test_legacy_missing_open_and_backup_tail_are_unknown(store, tmp_path, monkeypatch):
    monkeypatch.delenv("MARKETDATA_CLOCK_CONTEXT", raising=False)
    old = depth.start_collection(store, {})
    assert ce.query(store, old["id"])["evidence_state"] == "unknown"
    report, j, _ = recorded_collection(store, tmp_path, monkeypatch, close=False)
    assert "collection_end_unknown" in ce.query(store, report["id"])["unknown_reasons"]
    monkeypatch.setattr(ce, "sample", lambda: stamp(10))
    depth.save_collection(store, report, "partial")
    j.write("finished", sample_value=stamp(11), decision={})  # Still mutable and NOT in backup.
    backup.create_backup(store, tmp_path / "checkpoint", min_free_gib=0)
    result = ce.query_root(tmp_path / "checkpoint", report["id"])
    assert result["evidence_state"] == "unknown"
    assert "end_not_covered_by_committed_evidence" in result["unknown_reasons"]


def test_rotation_and_retry_after_commit_before_spool_unlink(store, tmp_path, monkeypatch):
    b = base()
    j = ce.Journal(store, tmp_path / "spool", b, min_free_bytes=0)
    j.write("running", sample_value=stamp(1))
    path = j.path
    original = Path.unlink
    monkeypatch.setattr(
        Path,
        "unlink",
        lambda p, *a, **kw: (
            (_ for _ in ()).throw(OSError("crash after commit"))
            if p == path
            else original(p, *a, **kw)
        ),
    )
    with pytest.raises(OSError, match="crash"):
        j.flush()
    assert path.exists() and store.db.execute("SELECT count(*) FROM fetches").fetchone()[0] == 1
    monkeypatch.setattr(Path, "unlink", original)
    ce.Journal(store, j.root, base(), min_free_bytes=0)
    assert not path.exists() and store.db.execute("SELECT count(*) FROM fetches").fetchone()[0] == 1


def test_uncommitted_spool_and_capacity_fail_closed(store, tmp_path, monkeypatch):
    j = ce.Journal(store, tmp_path / "spool", base(), min_free_bytes=0)
    j.write("running", sample_value=stamp(1))
    path = j.path
    original = store.put_raw
    monkeypatch.setattr(store, "put_raw", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        j.flush()
    assert path.exists() and store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
    monkeypatch.setattr(store, "put_raw", original)
    ce.Journal(store, j.root, base(), min_free_bytes=0)
    assert not path.exists()
    monkeypatch.setattr(ce.shutil, "disk_usage", lambda _: type("Usage", (), {"free": 0})())
    with pytest.raises(DataError, match="capacity"):
        j.write("running", sample_value=stamp(2))


def test_corruption_and_missing_chain_never_become_good(store, tmp_path, monkeypatch):
    report, j, _ = recorded_collection(store, tmp_path, monkeypatch)
    j.write("finished", sample_value=stamp(5))
    j.flush()
    with store.db:
        store.db.execute("DELETE FROM fetches WHERE url LIKE '%/0'")
    with pytest.raises(DataError, match="chain"):
        ce.query(store, report["id"])


def test_commit_worker_does_not_block_sampling_and_reports_failure(profile, tmp_path, monkeypatch):
    Store(profile["data_root"]).close()
    entered, release = threading.Event(), threading.Event()
    original = cr.publish_chunk

    def delayed(store, path):
        entered.set()
        assert release.wait(10)
        return original(store, path)

    monkeypatch.setattr(cr, "publish_chunk", delayed)
    e = cr.Evidence(
        profile, "depth", journal_factory=lambda *a, **kw: ce.Journal(*a, **kw, min_free_bytes=0)
    )
    try:
        e.write("running", sample_value=stamp(1))
        e.journal.flush()
        assert entered.wait(5)
        # The writer is deliberately stalled. New evidence still reaches fsync.
        e.write("running", sample_value=stamp(2))
        assert e.journal.path.read_bytes().endswith(b"\n")
        release.set()
        e.flush()
    finally:
        release.set()
        e.close()


def test_real_child_stopped_on_evidence_failure(profile, monkeypatch):
    e = Memory()
    original = e.write
    child_ref = []

    def write(state, **kw):
        if child_ref and state == "running":
            raise OSError("evidence write failed")
        original(state, **kw)

    e.write = write

    def spawn(*args, **kw):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
        )
        child_ref.append(child)
        return child

    def real_clock(_):
        at = cr.rt.stamp()
        t = at["wall_ns"] / 1e9 - 100000
        c = clock(profile, t)
        c["sample"] = at
        return c

    with pytest.raises(OSError, match="evidence"):
        cr.supervise(
            profile,
            "depth",
            e,
            threading.Event(),
            {"ready": True},
            checker=real_clock,
            env_check=lambda _: "fixture",
            spawn=spawn,
        )
    assert len(child_ref) == 1 and child_ref[0].poll() is not None


def test_restic_extract_restore_and_query(store, tmp_path, monkeypatch):
    binary = os.environ.get("CLOCK_TEST_RESTIC")
    if binary is None:
        pytest.skip("explicit isolated restic binary required")
    report, _, _ = recorded_collection(store, tmp_path, monkeypatch)
    checkpoint = tmp_path / "checkpoint"
    backup.create_backup(store, checkpoint, min_free_gib=0)
    expected = ce.query_root(checkpoint, report["id"])
    password = tmp_path / "test-password"
    password.write_text(uuid.uuid4().hex)
    password.chmod(0o600)
    env = {
        **os.environ,
        "RESTIC_REPOSITORY": str(tmp_path / "repository"),
        "RESTIC_PASSWORD_FILE": str(password),
        "RESTIC_CACHE_DIR": str(tmp_path / "cache"),
    }

    def run(args, cwd=None):
        result = subprocess.run(
            [binary, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=90
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    run(["init"])
    run(["backup", "--json", "."], checkpoint)
    snapshot = json.loads(run(["snapshots", "--json"]))[0]["id"]
    run(["check", "--read-data"])
    extracted = tmp_path / "extracted"
    run(["restore", snapshot, "--target", str(extracted)])
    assert backup.verify_backup(extracted)["ok"]
    restored = tmp_path / "from-restic"
    backup.restore_backup(extracted, restored, min_free_gib=0)
    assert ce.query_root(restored, report["id"]) == expected
    with_store = Store(restored)
    try:
        assert with_store.validate()["ok"]
        backup.create_backup(with_store, tmp_path / "independent", min_free_gib=0)
        assert backup.verify_backup(tmp_path / "independent")["ok"]
    finally:
        with_store.close()


def test_policy_does_not_reopen_bootstrap_or_relax_job_gate(profile, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "gate", ROOT / "integrations/runtime/clock_gate.py"
    )
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    ledger = gate.Ledger(tmp_path)
    with ledger.lock():
        gate.enroll({}, ledger, "fixture")
        state = ledger.read()
        state.update(phase="closed", closed_observation={"ready": True})
        ledger.save(state)
    original = ledger.path.read_bytes()
    with pytest.raises(gate.Hold, match="Current clock"):
        gate.check_gate(
            {},
            profile,
            ledger=ledger,
            current_boot="fixture",
            checker=lambda _: clock(profile, 1, age=900),
        )
    assert ledger.path.read_bytes() == original


def test_slow_commit_rechecks_clock_before_spawn(profile):
    timer, evidence, starts = Timer(), Memory(), []

    def flush():
        evidence.flushes += 1
        if evidence.flushes == 2:
            timer.t += 6

    evidence.flush = flush
    timer.is_set = lambda: timer.t > 15
    cr.supervise(
        profile,
        "depth",
        evidence,
        timer,
        {"ready": True},
        checker=lambda _: clock(profile, timer.t, age=900 if timer.t > 5 else 1),
        env_check=lambda _: "fixture",
        spawn=lambda *a, **kw: starts.append(kw),
        stamp=lambda: stamp(timer.t),
        monotonic=lambda: timer.t,
    )
    assert starts == []
    assert any(r["state"] == "start_refused_after_commit" for r in evidence.rows)


@pytest.mark.parametrize("after_commit", [False, True])
def test_actual_sigkill_preserves_and_recovers_spool(store, tmp_path, after_commit):
    import signal

    spool = tmp_path / "spool"
    run = base()
    code = """
import json, os, signal, sys
from pathlib import Path
from marketdata.store import Store
from marketdata.clock_evidence import Journal
store=Store(sys.argv[1])
j=Journal(store,sys.argv[2],json.loads(sys.argv[3]),min_free_bytes=0)
j.write("running", sample_value={"wall_ns":1,"boottime_ns":1,"monotonic_ns":1,"sampling_span_ns":0})
if sys.argv[4] == "yes":
    orig=Path.unlink
    Path.unlink=lambda self,*a,**kw: os.kill(os.getpid(),signal.SIGKILL) if self==j.path else orig(self,*a,**kw)
    j.flush()
os.kill(os.getpid(),signal.SIGKILL)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(store.root),
            str(spool),
            json.dumps(run),
            "yes" if after_commit else "no",
        ],
        timeout=15,
    )
    assert result.returncode == -signal.SIGKILL
    assert len(list(spool.glob("*.jsonl"))) == 1
    ce.Journal(store, spool, base(), min_free_bytes=0)
    assert not list(spool.glob("*.jsonl"))
    assert store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM fetches").fetchone()[0] == 1


def test_rotation_bounds_and_partial_tail_are_preserved(store, tmp_path, monkeypatch):
    j = ce.Journal(store, tmp_path / "spool", base(), min_free_bytes=0)
    monkeypatch.setattr(ce, "MAX_CHUNK", 2048)
    for t in range(10):
        j.write("running", sample_value=stamp(t), padding="x" * 700)
    assert j.chunk > 1 and j.path.stat().st_size <= 2048
    with j.path.open("ab") as f:
        f.write(b'{"partial":')
    j.flush()
    record = store.db.execute("SELECT metadata FROM fetches ORDER BY id DESC LIMIT 1").fetchone()
    assert json.loads(record[0])["partial_tail_bytes"] > 0
    assert store.validate()["ok"]


def test_async_worker_failure_is_visible_and_staging_retained(profile, monkeypatch):
    Store(profile["data_root"]).close()
    entered = threading.Event()

    def fail(store, path):
        entered.set()
        raise OSError("injected publication failure")

    monkeypatch.setattr(cr, "publish_chunk", fail)
    e = cr.Evidence(
        profile, "aggtrade", journal_factory=lambda *a, **kw: ce.Journal(*a, **kw, min_free_bytes=0)
    )
    try:
        e.write("running", sample_value=stamp(1))
        e.journal.flush()
        assert entered.wait(5)
        with pytest.raises(DataError, match="publication"):
            e.publisher.drain()
        assert list((e.root / "spool").glob("*.jsonl"))
    finally:
        e.close()


def test_actual_cli_preserves_unknown_vs_available(store, tmp_path, monkeypatch):
    report, _, _ = recorded_collection(store, tmp_path, monkeypatch)
    checkpoint = tmp_path / "checkpoint"
    backup.create_backup(store, checkpoint, min_free_gib=0)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "marketdata.cli",
            "--root",
            str(checkpoint),
            "clock-evidence",
            "--collection-id",
            report["id"],
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ce.query_root(checkpoint, report["id"])
    assert not (checkpoint / "state/catalog.sqlite-wal").exists()
    assert backup.verify_backup(checkpoint)["ok"]


@pytest.mark.parametrize("stream", ["depth", "aggtrade"])
def test_real_hour_boundary_protocol_with_two_children(profile, tmp_path, monkeypatch, stream):
    # Real processes and real catalog/immutable evidence, with a deterministic
    # read-only clock provider. No exchange traffic or host clock control.
    first_finished = threading.Event()
    child_count = []
    code = """
import sys, time
from marketdata import depth, aggtrades
from marketdata.store import Store
s=Store(sys.argv[1])
if sys.argv[2]=='depth':
    r=depth.start_collection(s, {"complete":False})
    time.sleep(0.1)
    depth.save_collection(s,r,"partial")
else:
    r=aggtrades.start_collection(s,"websocket", {"complete":True})
    time.sleep(0.1)
    aggtrades.save_collection(s,r,"complete")
s.close()
"""
    monkeypatch.setattr(
        cr.rt,
        "collector_command",
        lambda p, stream: [sys.executable, "-I", "-c", code, p["data_root"], stream],
    )
    original_spawn = subprocess.Popen

    def spawn(*args, **kw):
        child = original_spawn(*args, **kw)
        child_count.append(child)
        return child

    def checker(_):
        if child_count and child_count[0].poll() is not None:
            first_finished.set()
        at = cr.rt.stamp()
        c = clock(profile, at["wall_ns"] / 1e9 - 100000, age=900 if first_finished.is_set() else 1)
        c["sample"] = at
        return c

    e = cr.Evidence(
        profile, stream, journal_factory=lambda *a, **kw: ce.Journal(*a, **kw, min_free_bytes=0)
    )
    try:
        assert (
            cr.supervise(
                profile,
                stream,
                e,
                threading.Event(),
                {"ready": True},
                checker=checker,
                env_check=lambda _: "fixture",
                spawn=spawn,
                max_children=2,
            )
            == 0
        )
        collections = list(
            e.store.db.execute("SELECT id,report FROM event_collections ORDER BY started_at")
        )
        assert len(collections) == 2
        links = [json.loads(c["report"])["runtime_clock"] for c in collections]
        assert links[0]["run_id"] == links[1]["run_id"] == e.run_id
        result = ce.query(e.store, collections[1]["id"])
        assert result["evidence_state"] == "available"
        assert any(i["reference_stale"] and i["raw_start_allowed"] for i in result["intervals"])
        checkpoint = tmp_path / "protocol-checkpoint"
        backup.create_backup(e.store, checkpoint, min_free_gib=0)
        assert ce.query_root(checkpoint, collections[1]["id"]) == result
    finally:
        e.close()
    assert all(c.poll() is not None for c in child_count)


def test_late_collection_query_does_not_load_whole_long_running_history(
    store, tmp_path, monkeypatch
):
    b = base()
    j = ce.Journal(store, tmp_path / "spool", b, min_free_bytes=0)
    for t in range(40):
        j.write("running", sample_value=stamp(t), decision={})
        j.flush()
    monkeypatch.setenv("MARKETDATA_CLOCK_CONTEXT", json.dumps(b))
    monkeypatch.setattr(ce, "sample", lambda: stamp(40))
    report = depth.start_collection(store, {})
    for t in (40, 41, 42):
        j.write("running", sample_value=stamp(t), decision={})
        j.flush()
    monkeypatch.setattr(ce, "sample", lambda: stamp(41))
    depth.save_collection(store, report, "partial")
    reads = []
    original = store.raw

    def raw(identity):
        reads.append(identity)
        return original(identity)

    monkeypatch.setattr(store, "raw", raw)
    result = ce.query(store, report["id"])
    assert result["evidence_state"] == "available"
    assert len(reads) <= 4
