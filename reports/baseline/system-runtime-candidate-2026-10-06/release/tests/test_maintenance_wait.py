"""Exercise OS lock contention through the real maintenance CLI."""

import json
import os
import select
import signal
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest
from marketdata import maintenance as ops
from marketdata.domain import WriterBusy, utc
from marketdata.store import Store
from test_maintenance import closed_events


@contextmanager
def waiting_cli(store, mode="hourly"):
    # Use the source tree under test, including when tests run in an isolated
    # release copy with a virtualenv whose editable install points elsewhere.
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(ops.__file__).resolve().parents[1])
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "marketdata.cli",
            "--root",
            str(store.root),
            "maintenance",
            "--mode",
            mode,
            "--min-free-gib",
            "0",
            "--wait-for-lock",
        ],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def assert_waiting(process):
    assert select.select([process.stderr], [], [], 10)[0], "No lock-wait progress event"
    assert json.loads(process.stderr.readline()) == {"phase": "waiting_for_maintenance_lock"}
    assert process.poll() is None


@pytest.mark.parametrize("mode", ["hourly", "daily"])
def test_waiting_job_preserves_report_and_allows_collection_then_completes(store, mode):
    closed_events(store)
    latest = store.root / f"state/maintenance-{mode}-latest.json"
    latest.write_bytes(b"report owned by prior job")
    with ExitStack() as held:
        held.enter_context(ops.maintenance_lock(store))
        with waiting_cli(store, mode) as process:
            assert_waiting(process)
            assert latest.read_bytes() == b"report owned by prior job"
            assert not (store.root / "reports/maintenance").exists()
            # A second store mirrors a separate collector process. The waiting CLI
            # must not reserve the writer or leave a SQLite transaction open.
            collector = Store(store.root)
            try:
                with collector.writer():
                    identity = collector.put_raw(b"collected while waiting", "test:wait", "test")
            finally:
                collector.close()
            held.close()
            output, errors = process.communicate(timeout=20)
    assert process.returncode == 0, errors
    report = json.loads(output)
    assert report["ok"] and report["packing"]["files"] == 8
    assert report["new_prune"]["removed"] == 8
    assert report["wait_for_lock"] and report["lock_wait_seconds"] > 0
    assert utc(report["requested_at"]) < utc(report["started_at"])
    assert report["logical_files_deleted"] == 0
    assert json.loads(latest.read_bytes()) == report
    assert store.raw(identity)[0] == b"collected while waiting"
    assert store.validate()["ok"]
    if mode == "daily":
        assert report["validation"]["ok"] and not report["backup"]["reused"]
        assert (Path(report["backup"]["path"]) / "backup.json").is_file()


@pytest.mark.parametrize("mode", ["hourly", "daily"])
def test_sigint_cancels_wait_without_overwriting_report_or_creating_backup(store, mode):
    latest = store.root / f"state/maintenance-{mode}-latest.json"
    latest.write_bytes(b"current job's report")
    with ops.maintenance_lock(store), waiting_cli(store, mode) as process:
        assert_waiting(process)
        process.send_signal(signal.SIGINT)
        output, errors = process.communicate(timeout=10)
        assert process.returncode == 130, (output, errors)
        assert json.loads(errors)["state"] == "interrupted"
        assert latest.read_bytes() == b"current job's report"
        assert not (store.root / "reports/maintenance").exists()
        assert not (store.root / "backups").exists()
    # Closing an interrupted wait must leave no leaked lock behind.
    assert ops.run(store, mode=mode, min_free_gib=0)["ok"]


def test_validation_failure_after_wait_remains_a_failure_and_does_not_back_up(store):
    with store.writer():
        identity = store.put_raw(b"source", "test:source", "test")
    path = store.db.execute("SELECT path FROM artifacts WHERE id=?", (identity,)).fetchone()[0]
    (store.root / path).write_bytes(b"damaged")
    with ExitStack() as held:
        held.enter_context(ops.maintenance_lock(store))
        with waiting_cli(store, "daily") as process:
            assert_waiting(process)
            held.close()
            output, errors = process.communicate(timeout=20)
    assert process.returncode == 1, (output, errors)
    report = json.loads((store.root / "state/maintenance-daily-latest.json").read_bytes())
    assert report["state"] == "failed" and report["phase"] == "validate"
    assert not report["ok"] and not report["validation"]["ok"]
    assert report["wait_for_lock"] and report["lock_wait_seconds"] > 0
    assert not (store.root / "backups").exists()


def test_wait_does_not_swallow_writer_failure_after_acquiring_maintenance_lock(store, monkeypatch):
    def busy(*args, **kwargs):
        raise WriterBusy("collector is busy")

    monkeypatch.setattr(ops, "pack_events", busy)
    with pytest.raises(WriterBusy, match="collector is busy"):
        ops.run(store, min_free_gib=0, wait_for_lock=True)
    report = json.loads((store.root / "state/maintenance-hourly-latest.json").read_bytes())
    assert report["state"] == "failed" and report["phase"] == "pack"
    assert not report["ok"]
