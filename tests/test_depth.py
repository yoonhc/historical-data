import gzip
import json
import runpy
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from marketdata import depth
from marketdata import depth_stream as live
from marketdata.backup import create_backup, restore_backup, verify_backup
from marketdata.binance import Fetcher, SourceError
from marketdata.cli import main
from marketdata.domain import DataError, micros, utc
from marketdata.store import Store

BASE = utc("2024-01-01")


def snapshot(identity=100, *, bid_quantity="2"):
    return {
        "lastUpdateId": identity,
        "T": micros(BASE) // 1000 + identity,
        "E": micros(BASE) // 1000 + identity + 1,
        "bids": [["100", "1"], ["99", bid_quantity], ["98", "3"]],
        "asks": [["101", "1"], ["102", "2"], ["103", "3"]],
    }


def update(identity=100, *, first=None, previous=None, bids=None, asks=None):
    return {
        "e": "depthUpdate",
        "s": "BTCUSDT",
        "st": 1,
        "T": micros(BASE) // 1000 + identity,
        "E": micros(BASE) // 1000 + identity + 1,
        "U": identity if first is None else first,
        "u": identity,
        "pu": identity - 1 if previous is None else previous,
        "b": [["100", "1"]] if bids is None else bids,
        "a": [] if asks is None else asks,
    }


def row(raw, kind="update", sequence=0):
    return depth.parse(raw, kind, 0, sequence, "source", 0, BASE + timedelta(seconds=1))


def fetcher_for(snapshots):
    payloads, calls = iter(snapshots), []

    def handler(request):
        calls.append(str(request.url))
        assert request.url.params["limit"] == "1000"
        payload = next(payloads)
        return payload if isinstance(payload, httpx.Response) else httpx.Response(200, json=payload)

    return Fetcher(
        httpx.Client(transport=httpx.MockTransport(handler)), retries=0, sleep=lambda _: None
    ), calls


def connector_factory(monkeypatch, sessions, *, step=5):
    clock = SimpleNamespace(value=0)
    monkeypatch.setattr(live.time, "monotonic", lambda: clock.value)

    def sleep(seconds):
        clock.value += seconds

    monkeypatch.setattr(live.time, "sleep", sleep)
    monkeypatch.setattr(live, "now", lambda: BASE + timedelta(seconds=clock.value))
    sessions = iter(sessions)

    @contextmanager
    def connector(url, **kwargs):
        assert url == depth.WS and "/public/ws/" in url
        payloads = iter(next(sessions))

        def recv(timeout):
            clock.value += step
            payload = next(payloads)
            if isinstance(payload, BaseException):
                raise payload
            return payload if isinstance(payload, str) else json.dumps(payload)

        yield SimpleNamespace(recv=recv)

    return connector


def capture(store, monkeypatch, sessions, snapshots, *, seconds=9, **kwargs):
    connector = connector_factory(monkeypatch, sessions)
    fetcher, _ = fetcher_for(snapshots)
    return live.capture(
        store,
        connector=connector,
        fetcher=fetcher,
        seconds=seconds,
        verify_seconds=kwargs.pop("verify_seconds", 3600),
        top=2,
        min_free_gib=0,
        **kwargs,
    )


def test_snapshot_bridge_uses_futures_inclusive_rule_and_pu_chain():
    book = depth.Book(row(snapshot(), "anchor"))
    assert book.apply(row(update(99))) == "stale"
    assert book.apply(row(update(100, first=90, previous=89))) == "applied"
    # U may jump: futures continuity is pu == previous u, not adjacent integers.
    assert book.apply(row(update(130, first=120, previous=100))) == "applied"
    assert book.last == 130 and book.synced


def test_absolute_quantities_zero_deletion_and_unknown_removal():
    book = depth.Book(row(snapshot(), "anchor"))
    book.apply(row(update(100, bids=[["100", "7"], ["99", "0"], ["99.5", "0"]])))
    assert book.bids[Decimal(100)] == 7 and Decimal(99) not in book.bids
    book.apply(row(update(101, bids=[["100", "2"]])))
    assert book.bids[Decimal(100)] == 2


