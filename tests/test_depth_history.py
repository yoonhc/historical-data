import runpy
from datetime import timedelta
from pathlib import Path

import pytest
from marketdata import depth
from marketdata.domain import DataError, json_bytes
from test_depth import capture, row, snapshot, update


def session():
    return depth.Session(top=2, refresh_from_checks=True, history=depth.History())


def expanded_snapshot(identity=100, *, bid_quantity="2"):
    result = snapshot(identity, bid_quantity=bid_quantity)
    result["asks"].extend([["104", "3"], ["105", "4"]])
    return result


def test_delayed_check_aligns_past_events_then_refreshes_without_losing_duplicate_detection():
    active = session()
    active.take(row(snapshot(), "anchor"))
    active.take(row(update(100), sequence=1))
    active.take(row(update(101), sequence=2))
    active.take(row(expanded_snapshot(), "check", 3))
    check = active.checks[-1]
    assert check["equal"] and check["refreshed"]
    assert check["history_alignment"]["applied"] == 2
    assert active.book.ask_ceiling == 105 and active.book.last == 101
    active.take(row(update(101), sequence=4))
    assert active.counts["duplicate"] == 1
    active.take(row(update(102, asks=[[str(p), "0"] for p in (101, 102, 103)]), sequence=5))
    assert min(active.book.asks) == 104 and active.book.last == 102


def test_delayed_check_mismatch_is_not_adopted_or_silently_ignored():
    active = session()
    active.take(row(snapshot(), "anchor"))
    active.take(row(update(100), sequence=1))
    active.take(row(update(101), sequence=2))
    before = json_bytes(active.book.view())
    with pytest.raises(depth.SyncLost, match="disagrees"):
        active.take(row(expanded_snapshot(bid_quantity="9"), "check", 3))
    assert active.checks[-1]["comparable"] and not active.checks[-1]["equal"]
    assert active.checks[-1]["history_alignment"]["aligned"]
    assert json_bytes(active.book.view()) == before and active.refreshes == 0


def test_capture_and_replay_adopt_a_delayed_equal_check(store, monkeypatch):
    report = capture(
        store,
        monkeypatch,
        [[update(i) for i in range(100, 105)]],
        [snapshot(), expanded_snapshot(101)],
        seconds=21,
        verify_seconds=15,
    )
    final = report["epochs"][0]["final"]
    assert report["complete"] and final["checks"][0]["equal"]
    assert final["checks"][0]["history_alignment"]["applied"] == 2
    assert final["refreshes"] == 1 and final["book"]["ask_ceiling"] == 105
    replayed = depth.replay(store)
    assert replayed["valid"] and replayed["replay_matches_live"]
    verify = runpy.run_path(str(Path(__file__).parents[1] / "scripts/verify_depth_capture.py"))[
        "verify"
    ]
    assert verify(store, report["id"], replayed=replayed)["all_snapshot_checks_verified"]


def test_capture_and_replay_preserve_delayed_mismatch_then_reconnect(store, monkeypatch):
    report = capture(
        store,
        monkeypatch,
        [[update(i) for i in range(100, 103)], [update(200), update(201)]],
        [snapshot(), snapshot(101, bid_quantity="9"), snapshot(200)],
        seconds=26,
        verify_seconds=15,
    )
    assert report["sync_losses"] == 1 and report["connections"] == 2
    assert "disagrees" in report["epochs"][0]["final"]["error"]
    assert report["epochs"][1]["final"]["anchor_history"]["events"] == 0
    replayed = depth.replay(store)
    assert replayed["replay_matches_live"] and not replayed["valid"]


def test_delayed_check_cannot_refresh_when_existing_top_is_incomplete():
    active = session()
    active.take(row(snapshot(), "anchor"))
    active.take(row(update(100), sequence=1))
    active.take(row(update(101, asks=[["102", "0"], ["103", "0"]]), sequence=2))
    active.take(row(expanded_snapshot(), "check", 3))
    assert not active.checks[-1]["comparable"]
    assert not active.checks[-1]["refreshed"]
    assert len(active.book.asks) == 1 and active.book.ask_ceiling == 103


