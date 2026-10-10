import json
import signal
import sqlite3
import subprocess
import sys
from datetime import timedelta

import pytest
from marketdata import backup
from marketdata.binance import Fetcher
from marketdata.daily_preview import preview
from marketdata.daily_review import apply_review, inspect_task, task_hash
from marketdata.daily_schedule import Scheduler
from marketdata.domain import DAY, DataError, Key, WriterBusy, json_bytes, utc
from marketdata.fileio import layout_lock
from marketdata.pipeline import Collector
from marketdata.store import Store
from test_daily_schedule import DAY0, tasks
from test_daily_schedule import env as shared_env

env = shared_env


@pytest.fixture(autouse=True)
def publication_clock(env, monkeypatch):
    monkeypatch.setattr("marketdata.store.now", env[0])


def request(task, action="retry", identity="review-1", extra=1):
    return {
        "request_id": identity,
        "task_id": task["id"],
        "expected_task_sha256": task_hash(task),
        "action": action,
        "reason": "Operator checked the specific failure",
        "extra_attempts": extra if action == "retry" else 0,
    }


def failed(env, *, transient=False, **settings):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    url = upstream.add(key)
    upstream.responses[url] = (503 if transient else 400, b"error", {})
    runner = build([key], refresh=0, **settings)
    runner.run()
    return runner, key, url


def fill_old(store, runner, key, day=DAY0):
    Collector(store, Fetcher(client=runner.client, sleep=lambda _: None), cooperative=True).archive(
        key, utc(day), utc(day) + DAY, monthly=False, refresh=True
    )


def seed(runner, key, *, purpose="refresh", day=DAY0, cycle="2024-01-15"):
    task = runner.task(key, day, purpose, cycle, runner.clock())
    task.update(state="needs_review", next_at=None, review_required=True, reason="fixture_review")
    with runner.ledger.exclusive():
        runner.ledger.save("daily_tasks", task)
    return json.loads(json_bytes(task))


def test_reconcile_legacy_acquisition_no_http_preserves_history_and_other_tasks(store, env):
    clock, upstream, _ = env
    runner, key, _ = failed(env)
    before = tasks(runner)[0]
    upstream.add(key)
    fill_old(store, runner, key)
    runner.run()  # existing policy deliberately leaves needs_review untouched
    assert tasks(runner)[0]["state"] == "needs_review"
    attempt_rows = runner.ledger.rows("daily_attempts")
    calls = len(upstream.calls)
    receipt = apply_review(runner, request(before, "reconcile"))
    assert receipt["after"]["state"] == "complete"
    assert receipt["before"] == before
    assert receipt["after"]["attempts"] == before["attempts"]
    assert runner.ledger.rows("daily_attempts") == attempt_rows
    assert len(upstream.calls) == calls
    assert inspect_task(store.root, before["id"])["reviews"] == [receipt]


def test_retry_is_bounded_idempotent_and_keeps_failure_counters(store, env):
    clock, upstream, _ = env
    runner, key, url = failed(env, transient=True, transient_attempts=1)
    before = tasks(runner)[0]
    req = request(before, extra=2)
    receipt = apply_review(runner, req)
    assert apply_review(runner, req) == receipt
    assert len(runner.ledger.rows("daily_reviews")) == 1
    assert len(upstream.calls) == 4
    runner.run()
    assert tasks(runner)[0]["manual_retry"]["remaining"] == 1
    assert tasks(runner)[0]["state"] == "retry_wait"
    assert apply_review(runner, req) == receipt  # does not rewind state/budget
    assert tasks(runner)[0]["manual_retry"]["remaining"] == 1
    clock.value += timedelta(minutes=5)
    runner.run()
    after = tasks(runner)[0]
    assert after["state"] == "needs_review" and after["transient_failures"] == 3
    assert len(after["attempts"]) == 3 and len(upstream.calls) == 12
    runner.run()
    assert len(upstream.calls) == 12
    assert [a["manual_request_id"] for a in runner.ledger.rows("daily_attempts")[1:]] == [
        req["request_id"]
    ] * 2