def test_duplicate_conflict_and_gap_do_not_silently_change_book():
    book = depth.Book(row(snapshot(), "anchor"))
    event = row(update())
    book.apply(event)
    before = book.view()
    assert book.apply(event) == "duplicate"
    with pytest.raises(depth.SyncLost, match="Conflicting"):
        book.apply(row(update(bids=[["100", "2"]])))
    with pytest.raises(depth.SyncLost, match="pu"):
        book.apply(row(update(102)))
    assert book.view() == before


def test_missing_initial_bridge_and_crossed_update_require_new_snapshot():
    book = depth.Book(row(snapshot(), "anchor"))
    with pytest.raises(depth.SyncLost, match="bridge"):
        book.apply(row(update(101)))
    with pytest.raises(depth.SyncLost, match="Crossed"):
        book.apply(row(update(100, bids=[["102", "1"]])))
    assert not book.synced and max(book.bids) == 100


def test_known_price_frontier_does_not_claim_unobserved_outer_depth():
    book = depth.Book(row(snapshot(), "anchor"))
    book.apply(row(update(100, bids=[["90", "200"], ["98", "0"]], asks=[["110", "300"]])))
    assert Decimal(90) not in book.bids and Decimal(110) not in book.asks
    assert not book.view(top=3)["top_complete"]
    assert book.view(top=2)["top_complete"]
    with pytest.raises(depth.SyncLost, match="exhausted"):
        book.apply(row(update(101, bids=[["100", "0"], ["99", "0"]])))


@pytest.mark.parametrize(
    "field,value",
    [
        ("u", True),
        ("s", "ETHUSDT"),
        ("st", 2),
        ("b", [["100", "-1"]]),
        ("b", [["NaN", "1"]]),
        ("b", [["100", "1"], ["100", "2"]]),
    ],
)
def test_invalid_source_fields_are_rejected(field, value):
    with pytest.raises(DataError):
        row({**update(), field: value})


def test_independent_snapshot_is_aligned_to_same_update_before_comparison():
    session = depth.Session(top=2)
    session.take(row(snapshot(), "anchor"))
    session.take(row(update()))
    session.take(row(snapshot(102), "check", 2))
    assert session.pending and not session.checks
    session.take(row(update(101)))
    assert not session.checks
    session.take(row(update(102)))
    assert session.checks[0]["equal"] and session.checks[0]["last_update_id"] == 102


def test_independent_snapshot_difference_and_unalignable_check_are_explicit():
    session = depth.Session(top=2)
    session.take(row(snapshot(), "anchor"))
    session.take(row(update()))
    with pytest.raises(depth.SyncLost, match="disagrees"):
        session.take(row(snapshot(100, bid_quantity="9"), "check"))
    assert not session.checks[-1]["equal"]
    session = depth.Session(top=2)
    session.take(row(snapshot(), "anchor"))
    session.take(row(update()))
    session.take(row(snapshot(101), "check"))
    session.take(row(update(103, first=102, previous=100)))
    assert session.checks[-1]["comparable"] is False


def test_verified_snapshot_refresh_extends_coverage_without_breaking_update_chain():
    session = depth.Session(top=2, refresh_from_checks=True)
    session.take(row(snapshot(), "anchor"))
    session.take(row(update()))
    fresh = snapshot(102)
    fresh["asks"].append(["104", "9"])
    session.take(row(fresh, "check", 2))
    session.take(row(update(101)))
    assert session.refreshes == 0
    session.take(row(update(102)))
    assert session.refreshes == 1 and session.book.ask_ceiling == 104
    assert session.checks[-1]["refreshed"] and session.checks[-1]["equal"]
    session.take(row(update(102)))
    assert session.counts["duplicate"] == 1
    session.take(row(update(103, asks=[["101", "0"], ["102", "0"], ["103", "0"]])))
    assert min(session.book.asks) == 104 and session.book.last == 103


def test_equal_id_refresh_preserves_duplicate_detection_and_legacy_policy():
    fresh = snapshot()
    fresh["asks"].append(["104", "9"])
    for refresh in (False, True):
        session = depth.Session(top=2, refresh_from_checks=refresh)
        session.take(row(snapshot(), "anchor"))
        session.take(row(update()))
        session.take(row(fresh, "check"))
        session.take(row(update()))
        assert session.counts["duplicate"] == 1
        assert session.book.ask_ceiling == (104 if refresh else 103)


