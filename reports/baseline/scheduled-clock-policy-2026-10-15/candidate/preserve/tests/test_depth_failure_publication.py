"""Commit visibility, mirror/DB faults, durable bounds, retries and forced exit."""

import json
import selectors
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from depth_failure_harness import Rig, saved
from marketdata import depth
from marketdata.domain import DataError
from marketdata.store import Store


def stable_bounds(report):
    return {
        k: report.get(k)
        for k in (
            "messages",
            "persisted_messages",
            "snapshots",
            "last_received_at",
            "last_update_id",
            "epochs",
        )
    }


def run_cli(root, command="depth-status"):
    result = subprocess.run(
        [sys.executable, "-m", "marketdata.cli", "--root", str(root), command],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    # The installed console script's main is also used below if cli has no -m entry.
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout)


def spawn_paused(tmp_path, remaining):
    root, result = tmp_path / "store", tmp_path / "worker-result.json"
    child = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("depth_failure_harness.py")),
            "--root",
            str(root),
            "--remaining",
            str(remaining),
            "--result",
            str(result),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=15), "collector did not reach the persistence barrier"
        line = child.stdout.readline()
        assert line, child.stderr.read()
        assert json.loads(line)["blocked_before_persistence"]
        return child, root, result
    except BaseException:
        child.kill()
        child.communicate(timeout=10)
        raise


@pytest.mark.parametrize("remaining", [0, 8])
def test_external_cli_rejects_while_event_persistence_is_blocked(tmp_path, remaining):
    child, root, result_path = spawn_paused(tmp_path, remaining)
    try:
        value = saved(root)
        assert value["healthy"] is False and value["report"]["sync_losses"] == 1
        report = value["report"]
        assert report["messages"] == report["persisted_messages"] == 2
        assert report["last_update_id"] == 102
        assert report["epochs"][0]["final"]["book"]["last_update_id"] == 102
        assert report["failure_notice"]["observed_update_id"] == 103
        assert report["last_received_at"] < report["failure_notice"]["observed_received_at"]
        code, status = run_cli(root)
        assert code == 2 and status["healthy"] is False and status["report"] == value["report"]
        assert value["last_receive_age_seconds"] <= status["last_receive_age_seconds"] < 10
        latest = json.loads((root / "state/depth-latest.json").read_bytes())
        assert latest == report
        child.communicate("resume\n", timeout=15)
        assert child.returncode == 0
        result = json.loads(result_path.read_bytes())
        assert result["error"] is None and result["final"]["state"] == "partial"
        assert result["final"]["report"]["sync_losses"] == 1
        assert result["replay"]["replay_matches_live"] and not result["replay"]["valid"]
        assert result["final"]["report"]["epochs"][-1]["final"]["book"]["top_complete"]
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


@pytest.mark.parametrize("remaining", [0, 8])
@pytest.mark.parametrize(
    "mode",
    [
        "json_once",
        "json_always",
        "json_after_flush_once",
        "raw_once",
        "raw_always",
        "db_once",
        "db_always",
        "commit_once",
        "commit_always",
    ],
)
def test_failure_publication_faults_through_collector_exception_handler(tmp_path, remaining, mode):
    result = Rig(tmp_path / "store", remaining, mode).run()
    assert result["error"] is not None
    baseline = next(e for e in result["events"] if e["phase"] == "healthy_before_failure")
    assert baseline["healthy"]
    final, latest = result["final"], result["latest_json"]
    if mode in {"db_always", "commit_always"}:
        # Publication itself never committed. The old healthy report can remain
        # visible until stale; do not claim it was invalidated out-of-process.
        assert final["healthy"] and final["report"]["sync_losses"] == 0
        assert stable_bounds(final["report"]) == stable_bounds(baseline["report"])
        assert result["counts"]["raw"] == 0 and "failure_notice" not in latest
    else:
        assert not final["healthy"] and final["state"] == "failed"
        assert final["report"]["sync_losses"] == final["report"]["coverage_resets"] == 1
        assert final["report"]["coverage_top_shortfalls"] == int(remaining == 8)
        assert not result["replay"]["valid"]
        assert final["report"]["persisted_messages"] == result["update_rows"]
        for event in result["events"]:
            if event["phase"] in {"db_committed_before_json", "before_failed_event_persistence"}:
                assert not event["healthy"] and event["report"]["sync_losses"] == 1
        if mode == "json_always":
            assert stable_bounds(final["report"]) == stable_bounds(baseline["report"])
            assert result["counts"]["raw"] == 0
            assert latest == baseline["report"]  # DB failed-state and stale JSON are distinct.
        elif mode == "raw_always":
            assert stable_bounds(final["report"]) == stable_bounds(baseline["report"])
            assert final["report"]["persisted_messages"] == 2
            assert "flush_error" in final["report"]
        else:
            assert final["report"]["persisted_messages"] == 3
            assert latest == final["report"]
        notices = [
            e["report"]["failure_notice"]
            for e in result["events"]
            if "failure_notice" in e["report"]
        ]
        assert len({n["recorded_at"] for n in notices}) == 1


