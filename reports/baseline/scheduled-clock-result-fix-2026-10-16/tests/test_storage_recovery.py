"""Regression expectations for resource lifetime and historical result recovery."""

# ruff: noqa: F811 -- pytest imports fixtures and injects parameters with their names.
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from marketdata import cli
from marketdata import job_clock as jc
from test_clock_decisions import load_runner
from test_wrapper_recovery import wrapper  # noqa: F401

HERE = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", ["found", "missing", "query_error"])
def test_record_connection_is_closed_on_all_paths(tmp_path, monkeypatch, case):
    contract = load_runner().load_contract()
    path = tmp_path / "record.sqlite"
    db = sqlite3.connect(path)
    if case != "query_error":
        db.execute("CREATE TABLE daily_runs(id TEXT PRIMARY KEY, record TEXT)")
        db.execute("INSERT INTO daily_runs VALUES ('run', '{}')")
        db.commit()
    db.close()
    original, retained = sqlite3.connect, []

    def connect(*a, **k):
        db = original(*a, **k)
        retained.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    ref = dict(table="daily_runs", path=str(path), id="absent" if case == "missing" else "run")
    if case == "found":
        assert contract.record_bytes(ref) == b"{}"
    else:
        with pytest.raises(
            contract.RequestError if case == "missing" else sqlite3.OperationalError
        ):
            contract.record_bytes(ref)
    assert len(retained) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        retained[0].execute("SELECT 1")


def prepare(wrapper, monkeypatch, job):
    if job == "oi-probe":
        from marketdata.oi_probe import probe_once
        from test_oi_probe import START, Fetch

        wrapper.samples.wall = START.timestamp()
        source = Fetch(wrapper.store)
        monkeypatch.setattr(
            cli, "probe_once", lambda store: probe_once(store, source, clock=lambda: START)
        )
        args = ["--root", str(wrapper.store.root), "oi-probe"]
        return args, wrapper.store.root, wrapper.store.root / "state/oi-probe-latest.json"
    from scripts import network_observer as observer

    output = wrapper.root / "network"
    profile = wrapper.root / "network.toml"
    profile.write_text(f'version=1\noutput_root="{output}"\n')
    monkeypatch.setattr(
        observer,
        "observe",
        lambda _: {
            "started_at": "2026-09-22T12:00:01Z",
            "finished_at": "2026-09-22T12:00:02Z",
            "status": "degraded",
            "failed_probes": ["dns"],
            "probes": {"dns": {"ok": False, "detail": "원래 관측"}},
        },
    )

    def invoke(job, args):
        monkeypatch.setattr(sys, "argv", [job, *args])
        return observer.main()

    monkeypatch.setattr(wrapper.runner, "invoke", invoke)
    # Exercise a nonzero byte offset and multibyte UTF-8, not text stream cookies.
    observer.run(profile)
    return ["--profile", str(profile)], profile, output / "latest.json"


def unfinished(wrapper, monkeypatch, job, args, boundary):
    close = wrapper.context_class.close
    contract = wrapper.runner.load_contract()
    seal = contract.seal_result
    monkeypatch.setattr(wrapper.runner, "load_contract", lambda: contract)
    if boundary == "close":

        def fail(c):
            close(c)
            raise jc.ClockFailure("injected_close_failure")

        monkeypatch.setattr(wrapper.context_class, "close", fail)
    else:
        monkeypatch.setattr(
            contract, "seal_result", lambda _: (_ for _ in ()).throw(OSError("seal interrupted"))
        )
    with pytest.raises(BaseExceptionGroup):
        wrapper.run(job, args)
    before = wrapper.request(job)
    assert before["state"] == "finalization_failed"
    assert ("work_result" in before) == (boundary == "close")
    monkeypatch.setattr(wrapper.context_class, "close", close)
    monkeypatch.setattr(contract, "seal_result", seal)
    return before, contract