@pytest.mark.parametrize("budget", ["events", "levels", "serialized_bytes"])
def test_history_eviction_refuses_an_old_bridge_but_can_use_a_retained_one(monkeypatch, budget):
    probe = depth.History()
    probe.append(row(update(100), sequence=1))
    limit = probe.serialized_bytes + 10 if budget == "serialized_bytes" else 2
    monkeypatch.setitem(depth.HISTORY_LIMITS, budget, limit)
    active = session()
    active.take(row(snapshot(), "anchor"))
    for index, identity in enumerate(range(100, 103), 1):
        active.take(row(update(identity), sequence=index))
    before = json_bytes(active.book.view())
    active.take(row(expanded_snapshot(100), "check", 4))
    assert not active.checks[-1]["comparable"] and active.refreshes == 0
    assert not active.checks[-1]["history_alignment"]["aligned"]
    assert "bridge" in active.checks[-1]["history_alignment"]["reason"]
    assert json_bytes(active.book.view()) == before
    candidate, proof = active.history.align(row(snapshot(102), "anchor", 5))
    assert candidate.last == 102 and proof["aligned"]


def test_oversized_event_evicts_old_history_instead_of_leaving_a_false_tail(monkeypatch):
    monkeypatch.setitem(depth.HISTORY_LIMITS, "levels", 2)
    history = depth.History()
    history.append(row(update(100), sequence=1))
    history.append(row(update(101, bids=[[str(p), "1"] for p in (100, 99, 98)]), sequence=2))
    assert not history.events and history.levels == history.serialized_bytes == 0
    candidate, proof = history.align(row(snapshot(100), "anchor", 3))
    assert candidate is None and not proof["aligned"]


@pytest.mark.parametrize("bad", ["gap", "conflict", "reversed_time", "reversed_id"])
def test_alignment_rejects_invalid_history_without_publishing_a_partial_book(bad):
    history = depth.History()
    history.append(row(update(100), sequence=1))
    if bad == "gap":
        event = row(update(102), sequence=2)
    elif bad == "conflict":
        event = row(update(100, bids=[["100", "9"]]), sequence=2)
    elif bad == "reversed_id":
        event = row(update(99), sequence=2)
    else:
        event = row(update(101), sequence=2)
        event["timestamp"] -= timedelta(seconds=1)
    history.append(event)
    candidate, proof = history.align(row(snapshot(), "anchor", 3))
    assert candidate is None and not proof["aligned"] and proof["reason"]


def test_history_copies_values_and_ignores_live_vs_persisted_source_provenance():
    event = row(update(100), sequence=1)
    first, second = depth.History(), depth.History()
    first.append(event)
    event["source_id"], event["source_row"] = "persisted" * 10, 987
    second.append(event)
    assert first.serialized_bytes == second.serialized_bytes
    a, pa = first.align(row(snapshot(), "anchor", 2))
    b, pb = second.align(row(snapshot(), "anchor", 2))
    assert json_bytes(a.view()) == json_bytes(b.view()) and pa == pb
    event["bids"][0]["quantity"] = 999
    candidate, _ = first.align(row(snapshot(), "anchor", 2))
    assert candidate.bids[100] == 1


def test_same_socket_coverage_reset_uses_failed_event_but_keeps_failure_and_counts(
    store, monkeypatch
):
    removal = update(
        101, asks=[[str(p), "0"] for p in (101, 102, 103)] + [["104", "3"], ["105", "4"]]
    )
    report = capture(
        store,
        monkeypatch,
        [[update(100), removal, removal, update(102)]],
        [snapshot(), expanded_snapshot()],
        seconds=19,
    )
    assert report["connections"] == 1 and report["sync_losses"] == report["coverage_resets"] == 1
    assert report["lagged_snapshots"] == 0 and not report["complete"]
    first, second = report["epochs"]
    assert first["final"]["error"] == "Known snapshot price range exhausted"
    assert second["final"]["anchor_history"]["applied"] == 2
    assert second["final"]["counts"] == {"applied": 1, "stale": 0, "duplicate": 1}
    replayed = depth.replay(store)
    assert replayed["replay_matches_live"] and not replayed["valid"]
    verify = runpy.run_path(str(Path(__file__).parents[1] / "scripts/verify_depth_capture.py"))[
        "verify"
    ]
    assert verify(store, report["id"], replayed=replayed)["raw_to_canonical_equal"]