def test_capture_refreshes_thinning_coverage_before_periodic_check(store, monkeypatch):
    anchor = snapshot()
    anchor["asks"] = [[str(p), "1"] for p in range(101, 301)]
    fresh = snapshot()
    fresh["asks"] = [[str(p), "1"] for p in [*range(101, 141), *range(301, 461)]]
    deletes = [[str(p), "0"] for p in range(141, 301)]
    report = capture(
        store, monkeypatch, [[update(100, asks=deletes), update(101)]], [anchor, fresh]
    )
    assert report["snapshots"] == 2 and report["sync_losses"] == 0
    assert report["epochs"][0]["final"]["refreshes"] == 1
    result = depth.replay(store, report["id"])
    assert result["valid"] and result["verification_equal"] == 1


@pytest.mark.parametrize("quantity,equal", [("2", True), ("9", False)])
def test_offline_delayed_snapshot_check_uses_prior_deltas_and_detects_mismatch(
    store, monkeypatch, quantity, equal
):
    report = capture(
        store,
        monkeypatch,
        [[update(i) for i in range(100, 105)]],
        [snapshot(), snapshot(101, bid_quantity=quantity)],
        seconds=21,
        verify_seconds=15,
    )
    original = depth.replay(store, report["id"])
    assert original["valid"] and not original["verification_complete"]
    verify = runpy.run_path(str(Path(__file__).parents[1] / "scripts/verify_depth_capture.py"))[
        "verify"
    ]
    result = verify(store, report["id"])
    assert result["delayed_snapshot_checks"][0]["equal"] is equal
    assert result["all_snapshot_checks_verified"] is equal
    assert not result["verification_complete"]  # Original live evidence is unchanged.
    assert depth.replay(store, report["id"]) == original


def test_planned_rotation_starts_new_epoch_and_offline_replay_matches(store, monkeypatch):
    report = capture(
        store,
        monkeypatch,
        [[update(100), update(101)], [update(200), update(201)]],
        [snapshot(100), snapshot(200)],
        seconds=19,
        rotate_seconds=10,
    )
    assert report["complete"] and report["connections"] == 2 and report["snapshots"] == 2
    assert report["epochs"][1]["reason"] == "planned_rotation"
    replay = depth.replay(store, report["id"])
    assert replay["valid"] and replay["replay_matches_live"] and not replay["continuous"]
    assert replay["rows"] == 6
    assert not depth.status(store)["healthy"]


def test_live_snapshot_verification_and_replay(store, monkeypatch):
    report = capture(
        store,
        monkeypatch,
        [[update(i) for i in range(100, 105)]],
        [snapshot(), snapshot(103)],
        seconds=21,
        verify_seconds=15,
    )
    replay = depth.replay(store, report["id"])
    assert replay["valid"] and replay["verification_equal"] == 1
    assert replay["continuous"]


def test_missing_update_resynchronizes_but_missing_history_remains_invalid(store, monkeypatch):
    report = capture(
        store,
        monkeypatch,
        [[update(100), update(102)], [update(200), update(201)]],
        [snapshot(), snapshot(200)],
        seconds=20,
    )
    assert report["sync_losses"] == 1 and not report["complete"]
    assert report["epochs"][1]["reason"] == "resync_after_loss"
    result = depth.replay(store)
    assert result["replay_matches_live"] and not result["valid"]
    assert result["epochs"][1]["book"]["synced"]
    assert "pu" in result["epochs"][0]["error"]


def test_disconnect_starts_fresh_book_and_records_unrecoverable_boundary(store, monkeypatch):
    report = capture(
        store,
        monkeypatch,
        [[update(100), OSError("dropped")], [update(200), update(201)]],
        [snapshot(), snapshot(200)],
        seconds=21,
    )
    assert report["connection_errors"] == 1 and report["snapshots"] == 2
    assert report["epochs"][1]["reason"] == "reconnect_after_disconnect"
    assert not report["complete"] and depth.replay(store)["replay_matches_live"]