@pytest.mark.parametrize(
    "field,value", [("extra_attempts", 2), ("reason", "different"), ("task_id", "0" * 64)]
)
def test_request_id_reuse_with_different_content_is_refused(store, env, field, value):
    runner, _, _ = failed(env)
    req = request(tasks(runner)[0])
    apply_review(runner, req)
    with pytest.raises(DataError, match="different contents"):
        apply_review(runner, dict(req, **{field: value}))
    assert tasks(runner)[0]["manual_retry"]["remaining"] == 1


@pytest.mark.parametrize("extra", [0, 4, -1, True, "1"])
def test_retry_rejects_unbounded_or_ambiguous_budget(store, env, extra):
    runner, _, _ = failed(env)
    with pytest.raises(DataError, match="1..3"):
        apply_review(runner, request(tasks(runner)[0], extra=extra))
    assert tasks(runner)[0]["state"] == "needs_review"


def test_stale_task_inspection_and_unconfigured_target_refused(store, env):
    runner, _, _ = failed(env)
    before = tasks(runner)[0]
    req = request(before)
    changed = dict(before, reason="new evidence")
    runner.ledger.save("daily_tasks", changed)
    with pytest.raises(DataError, match="changed since"):
        apply_review(runner, req)
    other = env[2]([Key("mark_price_bars", interval="1h")], refresh=0)
    with pytest.raises(DataError, match="scope differs"):
        apply_review(other, request(changed))


def test_retry_does_not_spend_grant_or_request_during_persisted_cooldown(store, env):
    clock, upstream, _ = env
    runner, key, url = failed(env)
    task = tasks(runner)[0]
    runner.cooldown(url, {"retry-after": "3600"})
    apply_review(runner, request(task))
    upstream.add(key)
    calls = len(upstream.calls)
    runner.run()
    after = tasks(runner)[0]
    assert after["state"] == "cooldown" and after["manual_retry"]["remaining"] == 1
    assert len(upstream.calls) == calls
    clock.value += timedelta(seconds=3600)
    runner.run()
    assert tasks(runner)[0]["state"] == "complete"
    assert tasks(runner)[0]["manual_retry"]["remaining"] == 0
    assert len(upstream.calls) == calls + 2


def test_refresh_requires_new_remote_confirmation_and_publication(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)
    runner = build([key], refresh=0)
    fill_old(store, runner, key)
    clock.value += timedelta(hours=1)
    task = seed(runner, key)
    req = request(task, "reconcile")
    calls = len(upstream.calls)
    with pytest.raises(DataError, match="No publication"):
        apply_review(runner, req)
    assert len(upstream.calls) == calls
    fill_old(store, runner, key)  # identical bytes but a NEW successful fetch/publication
    calls = len(upstream.calls)
    receipt = apply_review(runner, req)
    assert receipt["after"]["state"] == "complete" and len(upstream.calls) == calls
    proof = receipt["after"]["proof"]["review_confirmation"]
    assert len(proof["fetches"]) == 2
    assert all(utc(f["observed_at"]) >= utc(task["first_eligible_at"]) for f in proof["fetches"])