@pytest.mark.parametrize("job", ["oi-probe", "network-observer"])
@pytest.mark.parametrize("boundary", ["close", "seal"])
def test_old_runtime_updates_latest_then_reapply_recovers_original(
    wrapper, monkeypatch, job, boundary
):
    args, target, latest = prepare(wrapper, monkeypatch, job)
    before, contract = unfinished(wrapper, monkeypatch, job, args, boundary)
    original = json.loads(latest.read_bytes())
    parents = json.loads((HERE / "parents.json").read_bytes())
    parent = Path(parents["variants"]["base"])
    relative = (
        "packages/marketdata/src/marketdata/oi_probe.py"
        if job == "oi-probe"
        else "scripts/network_observer.py"
    )
    assert (
        hashlib.sha256((parent / relative).read_bytes()).hexdigest()
        == parents["files"]["base/" + relative]["sha256"]
    )
    completed = subprocess.run(
        [
            str(parent / ".venv/bin/python"),
            "-B",
            "-I",
            str(HERE / "tests/old_observation.py"),
            job,
            str(parent),
            str(target),
            "2026-09-22T12:05:01+00:00",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode in (0, 2), completed.stderr
    executed = json.loads(completed.stdout.strip().splitlines()[-1])
    assert executed["module"].startswith(str(parent) + "/")
    later = latest.read_bytes()
    assert json.loads(later) != original
    if job == "oi-probe":
        assert json.loads(later)["source_id"] == original["source_id"]
        assert json.loads(later)["probe_id"] != original["probe_id"]
        assert wrapper.store.db.execute("SELECT COUNT(*) FROM fetches").fetchone()[0] == 2
    monkeypatch.setattr(
        wrapper.runner, "invoke", lambda *a: pytest.fail("must not repeat observation A")
    )
    assert wrapper.run(job, args) in (0, 2)
    after = wrapper.request(job)
    assert after["state"] == "complete"
    assert after["request_id"] == before["request_id"] and after["pins"] == before["pins"]
    assert after["attempt"]["work"]["recovered_committed_result"]
    assert latest.read_bytes() == later
    assert (
        after["work_result"]["result"]
        == before.get("work_result", before.get("unsealed_result"))["result"]
    )
    if boundary == "close":
        assert after["work_result"] == before["work_result"]
    assert not after["work_result"]["files"]


@pytest.mark.parametrize("job", ["oi-probe", "network-observer"])
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_missing_or_changed_original_history_blocks_recovery(wrapper, monkeypatch, job, damage):
    args, _, latest = prepare(wrapper, monkeypatch, job)
    before, _ = unfinished(wrapper, monkeypatch, job, args, "close")
    if job == "oi-probe":
        ref = before["work_result"]["records"][0]
        with wrapper.store.db:
            if damage == "missing":
                wrapper.store.db.execute("DELETE FROM fetches WHERE id=?", (ref["id"],))
            else:
                wrapper.store.db.execute(
                    "UPDATE fetches SET metadata='{}' WHERE id=?", (ref["id"],)
                )
    else:
        ref = before["work_result"]["ranges"][0]
        path = Path(ref["path"])
        if damage == "missing":
            path.write_bytes(path.read_bytes()[: ref["offset"]])
        else:
            raw = bytearray(path.read_bytes())
            raw[ref["offset"] + 3] ^= 1
            path.write_bytes(raw)
    assert latest.exists()  # A valid latest view must not substitute for missing history.
    monkeypatch.setattr(
        wrapper.runner, "invoke", lambda *a: pytest.fail("must not repeat uncertain work")
    )
    with pytest.raises(BaseExceptionGroup):
        wrapper.run(job, args)
    after = wrapper.request(job)
    assert after["state"] == "finalization_failed" and after["pins"] == before["pins"]
    assert after["work_result"] == before["work_result"]


def test_ambiguous_probe_history_is_not_resolved_by_latest(wrapper, monkeypatch):
    args, _, _ = prepare(wrapper, monkeypatch, "oi-probe")
    before, _ = unfinished(wrapper, monkeypatch, "oi-probe", args, "seal")
    with wrapper.store.db:
        wrapper.store.db.execute(
            "INSERT INTO fetches(artifact_id,url,observed_at,metadata) "
            "SELECT artifact_id,url,observed_at,metadata FROM fetches"
        )
    monkeypatch.setattr(wrapper.runner, "invoke", lambda *a: pytest.fail("must not observe again"))
    with pytest.raises(BaseExceptionGroup):
        wrapper.run("oi-probe", args)
    assert wrapper.request("oi-probe")["unsealed_result"] == before["unsealed_result"]
