import copy
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from marketdata import backup
from marketdata import checkpoint_clock as clock
from marketdata import checkpoint_evidence as evidence
from marketdata.domain import DataError, json_bytes, sha256
from test_backup import populate
from test_retirement_clock import Samples, provider

from scripts import retire_checkpoints as retirement


@pytest.fixture
def lab(store, tmp_path):
    populate(store, tmp_path)
    scope = {"repository_id": "a" * 64, "archive_scope": "isolated", "source_root": str(store.root)}
    reg = evidence.Registry(tmp_path / "evidence", scope, "d" * 64)
    samples = Samples()

    def observe():
        return clock.Guard(
            lambda: provider(samples), tmp_path / (uuid.uuid4().hex + ".jsonl"), sampler=samples
        ).check(fresh=True)

    with reg.lock(create=True):
        reg.baseline([])
    return store, reg, samples, observe


def create(lab, name, observer=None):
    store, reg, _, observe = lab
    return backup.create_backup(
        store,
        reg.root.parent / "copies" / name,
        min_free_gib=0,
        evidence=evidence.Producer(reg, observer or observe),
    )


def entry(result):
    root, manifest = backup.load_backup(result["path"])
    return {
        **evidence.identity(root, manifest),
        "path": str(root),
        "completed_at": manifest["completed_at"],
        "checkpoint_at": manifest["checkpoint_finished_at"],
    }


@pytest.mark.parametrize("radius,accepted", [(5, True), (5.000000001, False)])
def test_total_uncertainty_boundary(tmp_path, radius, accepted):
    s = Samples()
    sample = s()
    sample["span_ns"] = 0
    g = clock.Guard(
        lambda: provider(s, correction=radius, dispersion=0, delay=0),
        tmp_path / "clock",
        sampler=lambda: sample,
    )
    if accepted:
        o = g.check(fresh=True)
        assert o["total_uncertainty"] == 5
        clock.validate_observation(o)
    else:
        with pytest.raises(clock.ClockHold, match="total_uncertainty"):
            g.check(fresh=True)


@pytest.mark.parametrize(
    "change", [{"correction": 0.2}, {"dispersion": 1}, {"skew": 1000}, {"age": 100000}]
)
def test_old_individual_limits_and_reference_age_not_gates(tmp_path, change):
    s = Samples()
    o = clock.Guard(lambda: provider(s, **change), tmp_path / "clock", sampler=s).check(fresh=True)
    assert o["ready"] and "reference_stale" not in o
    clock.validate_observation(o)


@pytest.mark.parametrize("age,accepted", [(-0.2, True), (-1, False)])
def test_future_reference_is_compared_to_observation_upper(tmp_path, age, accepted):
    s = Samples()
    g = clock.Guard(lambda: provider(s, age=age, dispersion=0.4), tmp_path / "clock", sampler=s)
    if accepted:
        o = g.check(fresh=True)
        assert o["reference_age_seconds"] < 0
        clock.validate_observation(o)
    else:
        with pytest.raises(clock.ClockHold, match="future"):
            g.check(fresh=True)


def test_query_alignment_and_sampling_are_separate_and_count_toward_cap(tmp_path):
    s = Samples()

    def query():
        t = provider(s, correction=3, delay=0, dispersion=0)
        s.advance(2.1)
        return t

    g = clock.Guard(query, tmp_path / "clock", sampler=s)
    with pytest.raises(clock.ClockHold, match="total_uncertainty"):
        g.check(fresh=True)
    s = Samples()

    def short_query():
        t = provider(s)
        s.advance(0.02)
        return t

    o = clock.Guard(short_query, tmp_path / "clock2", sampler=s).check(fresh=True)
    assert o["query_alignment_margin"] == 0.02
    assert o["sampling_margin"] == 0.000002
    assert o["sample"] == o["query_after"]
    changed = copy.deepcopy(o)
    changed["query_before"]["monotonic_ns"] += 1
    with pytest.raises(ValueError, match="query bounds"):
        clock.validate_observation(changed)