def test_lagged_epoch_retains_skipped_updates_for_same_socket_retry(store, monkeypatch):
    report = capture(
        store,
        monkeypatch,
        [[update(i) for i in range(100, 104)]],
        [snapshot(90), snapshot(101)],
        seconds=19,
    )
    assert report["lagged_snapshots"] == 1 and report["unreconstructed_messages"] == 3
    assert report["epochs"][0]["final"]["counts"]["applied"] == 0
    final = report["epochs"][1]["final"]
    assert final["anchor_history"]["applied"] == 2 and final["counts"]["applied"] == 1
    assert final["book"]["last_update_id"] == 103
    replayed = depth.replay(store)
    assert replayed["replay_matches_live"] and not replayed["valid"]


@pytest.mark.parametrize(
    "reason", ["planned_rotation", "reconnect_after_disconnect", "resync_after_loss"]
)
def test_a_new_connection_cannot_borrow_the_previous_connection_bridge(reason):
    report = {
        "top": 2,
        "refresh_from_checks": True,
        "history_policy": depth.HISTORY_POLICY,
        "history_limits": dict(depth.HISTORY_LIMITS),
    }
    previous = depth.new_session(report, "initial")
    previous.take(row(snapshot(), "anchor"))
    previous.take(row(update(100), sequence=1))
    previous.take(row(update(101), sequence=2))
    current = depth.new_session(report, reason, previous)
    current.take(row(snapshot(), "anchor", 3))
    with pytest.raises(depth.SnapshotBehind):
        current.take(row(update(102), sequence=4))
    assert not current.anchor_history["aligned"] and current.anchor_history["events"] == 0


def test_live_rotation_and_replay_both_clear_history(store, monkeypatch):
    report = capture(
        store,
        monkeypatch,
        [[update(100), update(101)], [update(102), update(103)]],
        [snapshot(), snapshot()],
        seconds=19,
        rotate_seconds=10,
    )
    assert report["connections"] == 2 and report["lagged_snapshots"] == 1
    assert report["epochs"][1]["final"]["anchor_history"]["events"] == 0
    assert depth.replay(store)["replay_matches_live"]


@pytest.mark.parametrize("tamper", ["policy", "limits"])
def test_replay_rejects_unknown_policy_or_changed_limits(store, monkeypatch, tamper):
    report = capture(store, monkeypatch, [[update(100), update(101)]], [snapshot()])
    if tamper == "policy":
        report["history_policy"] = "unsupported"
    else:
        report["history_limits"] = {**report["history_limits"], "events": 1}
    depth.save_collection(store, report, "complete")
    with pytest.raises(DataError, match="history"):
        depth.replay(store)


def test_legacy_replay_does_not_require_new_policy_or_epoch_reason(store, monkeypatch):
    report = capture(
        store, monkeypatch, [[update(100), update(101)]], [snapshot()], history_policy=None
    )
    report["epochs"][0].pop("reason")
    assert "history_policy" not in report and "anchor_history" not in report["epochs"][0]["final"]
    depth.save_collection(store, report, "complete")
    assert depth.replay(store)["replay_matches_live"]


def test_a_same_socket_epoch_without_preceding_history_is_rejected():
    report = {
        "top": 2,
        "history_policy": depth.HISTORY_POLICY,
        "history_limits": dict(depth.HISTORY_LIMITS),
    }
    with pytest.raises(DataError, match="preceding history"):
        depth.new_session(report, "same_socket_after_coverage_exhausted")