def test_new_download_without_publication_cannot_settle_refresh(store, env, monkeypatch):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)
    runner = build([key], refresh=0)
    fill_old(store, runner, key)
    clock.value += timedelta(hours=1)
    task = seed(runner, key)
    monkeypatch.setattr(
        Collector, "_publish", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        fill_old(store, runner, key)
    with pytest.raises(DataError, match="No publication"):
        apply_review(runner, request(task, "reconcile"))
    assert next(t for t in tasks(runner) if t["id"] == task["id"])["state"] == "needs_review"


def test_review_never_accepts_conflicting_revision(store, env):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)
    runner = build([key])
    runner.run()
    original = [r[0] for r in store.db.execute("SELECT revision_id FROM active")]
    clock.value += DAY
    upstream.add(key, price="102")
    runner.run()
    task = next(t for t in tasks(runner) if t["state"] == "needs_review")
    with pytest.raises(DataError):
        apply_review(runner, request(task, "reconcile"))
    apply_review(runner, request(task))
    runner.run()
    # Existing conflict also blocks the acquisition peer. Approval cannot bypass
    # that dependency; directly exercise the ingestion validator in isolation.
    with runner.ledger.exclusive():
        runner.execute_task(next(t for t in tasks(runner) if t["id"] == task["id"]))
    assert next(t for t in tasks(runner) if t["id"] == task["id"])["state"] == "needs_review"
    assert [r[0] for r in store.db.execute("SELECT revision_id FROM active")] == original


@pytest.mark.parametrize(
    "phase", ["review_after_task_write", "review_before_commit", "review_after_commit"]
)
def test_review_crash_boundaries_are_atomic(store, env, phase):
    runner, _, _ = failed(env)
    before = tasks(runner)[0]
    req = request(before, extra=2)

    def crash(where, receipt):
        if where == phase:
            raise KeyboardInterrupt()

    runner.fault = crash
    with pytest.raises(KeyboardInterrupt):
        apply_review(runner, req)
    committed = phase == "review_after_commit"
    assert len(runner.ledger.rows("daily_reviews")) == int(committed)
    if not committed:
        assert tasks(runner)[0] == before
    runner.fault = lambda *a: None
    receipt = apply_review(runner, req)
    assert apply_review(runner, req) == receipt
    assert tasks(runner)[0]["manual_retry"]["remaining"] == 2