def test_malformed_websocket_keeps_raw_and_marks_failure(store, monkeypatch):
    with pytest.raises(json.JSONDecodeError):
        capture(store, monkeypatch, [["not json"]], [snapshot()])
    record = store.db.execute("SELECT id FROM artifacts WHERE kind='depth_websocket'").fetchone()
    assert b"not json" in gzip.decompress(store.raw(record["id"])[0])
    assert depth.status(store)["state"] == "failed"


def test_rest_rate_limit_preserves_body_and_persists_cooldown(store):
    fetcher, calls = fetcher_for(
        [httpx.Response(429, content=b"limited", headers={"Retry-After": "900"})]
    )
    report = depth.start_collection(store, {})
    with pytest.raises(SourceError):
        live.snapshot(store, fetcher, report["id"], 0, 0, "anchor")
    with pytest.raises(DataError, match="cooldown"):
        live.snapshot(store, fetcher, report["id"], 0, 0, "anchor")
    assert len(calls) == 1
    artifact = store.db.execute("SELECT id FROM artifacts").fetchone()[0]
    assert store.raw(artifact)[0] == b"limited"


def test_ctrl_c_flushes_and_next_run_uses_new_snapshot(store, monkeypatch):
    with pytest.raises(KeyboardInterrupt):
        capture(store, monkeypatch, [[update(), KeyboardInterrupt()]], [snapshot()])
    previous = depth.status(store)["report"]
    assert previous["state"] == "interrupted" and previous["persisted_messages"] == 1
    report = capture(store, monkeypatch, [[update(200), update(201)]], [snapshot(200)])
    assert report["id"] != previous["id"] and report["snapshots"] == 1
    assert depth.replay(store, report["id"])["valid"]


def test_orphan_running_record_is_interrupted_on_restart(store, monkeypatch):
    previous = depth.start_collection(store, {})
    capture(store, monkeypatch, [[update(), update(101)]], [snapshot()])
    record = depth.collection_record(store, previous["id"])
    assert record["state"] == json.loads(record["report"])["state"] == "interrupted"


def test_low_disk_stops_before_network_snapshot(store, monkeypatch):
    connector = connector_factory(monkeypatch, [[]])
    fetcher, calls = fetcher_for([])
    monkeypatch.setattr(live.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(DataError, match="free space"):
        live.capture(store, seconds=5, connector=connector, fetcher=fetcher)
    assert not calls and depth.status(store)["state"] == "failed"


def test_depth_backup_restore_and_corruption_detection(store, monkeypatch, tmp_path):
    report = capture(store, monkeypatch, [[update(), update(101)]], [snapshot()])
    expected = depth.replay(store, report["id"])
    backup = tmp_path / "backup"
    create_backup(store, backup, min_free_gib=0)
    assert verify_backup(backup)["ok"]
    target = tmp_path / "restored"
    restore_backup(backup, target, min_free_gib=0)
    restored = Store(target)
    try:
        assert depth.replay(restored, report["id"]) == expected
        path = restored.db.execute(
            "SELECT path FROM event_segments WHERE dataset=?", (depth.DATASET,)
        ).fetchone()[0]
        (target / path).write_bytes(b"corrupted")
        assert not restored.validate()["ok"]
        with pytest.raises(DataError, match="hash"):
            depth.replay(restored, report["id"])
    finally:
        restored.close()


def test_journal_sequence_holes_and_replay_budget_are_detected(store, monkeypatch):
    capture(store, monkeypatch, [[update(), update(101)]], [snapshot()])
    with pytest.raises(DataError, match="budget"):
        depth.replay(store, max_rows=1)
    with store.db:
        store.db.execute("DELETE FROM event_segments WHERE first_id=0")
    with pytest.raises(DataError, match="sequence"):
        depth.replay(store)


def test_cli_empty_store_and_invalid_duration(store, capsys):
    assert main(["--root", str(store.root), "depth-status"]) == 2
    assert main(["--root", str(store.root), "depth-replay"]) == 1
    assert main(["--root", str(store.root), "depth-stream", "--seconds", "0"]) == 1
    assert "Depth pilot" in capsys.readouterr().err