def test_completed_marker_precedes_observation_and_input_stays_exclusive(lab):
    store, reg, _, observe = lab
    destination = reg.root.parent / "copies" / "new"
    calls = []

    def after_complete():
        raw = (destination / "backup.json").read_bytes()
        assert backup.verify_backup(destination)["ok"]
        script = "from marketdata.checkpoint_locks import checkpoint_locks,CheckpointBusy\nimport sys\ntry:\n with checkpoint_locks([sys.argv[1]],wait=False): pass\nexcept CheckpointBusy: sys.exit(23)\n"
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(destination)], capture_output=True, timeout=10
        )
        assert result.returncode == 23, result.stderr
        calls.append(sha256(raw))
        return observe()

    result = create(lab, "new", after_complete)
    bundle = evidence.read_bundle(destination, backup.load_backup(destination)[1])
    assert result["ok"] and result["clock_evidence_state"] == "available"
    assert bundle["bundle"]["identity"]["manifest_sha256"] == calls[0]
    assert len(calls) == 1


def test_clock_failure_preserves_data_order_and_later_first_witness(lab):
    _, reg, samples, observe = lab

    def unavailable():
        raise OSError("provider unavailable")

    first = create(lab, "a", unavailable)
    assert first["ok"] and first["clock_evidence_state"] == "missing"
    path = Path(first["path"]) / evidence.SIDECAR
    original = path.read_bytes()
    ident = entry(first)
    ident = {k: ident[k] for k in ("backup_id", "manifest_sha256", "source_root", "scope")}
    assert reg.graph().proof(ident) is None
    samples.advance(100)
    with reg.lock():
        witness = reg.observe(ident, observe)
        again = reg.observe(ident, unavailable)
    assert again == witness and path.read_bytes() == original
    second = create(lab, "b")
    bundle = evidence.read_bundle(second["path"])["bundle"]
    assert witness in bundle["objects"]
    graph = evidence.Graph(bundle["objects"], reg.scope)
    assert first["backup_id"] in graph.ancestors(second["backup_id"])
    assert graph.proof(ident)["object_id"] == witness


@pytest.mark.parametrize("stage", ["before_marker", "after_marker", "sidecar"])
def test_interrupt_boundaries_never_invent_completion(lab, monkeypatch, stage):
    _, reg, _, _ = lab
    observer_calls = []
    if stage == "before_marker":
        original = backup.atomic_write

        def fail(path, value):
            if Path(path).name == "backup.json":
                raise OSError("interrupted marker")
            return original(path, value)

        monkeypatch.setattr(backup, "atomic_write", fail)
    elif stage == "after_marker":
        monkeypatch.setattr(
            evidence.Producer, "complete", lambda *a: (_ for _ in ()).throw(SystemExit("crash"))
        )
    else:
        original = evidence.immutable

        def fail(path, value):
            if Path(path).name == evidence.SIDECAR:
                raise OSError("sidecar fsync failure")
            return original(path, value)

        monkeypatch.setattr(evidence, "immutable", fail)
    root = reg.root.parent / "copies" / stage
    if stage == "sidecar":
        result = create(lab, stage)
        assert result["ok"] and result["clock_evidence_state"] == "missing"
        assert result["backup_id"] in reg.graph().checkpoints
        assert not (root / evidence.SIDECAR).exists()
    else:
        with pytest.raises((OSError, SystemExit)):
            create(lab, stage, lambda: observer_calls.append(True))
        assert not observer_calls and not reg.graph().checkpoints
        assert (root / "backup.json").exists() is (stage == "after_marker")


def test_missing_registry_cannot_block_valid_backup_or_fake_order(lab):
    store, reg, _, observe = lab
    absent = evidence.Registry(reg.root.parent / "not-installed", reg.scope, "d" * 64)
    result = backup.create_backup(
        store,
        reg.root.parent / "without-baseline",
        min_free_gib=0,
        evidence=evidence.Producer(absent, observe),
    )
    assert result["ok"] and result["clock_evidence_state"] == "missing"
    assert not absent.root.exists() and not (Path(result["path"]) / evidence.SIDECAR).exists()


def test_same_source_immutable_baseline_and_registry_conflict(lab):
    _, reg, _, _ = lab
    result = create(lab, "a")
    with reg.lock():
        with pytest.raises(BlockingIOError):
            with reg.lock():
                pass
        with pytest.raises(DataError, match="cannot be replaced"):
            reg.baseline([evidence.read_bundle(result["path"])["bundle"]["identity"]])
    bundle = evidence.read_bundle(result["path"])["bundle"]
    with pytest.raises(DataError, match="Immutable"):
        evidence.immutable(Path(result["path"]) / evidence.SIDECAR, {**bundle, "schema": 9})