@pytest.mark.parametrize("remaining", [0, 8])
def test_two_failures_stay_monotonic_and_older_retry_cannot_replace_latest(tmp_path, remaining):
    result = Rig(tmp_path / "store", remaining, "repeat_shock").run()
    assert result["error"] is None
    report = result["final"]["report"]
    assert report["sync_losses"] == report["coverage_resets"] == 2
    assert report["coverage_top_shortfalls"] == 2 * int(remaining == 8)
    assert report["state"] == "partial" and not result["replay"]["valid"]
    assert result["replay"]["replay_matches_live"]
    observed = [e["report"]["sync_losses"] for e in result["events"]]
    assert observed == sorted(observed)
    first = next(
        e["report"]["failure_notice"] for e in result["events"] if "failure_notice" in e["report"]
    )
    last = report["failure_notice"]
    assert last["sequence"] > first["sequence"] and last["observed_update_id"] == 105
    store = Store(tmp_path / "store")
    try:
        with pytest.raises(DataError, match="older failure"):
            depth.publish_failure(store, first)
        depth.publish_failure(store, last)
        final = saved(store.root)["report"]
        assert final["state"] == "partial" and final["sync_losses"] == 2
        assert final["failure_notice"] == last
    finally:
        store.close()


@pytest.mark.parametrize("remaining", [0, 8])
def test_force_kill_after_publication_preserves_notice_and_restart_boundary(tmp_path, remaining):
    child, root, _ = spawn_paused(tmp_path, remaining)
    before = saved(root)
    child.kill()
    child.communicate(timeout=10)
    assert child.returncode != 0
    assert saved(root)["report"] == before["report"] and saved(root)["healthy"] is False
    notice = before["report"]["failure_notice"]
    store = Store(root)
    try:
        original = deepcopy(notice)
        for _ in range(2):
            assert depth.publish_failure(store, notice) == original
        assert notice == original
        after = saved(root)
        assert not after["healthy"] and after["report"]["sync_losses"] == 1
        assert stable_bounds(after["report"]) == stable_bounds(before["report"])
    finally:
        store.close()
    restarted = Rig(root, remaining, "no_shock").run()
    assert restarted["error"] is None and restarted["final"]["state"] == "complete"
    with sqlite3.connect(root / "state/catalog.sqlite") as db:
        record = db.execute(
            "SELECT state,report FROM event_collections WHERE id=?", (notice["collection_id"],)
        ).fetchone()
    assert record[0] == "interrupted"
    previous = json.loads(record[1])
    assert previous["sync_losses"] == 1 and previous["failure_notice"] == original
    assert not previous["complete"]


def test_publication_rejects_nested_transaction_and_conflicting_retry(tmp_path):
    result = Rig(tmp_path / "store", 8, "raw_always").run()
    store = Store(tmp_path / "store")
    try:
        notice = result["final"]["report"]["failure_notice"]
        store.db.execute("BEGIN")
        with pytest.raises(DataError, match="own committed transaction"):
            depth.publish_failure(store, notice)
        store.db.rollback()
        changed = {**notice, "sync_losses": 2}
        with pytest.raises(DataError, match="Conflicting"):
            depth.publish_failure(store, changed)
        assert saved(store.root)["report"]["sync_losses"] == 1
        before = deepcopy(notice)
        depth.publish_failure(store, notice)
        assert notice == before and saved(store.root)["state"] == "failed"
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["raw_always", "raw_once", "json_once", "json_always"])
def test_normal_book_flush_faults_preserve_confirmed_bounds_without_reconnecting(tmp_path, mode):
    result = Rig(tmp_path / "store", 8, "healthy_" + mode).run()
    baseline = next(e for e in result["events"] if e["phase"] == "healthy_before_failure")
    assert baseline["healthy"] and result["error"] is not None
    final, report = result["final"], result["final"]["report"]
    assert not final["healthy"] and final["state"] == "failed"
    assert "failure_notice" not in report and report["sync_losses"] == 0
    assert report["coverage_resets"] == report["coverage_top_shortfalls"] == 0
    assert report["connections"] == 1 and report["connection_errors"] == 0
    assert not any(e["phase"] == "before_recovery_snapshot" for e in result["events"])
    assert report["persisted_messages"] == result["update_rows"]
    assert result["replay"]["replay_matches_live"] and not result["replay"]["valid"]
    if mode == "raw_always":
        assert result["counts"]["raw"] == 2
        assert stable_bounds(report) == stable_bounds(baseline["report"])
        observed = report["flush_failure_observation"]
        assert observed["messages_received"] == 3
        assert observed["book_update_id"] == 103
        assert observed["last_received_at"] > report["last_received_at"]
        assert observed["persisted_messages"] == 2
    else:
        assert report["messages"] == report["persisted_messages"] == 3
        assert report["last_update_id"] == 103
        assert report["epochs"][0]["final"]["book"]["last_update_id"] == 103
    if mode == "json_always":
        assert result["latest_json"] == baseline["report"]
    else:
        assert result["latest_json"] == report
