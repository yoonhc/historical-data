"""Request budgeting and durable direct evidence, independent of historical gap repair."""

import json

import httpx
import pytest
from marketdata import depth
from marketdata import depth_stream as live
from marketdata.binance import Fetcher, SourceError
from marketdata.depth_refresh import RefreshChecks, new_decision_log, record_decision
from test_depth import BASE, row
from test_depth_refresh import Exchange


def book(asks=700):
    payload = {
        "lastUpdateId": 100,
        "T": 1704067200000,
        "E": 1704067200001,
        "bids": [[str(p), "1"] for p in range(1000, 0, -1)],
        "asks": [[str(p), "1"] for p in range(1001, 2001)],
    }
    result = depth.Book(row(payload, "anchor"))
    for p in sorted(result.asks)[: 1000 - asks]:
        del result.asks[p]
    return result


def assess(controller, value, at, *, last=None, **kwargs):
    return controller.assess(
        value,
        20,
        current=at,
        last_request=at - 1 if last is None else last,
        periodic_seconds=10,
        **kwargs,
    )


def test_logging_keeps_all_twelve_early_slots_and_periodic_checks_available():
    controller, value = RefreshChecks(), book(700)
    for stamp in range(1, 13):
        decision = assess(controller, value, stamp)
        assert decision["action"] == "request"
        controller.claim(decision, stamp)
    assert assess(controller, value, 13)["reason"] == "extra_budget"
    assert assess(controller, value, 22, last=12)["reason"] == "periodic"
    decision = assess(controller, value, 61)
    assert decision["action"] == "request"
    controller.claim(decision, 61)
    assert controller.budget(61)["extra_used"] == 12


@pytest.mark.parametrize(
    "settings,reason",
    [
        ({"last": 9.5}, "minimum_interval"),
        ({"pending": True}, "pending_verification"),
        ({"stop_at": 10}, "deadline"),
        ({"synchronized": False}, "not_synchronized"),
    ],
)
def test_low_headroom_never_bypasses_another_gate(settings, reason):
    decision = assess(RefreshChecks(), book(300), 10, **settings)
    assert decision["action"] == "defer" and reason in decision["blockers"]


def test_idle_evaluations_are_not_counted_as_blocked_requests():
    controller = RefreshChecks()
    log = new_decision_log()
    record_decision(
        log,
        assess(controller, book(1000), 0, last=0),
        epoch=0,
        after_sequence=1,
        at=BASE,
        phase="before_flush",
    )
    assert log["evaluations"] == 1 and log["deferred_evaluations"] == 0
    assert not log["first_by_reason"] and log["last"] is None


def test_all_applicable_blockers_are_direct_evidence_but_one_evaluation_is_counted_once():
    controller, critical = RefreshChecks(), book(300)
    for stamp in range(1, 13):
        controller.claim(assess(controller, critical, stamp), stamp)
    decision = assess(controller, critical, 12.5, last=12, pending=True)
    assert decision["blockers"] == ["pending_verification", "minimum_interval", "extra_budget"]
    log = new_decision_log()
    for seq in range(100):
        record_decision(log, decision, epoch=0, after_sequence=seq, at=BASE, phase="before_flush")
    assert log["deferred_evaluations"] == 100
    assert sum(log["primary_reason_counts"].values()) == 100
    assert sum(log["blocker_counts"].values()) == 300
    assert len(log["recent_deferrals"]) == 8
    assert log["first_by_reason"]["extra_budget"]["after_sequence"] == 0
    assert log["last_by_reason"]["extra_budget"]["after_sequence"] == 99


def test_pending_capture_is_explained_and_successful_request_context_is_durable(store, monkeypatch):
    exchange = Exchange(monkeypatch, future=True)
    report = exchange.capture(store, seconds=2)
    log = report["refresh_decisions"]
    assert log["blocker_counts"]["pending_verification"] > 0
    assert log["blocker_counts"]["minimum_interval"] > 0
    assert len(exchange.calls) == 2
    metadata = [json.loads(r[0]) for r in store.db.execute("SELECT metadata FROM fetches")]
    contexts = [m["refresh_context"] for m in metadata if m.get("kind") == "check"]
    assert len(contexts) == 1
    assert contexts[0]["budget"]["extra_used"] == 0
    assert contexts[0]["budget_after_claim"]["extra_used"] == 1
    assert contexts[0]["reason"] == "headroom"
    assert depth.replay(store)["replay_matches_live"]


def test_flush_recheck_records_deadline_once_and_sends_no_request(store, monkeypatch):
    exchange = Exchange(monkeypatch)
    original = live.persist_messages

    def slow(*args):
        result = original(*args)
        if exchange.ms >= 1300:
            exchange.ms += 6000
        return result

    monkeypatch.setattr(live, "persist_messages", slow)
    report = exchange.capture(store, seconds=3)
    log = report["refresh_decisions"]
    assert len(exchange.calls) == 1 and report["coverage_check_requests"] == 0
    assert log["primary_reason_counts"]["deadline"] == 1
    assert log["last"]["phase"] == "after_flush"
    assert log["evaluations"] == report["messages"]
    assert report["messages"] == report["persisted_messages"]


def test_failed_request_preserves_budget_context_and_existing_cooldown(store, monkeypatch):
    exchange = Exchange(monkeypatch)
    original = exchange.fetch

    def limited(request):
        if not exchange.calls:
            return original(request)
        exchange.calls.append(exchange.ms / 1000)
        return httpx.Response(429, content=b"limited early check")

    exchange.fetcher.client.close()
    exchange.fetcher = Fetcher(
        httpx.Client(transport=httpx.MockTransport(limited)), retries=0, sleep=exchange.sleep
    )
    with pytest.raises(SourceError):
        exchange.capture(store)
    report = json.loads((store.root / "state/depth-latest.json").read_bytes())
    metadata = [json.loads(r[0]) for r in store.db.execute("SELECT metadata FROM fetches")]
    error = next(m for m in metadata if m.get("status") == 429)
    context = error["refresh_context"]
    assert context["budget_after_claim"]["extra_used"] == 1
    assert context["budget_after_claim"]["extra_limit"] == 12
    assert len(exchange.calls) == 2 and report["state"] == "failed"
    assert report["messages"] == report["persisted_messages"] == len(exchange.receipts)
    assert (store.root / "state/depth-rest-cooldown.json").exists()


def test_failure_pins_prior_decisions_as_independent_evidence(store, monkeypatch):
    exchange = Exchange(monkeypatch, shock=True)
    report = exchange.capture(store, seconds=2)
    prior = report["epochs"][0]["prior_refresh_decisions"]
    assert report["state"] == "partial" and report["coverage_resets"] == 1
    assert prior["evaluations"] < report["refresh_decisions"]["evaluations"]
    assert prior is not report["refresh_decisions"]
    prior["primary_reason_counts"]["test_mutation"] = 1
    assert "test_mutation" not in report["refresh_decisions"]["primary_reason_counts"]
    assert depth.replay(store)["replay_matches_live"]
