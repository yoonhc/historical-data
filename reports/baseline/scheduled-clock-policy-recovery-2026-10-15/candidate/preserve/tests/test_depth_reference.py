"""Compare recovery with exchange state generated without the production book engine.

An exchange mutates its full book first, then publishes aggregated absolute updates.
REST snapshots can fall inside an aggregate, behind it, or ahead of the receiver.
Expected books come from the exchange's full state, never from Book/Session replay.
"""

from decimal import Decimal
from random import Random

import pytest
from marketdata import depth
from marketdata.domain import micros, utc

BASE = utc("2024-01-01")
START_MS = micros(BASE) // 1000


def exchange_batches(seed, count=160):
    rng = Random(seed)
    prices = {
        "bids": [Decimal(p) / 10 for p in range(800, 1001)],
        "asks": [Decimal(p) / 10 for p in range(1001, 1202)],
    }
    full = {side: {p: Decimal("1.125") for p in values} for side, values in prices.items()}
    identity = 10000
    batches = []
    for _ in range(count):
        previous = identity
        changed = {"bids": {}, "asks": {}}
        states = []
        for _ in range(rng.randint(2, 6)):
            # USD-M IDs need not be consecutive integers.
            identity += rng.randint(1, 9)
            side = rng.choice(("bids", "asks"))
            price = rng.choice(prices[side])
            quantity = Decimal(rng.randrange(0, 9)) / 1000
            if quantity:
                full[side][price] = quantity
            else:
                full[side].pop(price, None)
            changed[side][price] = quantity
            states.append((identity, {s: dict(book) for s, book in full.items()}))
        batches.append(
            {
                "raw": {
                    "e": "depthUpdate",
                    "s": "BTCUSDT",
                    "T": START_MS + identity,
                    "E": START_MS + identity + 1,
                    "U": states[0][0],
                    "u": identity,
                    "pu": previous,
                    "b": [[str(p), str(q)] for p, q in changed["bids"].items()],
                    "a": [[str(p), str(q)] for p, q in changed["asks"].items()],
                },
                "middle": states[len(states) // 2 - 1],
                "end": states[-1],
            }
        )
    return batches


def rest_snapshot(state, limit):
    identity, full = state
    return {
        "lastUpdateId": identity,
        "T": START_MS + identity,
        "E": START_MS + identity + 1,
        **{
            side: [[str(p), str(full[side][p])] for p in sorted(full[side], reverse=bid)[:limit]]
            for side, bid in (("bids", True), ("asks", False))
        },
    }


def assert_exchange_state(book, state):
    identity, full = state
    assert book.synced and book.last == identity
    assert book.bids == {p: q for p, q in full["bids"].items() if p >= book.bid_floor}
    assert book.asks == {p: q for p, q in full["asks"].items() if p <= book.ask_ceiling}
    view = book.view(5)
    assert view["top_complete"]
    assert view["bids"] == sorted(full["bids"].items(), reverse=True)[:5]
    assert view["asks"] == sorted(full["asks"].items())[:5]


@pytest.mark.parametrize("seed", range(32))
def test_recovery_matches_independent_exchange_state(seed):
    batches = exchange_batches(seed)
    policy = {
        "top": 5,
        "refresh_from_checks": True,
        "history_policy": depth.HISTORY_POLICY,
        "history_limits": dict(depth.HISTORY_LIMITS),
    }
    active = depth.new_session(policy, "initial")
    sequence, epoch = 0, 0

    def take(raw, kind="update"):
        nonlocal sequence
        row = depth.parse(raw, kind, epoch, sequence, "test", sequence, BASE)
        sequence += 1
        active.take(row)

    # The first two buffered events predate the REST anchor. The third event
    # overlaps the snapshot and contains mutations on both sides of its ID.
    take(rest_snapshot(batches[2]["middle"], 32), "anchor")
    delayed, pending, reanchored, duplicated = 0, 0, 0, 0
    prior_epochs = []
    for index, batch in enumerate(batches):
        take(batch["raw"])
        if index < 2:
            assert not active.book.synced
            continue
        assert_exchange_state(active.book, batch["end"])

        if index % 13 == 0:
            take(batch["raw"])
            duplicated += 1
            assert_exchange_state(active.book, batch["end"])

        if active.pending is None and index % 17 == 7:
            take(rest_snapshot(batches[max(2, index - 4)]["middle"], 48), "check")
            assert active.checks[-1]["equal"] and active.checks[-1]["refreshed"]
            assert active.checks[-1]["history_alignment"]["aligned"]
            delayed += 1
            assert_exchange_state(active.book, batch["end"])

        if active.pending is None and index % 19 == 11 and index + 2 < len(batches):
            take(rest_snapshot(batches[index + 2]["middle"], 64), "check")
            assert active.pending is not None
            pending += 1
            # A future REST response must not advance the live book ahead of
            # events already processed by this receiver.
            assert_exchange_state(active.book, batch["end"])

        if active.pending is None and index in (55, 112):
            previous = active
            prior_epochs.append(previous)
            active = depth.new_session(policy, "same_socket_after_coverage_exhausted", previous)
            epoch += 1
            take(rest_snapshot(batches[index - 3]["middle"], 40), "anchor")
            assert active.anchor_history["aligned"]
            assert active.counts == {"applied": 0, "stale": 0, "duplicate": 0}
            reanchored += 1
            assert_exchange_state(active.book, batch["end"])

    assert delayed >= 8 and pending >= 7 and reanchored == 2 and duplicated >= 10
    assert len(active.history.events) == depth.HISTORY_LIMITS["events"]
    assert active.pending is None
    assert all(check["equal"] for session in [*prior_epochs, active] for check in session.checks)


def test_failed_partial_history_candidate_does_not_change_the_active_book():
    # A chain failure after the candidate applied its bridge must leave the
    # current book untouched, not publish the successfully applied prefix.
    batches = exchange_batches(171, count=8)
    history = depth.History()
    active = depth.Session(top=5, refresh_from_checks=True, history=history)
    active.take(depth.parse(rest_snapshot(batches[2]["middle"], 32), "anchor", 0, 0, "x", 0, BASE))
    for index, batch in enumerate(batches[2:], 1):
        active.take(depth.parse(batch["raw"], "update", 0, index, "x", 0, BASE))
    original = active.book
    assert_exchange_state(original, batches[-1]["end"])
    # An intentionally corrupted retained tail simulates invalid replay input.
    history.events[-1][0]["previous_update_id"] += 1
    check = depth.parse(rest_snapshot(batches[3]["middle"], 64), "check", 0, 10, "x", 0, BASE)
    active.take(check)
    assert active.book is original and active.refreshes == 0
    assert not active.checks[-1]["comparable"]
    proof = active.checks[-1]["history_alignment"]
    assert proof["applied"] > 0 and not proof["aligned"]
    assert "pu differs" in proof["reason"]
    assert_exchange_state(active.book, batches[-1]["end"])