def test_restore_keeps_evidence_without_mutating_original_manifest(lab):
    _, reg, _, _ = lab
    result = create(lab, "a")
    marker = Path(result["path"]) / "backup.json"
    original = marker.read_bytes()
    receipt = backup.restore_backup(result["path"], reg.root.parent / "restored", min_free_gib=0)
    record = receipt["checkpoint_evidence"]
    restored = Path(receipt["path"]) / record["path"]
    assert sha256(restored.read_bytes()) == record["sha256"]
    assert not record["deletion_authority_restored"]
    assert (
        evidence.decode_bundle(restored.read_bytes())["bundle"]["identity"]["backup_id"]
        == result["backup_id"]
    )
    assert marker.read_bytes() == original
    inventory = retirement.tree_inventory(
        Path(result["path"]), backup.load_backup(result["path"])[1]
    )
    assert evidence.SIDECAR in inventory["records"]
    (Path(result["path"]) / "unknown-file").write_text("not authorized")
    with pytest.raises(ValueError, match="Unexpected/missing"):
        retirement.tree_inventory(Path(result["path"]), backup.load_backup(result["path"])[1])


def test_archived_graph_is_required_and_recent_three_uses_causality(lab):
    _, reg, samples, _ = lab
    results = [create(lab, str(i)) for i in range(4)]
    entries = [entry(r) for r in results]
    # Dates deliberately suggest the oldest is newest. They are not the order proof.
    entries[0]["checkpoint_at"] = "2100-01-01T00:00:00Z"
    graph = reg.graph()
    first_upper = graph.proof(
        {k: entries[0][k] for k in ("backup_id", "manifest_sha256", "source_root", "scope")}
    )["completion_upper_utc_ns"]
    threshold = first_upper + 259260 * 10**9
    keeps, candidates, proofs = evidence.select(entries, graph, threshold - 1)
    assert len(keeps) == 4 and not candidates
    keeps, candidates, proofs = evidence.select(entries, graph, threshold)
    assert [e["backup_id"] for e in candidates] == [results[0]["backup_id"]]
    assert len(proofs[results[0]["backup_id"]]["retained_successors"]) == 3
    keeps, candidates, _ = evidence.select(entries, evidence.Graph({}, reg.scope), threshold)
    assert len(keeps) == 4 and not candidates
    with pytest.raises(DataError, match="Duplicate live"):
        evidence.select(entries + [entries[0]], graph, threshold)


def test_missing_ancestor_or_conflicting_manifest_is_not_accepted(lab):
    _, reg, _, _ = lab
    first, second = create(lab, "a"), create(lab, "b")
    raw = evidence.read_bundle(second["path"])["bundle"]
    missing = copy.deepcopy(raw)
    del missing["objects"][reg.graph().checkpoints[first["backup_id"]]]
    with pytest.raises(DataError, match="predecessor"):
        evidence.decode_bundle(json_bytes(missing))
    ident = evidence.read_bundle(first["path"])["bundle"]["identity"]
    with pytest.raises(DataError, match="manifest binding"):
        reg.graph().proof({**ident, "manifest_sha256": "0" * 64})


def test_fresh_process_can_read_restore_and_back_up_evidenced_data(lab):
    _, reg, _, _ = lab
    result = create(lab, "new")
    base = Path(__file__).resolve().parents[1]
    code = """
import sys
from pathlib import Path
sys.path[:0] = [sys.argv[1] + '/packages/marketdata/src']
from marketdata import backup
from marketdata.store import Store
source, restored, new = map(Path, sys.argv[2:])
assert backup.verify_backup(source)['ok']
assert backup.restore_backup(source, restored, min_free_gib=0)['ok']
store = Store(restored)
try:
 store.put_raw(b'post-rollback collection', 'test:rollback', 'test')
 assert backup.create_backup(store, new, min_free_gib=0)['ok']
 assert backup.verify_backup(new)['ok']
finally:
 store.close()
assert (source / 'checkpoint-evidence.json').is_file()
"""
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-c",
            code,
            str(base),
            result["path"],
            str(reg.root.parent / "old-restored"),
            str(reg.root.parent / "old-new-backup"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr


def test_evidence_dropins_preserve_existing_prechecks_and_timers():
    from scripts.render_retirement_units import evidence_dropins

    values = evidence_dropins("/opt/fixed", "/etc/md/settings.toml", "/etc/md/archive.toml")
    assert len(values) == 2
    assert all(
        "ExecStart=\nExecStart=" in value and "ExecStartPre" not in value and "[Timer]" not in value
        for value in values.values()
    )
    assert (
        "--min-free-gib 10 --max-files 20000"
        in values["marketdata-backup.service.d/80-checkpoint-evidence.conf"]
    )