def test_sqlite_receipt_failure_rolls_back_task_and_budget(store, env):
    runner, _, _ = failed(env)
    before = tasks(runner)[0]
    with store.db:
        store.db.execute(
            "CREATE TRIGGER review_fail BEFORE INSERT ON daily_reviews BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        apply_review(runner, request(before))
    assert tasks(runner)[0] == before and runner.ledger.rows("daily_reviews") == []


def test_scheduler_contention_refuses_review_without_mutation(store, env):
    runner, _, _ = failed(env)
    before = tasks(runner)[0]
    with runner.ledger.exclusive(), pytest.raises(WriterBusy):
        apply_review(runner, request(before))
    assert tasks(runner)[0] == before


@pytest.mark.parametrize("phase", ["review_after_task_write", "review_after_commit"])
def test_real_sigkill_review_then_restart_has_one_grant(store, env, tmp_path, phase):
    runner, _, _ = failed(env)
    req = request(tasks(runner)[0], extra=2)
    input_path = tmp_path / "request.json"
    input_path.write_bytes(json_bytes(req))
    code = """
import json, os, signal, sys
from marketdata.store import Store
from marketdata.daily_schedule import Scheduler
from marketdata.daily_review import apply_review
from marketdata.domain import Key, utc
from marketdata.operations import SyncPolicy
store=Store(sys.argv[1])
def crash(where, receipt):
    if where == sys.argv[3]: os.kill(os.getpid(), signal.SIGKILL)
runner=Scheduler(store,[Key("trade_bars", interval="1h")],SyncPolicy(start="2024-01-14",refresh_days=0),clock=lambda:utc("2024-01-15T09:00Z"),fault=crash)
apply_review(runner,json.load(open(sys.argv[2])))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-I", "-c", code, str(store.root), str(input_path), phase],
        timeout=15,
    )
    assert result.returncode == -signal.SIGKILL
    apply_review(runner, req)
    assert len(runner.ledger.rows("daily_reviews")) == 1
    assert tasks(runner)[0]["manual_retry"]["remaining"] == 2


def test_preview_captures_wal_writes_and_keeps_all_writes_isolated(store, env, tmp_path):
    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)
    runner = build([key], refresh=0)
    fill_old(store, runner, key)
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    rows_before = store.db.execute(
        "SELECT count(*) FROM sqlite_master WHERE name LIKE 'daily_%'"
    ).fetchone()[0]
    calls = len(upstream.calls)
    destination = tmp_path / "preview"

    def during(phase, value):
        if phase == "snapshot_created":
            with pytest.raises(WriterBusy), layout_lock(store.root, exclusive=True):
                pass
            # The ongoing collector can commit while preview plans its snapshot.
            with store.writer(), store.db:
                store.db.execute(
                    "INSERT INTO jobs VALUES('ongoing','other','a','b','running',NULL,NULL,1,'now')"
                )

    result = preview(
        store.root,
        destination,
        [key],
        runner.policy,
        runner.schedule,
        current=clock(),
        fault=during,
    )
    assert result["http_requests"] == 0 and len(upstream.calls) == calls
    assert result["state_counts"] == {"verified_complete_no_acquire": 1}
    assert (
        store.db.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'daily_%'").fetchone()[
            0
        ]
        == rows_before
        == 0
    )
    with sqlite3.connect(destination / "catalog-snapshot.sqlite") as snap:
        assert snap.execute("SELECT count(*) FROM active").fetchone()[0] == 1
        assert snap.execute("SELECT count(*) FROM jobs WHERE id='ongoing'").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM jobs WHERE id='ongoing'").fetchone()[0] == 1
    assert not (store.root / "state/daily-scheduler.lock").exists()
    assert (destination / "store/state/daily-scheduler.lock").is_file()
    assert not any(p.is_symlink() for p in destination.rglob("*"))
    assert result["sqlite_temporary_storage"].startswith("connection-local MEMORY")


def test_preview_with_existing_review_and_cooldown_does_not_change_them(store, env, tmp_path):
    runner, key, url = failed(env)
    runner.cooldown(url, {"retry-after": "1800"})
    before = tasks(runner)
    with layout_lock(store.root):
        pass
    result = preview(
        store.root, tmp_path / "view", [key], runner.policy, runner.schedule, current=env[0]()
    )
    assert result["state_counts"]["needs_review"] == 1
    assert tasks(runner) == before


def test_preview_layout_conflict_and_overlapping_destination_refused(store, env, tmp_path):
    runner, key, _ = failed(env)
    with pytest.raises(DataError, match="separate"):
        preview(store.root, store.root / "preview", [key], runner.policy, runner.schedule)
    with layout_lock(store.root, exclusive=True), pytest.raises(WriterBusy):
        preview(store.root, tmp_path / "view", [key], runner.policy, runner.schedule)
    assert not (tmp_path / "view").exists()


def test_new_review_ledger_survives_legacy_backup_restore(store, env, tmp_path):
    runner, key, _ = failed(env)
    req = request(tasks(runner)[0], extra=2)
    receipt = apply_review(runner, req)
    # Existing backup requires data directory even when the only input is a failed HTTP response.
    (store.root / "data").mkdir(exist_ok=True)
    created = backup.create_backup(store, tmp_path / "backup", min_free_gib=0)
    assert backup.verify_backup(tmp_path / "backup")["backup_id"] == created["backup_id"]
    backup.restore_backup(tmp_path / "backup", tmp_path / "restored", min_free_gib=0)
    restored = Store(tmp_path / "restored")
    other = Scheduler(
        restored, [key], runner.policy, runner.schedule, client=runner.client, clock=runner.clock
    )
    try:
        assert apply_review(other, req) == receipt
        assert tasks(other)[0]["manual_retry"]["remaining"] == 2
    finally:
        other.close()
        restored.close()


def test_funding_refresh_legacy_publication_and_empty_terminal_page(store, env):
    from marketdata.binance import rest_url
    from marketdata.domain import micros

    clock, upstream, build = env
    key = Key("funding_events", interval="event")
    start, end = utc(DAY0), utc(DAY0) + DAY
    events = [
        {
            "symbol": "BTCUSDT",
            "fundingTime": micros(start + timedelta(hours=8 * i)) // 1000,
            "fundingRate": "0.0001",
        }
        for i in range(3)
    ]
    upstream.responses[rest_url(key, start, end)[0]] = (200, json_bytes(events), {})
    upstream.responses[rest_url(key, start + timedelta(hours=16, milliseconds=1), end)[0]] = (
        200,
        b"[]",
        {},
    )
    runner = build([key], refresh=0)
    task = seed(runner, key)
    Collector(store, Fetcher(client=runner.client), cooperative=True).rest(key, start, end)
    calls = len(upstream.calls)
    receipt = apply_review(runner, request(task, "reconcile"))
    assert receipt["after"]["state"] == "complete" and len(upstream.calls) == calls
    assert len(receipt["after"]["proof"]["review_confirmation"]["fetches"]) == 3


def test_metadata_actual_observation_reconciles_but_historical_retry_refused(store, env):
    clock, upstream, build = env
    key = Key("instrument_metadata", market="spot", interval="event")
    url = "https://api.binance.com/api/v3/exchangeInfo"
    upstream.responses[url] = (
        200,
        json_bytes({"symbols": [{"symbol": "BTCUSDT", "status": "TRADING"}]}),
        {},
    )
    runner = build([key], refresh=0)
    task = seed(runner, key, purpose="observe", day="2024-01-15")
    Collector(store, Fetcher(client=runner.client), cooperative=True).rest(
        key, clock() - timedelta(seconds=1), clock()
    )
    clock.value += DAY
    with pytest.raises(DataError, match="historical metadata"):
        apply_review(runner, request(task))
    receipt = apply_review(runner, request(task, "reconcile"))
    assert receipt["after"]["state"] == "complete"
    assert len(upstream.calls) == 1


def test_manual_attempt_crash_does_not_restore_consumed_budget(store, env):
    clock, upstream, _ = env
    runner, key, _ = failed(env)
    upstream.add(key)
    apply_review(runner, request(tasks(runner)[0]))

    def crash(phase, attempt):
        if phase == "after_publish":
            raise KeyboardInterrupt()

    runner.fault = crash
    with pytest.raises(KeyboardInterrupt):
        runner.run()
    assert tasks(runner)[0]["manual_retry"]["remaining"] == 0
    count = len(upstream.calls)
    runner.fault = lambda *a: None
    runner.run()
    assert tasks(runner)[0]["state"] == "complete"
    assert tasks(runner)[0]["manual_retry"]["remaining"] == 0
    assert len(upstream.calls) == count


def test_clock_hold_preserves_manual_budget_and_missing_evidence_does_not_complete(store, env):
    runner, _, _ = failed(env)
    before = tasks(runner)[0]
    with pytest.raises(DataError, match="No complete"):
        apply_review(runner, request(before, "reconcile"))
    assert tasks(runner)[0] == before
    apply_review(runner, request(before))
    runner.readiness = lambda: {"ready": False, "reason": "strict gate"}
    runner.run()
    assert tasks(runner)[0]["state"] == "deferred_clock"
    assert tasks(runner)[0]["manual_retry"]["remaining"] == 1


def test_preview_missing_source_lock_never_creates_it(store, env, tmp_path):
    runner, key, _ = failed(env)
    assert not (store.root / "state/file-layout.lock").exists()
    with pytest.raises(FileNotFoundError):
        preview(store.root, tmp_path / "view", [key], runner.policy, runner.schedule)
    assert not (store.root / "state/file-layout.lock").exists()
    assert not (tmp_path / "view").exists()


def test_preview_reads_packed_data_and_detects_corruption(store, env, tmp_path):
    from marketdata import packing

    clock, upstream, build = env
    key = Key("trade_bars", interval="1h")
    upstream.add(key)
    runner = build([key], refresh=0)
    fill_old(store, runner, key)
    logical = list(packing.logical_files(store.db).values())
    # Test the existing packed/loose read contract with the same pack writer.
    for item in logical:
        item["bytes"] = (store.root / item["path"]).stat().st_size
    pack = packing.build_pack(store, logical, 0)
    with layout_lock(store.root, exclusive=True), store.writer(), store.db:
        store.db.execute(
            "INSERT INTO file_packs VALUES(?,?,?,?,?)",
            (pack["id"], pack["path"], pack["bytes"], pack["members"], clock().isoformat()),
        )
        for item in logical:
            store.db.execute(
                "INSERT INTO packed_files VALUES(?,?,?,?,?)",
                (item["path"], pack["id"], item["sha256"], item["bytes"], item["kind"]),
            )
    packing.prune_loose(store, apply=True)
    result = preview(
        store.root, tmp_path / "view", [key], runner.policy, runner.schedule, current=clock()
    )
    assert result["state_counts"] == {"verified_complete_no_acquire": 1}
    (store.root / pack["path"]).write_bytes(b"damaged")
    with pytest.raises(DataError):
        preview(
            store.root,
            tmp_path / "damaged-view",
            [key],
            runner.policy,
            runner.schedule,
            current=clock(),
        )


def test_claim_transaction_failure_does_not_consume_review_budget(store, env):
    runner, _, _ = failed(env)
    apply_review(runner, request(tasks(runner)[0]))
    with store.db:
        store.db.execute(
            "CREATE TRIGGER claim_fail BEFORE INSERT ON daily_attempts BEGIN SELECT RAISE(ABORT,'claim interrupted'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        runner.run()
    assert tasks(runner)[0]["manual_retry"]["remaining"] == 1
    assert len(tasks(runner)[0]["attempts"]) == 1


def test_manual_download_only_crash_exhausts_one_authorized_attempt(store, env, monkeypatch):
    runner, key, _ = failed(env)
    env[1].add(key)
    apply_review(runner, request(tasks(runner)[0]))
    with monkeypatch.context() as patch:
        patch.setattr(
            Collector, "_publish", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt())
        )
        with pytest.raises(KeyboardInterrupt):
            runner.run()
    calls = len(env[1].calls)
    runner.run()
    task = tasks(runner)[0]
    assert task["state"] == "needs_review" and task["manual_retry"]["remaining"] == 0
    assert len(env[1].calls) == calls


def test_actual_review_cli_concurrency_returns_one_receipt(store, env, tmp_path):
    runner, _, _ = failed(env)
    task = tasks(runner)[0]
    cfg = tmp_path / "scheduler.toml"
    cfg.write_text(
        '[operations]\nstart="2024-01-14"\nrefresh_days=0\n[[datasets]]\ndataset="trade_bars"\ninterval="1h"\n'
    )
    reqfile = tmp_path / "request.json"
    reqfile.write_bytes(json_bytes(request(task, extra=2)))
    cmd = [
        sys.executable,
        "-B",
        "-I",
        "-m",
        "marketdata.daily_review",
        "apply",
        "--root",
        str(store.root),
        "--config",
        str(cfg),
        "--request",
        str(reqfile),
    ]
    procs = [
        subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)
    ]
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=15)
        assert proc.returncode in {0, 75}, (stdout, stderr)
    replay = subprocess.run(cmd, capture_output=True, check=True, timeout=15)
    receipt = json.loads(replay.stdout)
    assert receipt["after"]["manual_retry"]["remaining"] == 2
    assert len(runner.ledger.rows("daily_reviews")) == 1
    inspected = subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-m",
            "marketdata.daily_review",
            "inspect",
            "--root",
            str(store.root),
            "--task-id",
            task["id"],
        ],
        capture_output=True,
        check=True,
        timeout=15,
    )
    assert json.loads(inspected.stdout)["reviews"] == [receipt]
