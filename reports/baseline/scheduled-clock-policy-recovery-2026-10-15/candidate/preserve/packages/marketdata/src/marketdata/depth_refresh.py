"""Bounded early snapshot checks for a finite, known order-book price range."""

from collections import deque
from decimal import Decimal

POLICY = "bounded_headroom_v1"
MIN_INTERVAL = 1.0
EXTRA_REQUESTS = 12
WINDOW = 60.0
LEVEL_FRACTION = Decimal("0.75")
PRICE_FRACTION = Decimal("0.25")
DECISION_LOG_POLICY = "coverage_decisions_v1"


def headroom(book, top):
    sides = {}
    for side, bid in (("bids", True), ("asks", False)):
        levels = getattr(book, side)
        initial = getattr(book, "snapshot_bid_levels" if bid else "snapshot_ask_levels")
        span = getattr(book, "snapshot_bid_span" if bid else "snapshot_ask_span")
        distance = max(levels) - book.bid_floor if bid else book.ask_ceiling - min(levels)
        threshold = int(initial * LEVEL_FRACTION)
        sides[side] = {
            "levels": len(levels),
            "level_threshold": threshold,
            "price_room": distance,
            "snapshot_price_span": span,
            "low_levels": len(levels) < threshold,
            "low_price_room": span > 0 and distance < span * PRICE_FRACTION,
        }
    return {
        "top_complete": all(s["levels"] >= top for s in sides.values()),
        "low": any(s["low_levels"] or s["low_price_room"] for s in sides.values()),
        "sides": sides,
    }


class RefreshChecks:
    """Nonblocking quota for extra checks; never reset it on epoch/reconnect."""

    def __init__(self):
        self.extra = deque()

    def budget(self, current):
        while self.extra and self.extra[0] <= current - WINDOW:
            self.extra.popleft()
        return {
            "extra_used": len(self.extra),
            "extra_limit": EXTRA_REQUESTS,
            "oldest_extra_expires_in_seconds": max(0, self.extra[0] + WINDOW - current)
            if self.extra
            else None,
        }

    def assess(
        self,
        book,
        top,
        *,
        current,
        last_request,
        periodic_seconds,
        synchronized=True,
        pending=False,
        stop_at=None,
    ):
        """One decision and every applicable gate; no request or sleep is performed."""
        budget = self.budget(current)
        margin = headroom(book, top)
        age = current - last_request
        trigger = "periodic" if age >= periodic_seconds else "headroom" if margin["low"] else None
        result = {
            "action": "idle",
            "reason": "not_due",
            "trigger": trigger,
            "headroom": margin,
            "budget": budget,
            "age_seconds": age,
            "blockers": [],
        }
        if trigger is None:
            return result
        blockers = result["blockers"]
        if stop_at is not None and current >= stop_at:
            blockers.append("deadline")
        if not synchronized:
            blockers.append("not_synchronized")
        if pending:
            blockers.append("pending_verification")
        if age < MIN_INTERVAL:
            blockers.append("minimum_interval")
        if trigger == "headroom" and budget["extra_used"] >= EXTRA_REQUESTS:
            blockers.append("extra_budget")
        result.update(
            action="defer" if blockers else "request", reason=blockers[0] if blockers else trigger
        )
        return result

    def decision(self, book, top, *, current, last_request, periodic_seconds):
        result = self.assess(
            book, top, current=current, last_request=last_request, periodic_seconds=periodic_seconds
        )
        return result if result["action"] == "request" else None

    def claim(self, decision, current):
        if decision.get("action") != "request":
            raise RuntimeError("Cannot claim a deferred or idle depth check")
        if decision["reason"] == "headroom":
            budget = self.budget(current)
            if budget["extra_used"] >= EXTRA_REQUESTS:
                raise RuntimeError("Extra depth check quota exceeded")
            self.extra.append(current)


def metadata():
    return {
        "policy": POLICY,
        "min_interval_seconds": MIN_INTERVAL,
        "extra_requests": EXTRA_REQUESTS,
        "window_seconds": WINDOW,
        "level_fraction": str(LEVEL_FRACTION),
        "price_fraction": str(PRICE_FRACTION),
        "scope": "extra checks within this capture; survives resync/reconnect; periodic checks and recovery anchors are separate",
    }


def new_decision_log():
    return {
        "policy": DECISION_LOG_POLICY,
        "evaluations": 0,
        "deferred_evaluations": 0,
        "primary_reason_counts": {},
        "blocker_counts": {},
        "first_by_reason": {},
        "last_by_reason": {},
        "recent_deferrals": [],
        "last": None,
        "count_semantics": "one terminal assessment per check_coverage invocation; deferred evaluations are neither HTTP calls nor unique outage episodes",
    }


def record_decision(log, decision, *, epoch, after_sequence, at, phase):
    """Bounded summaries, with first/last direct evidence for every blocking reason."""
    log["evaluations"] += 1
    if decision["action"] == "idle":
        return
    record = {
        "epoch": epoch,
        "after_sequence": after_sequence,
        "evaluated_at": at,
        "phase": phase,
        **decision,
    }
    log["last"] = record
    if decision["action"] != "defer":
        return
    log["deferred_evaluations"] += 1
    reason = decision["reason"]
    counts = log["primary_reason_counts"]
    counts[reason] = counts.get(reason, 0) + 1
    for blocker in decision["blockers"]:
        counts = log["blocker_counts"]
        counts[blocker] = counts.get(blocker, 0) + 1
        log["first_by_reason"].setdefault(blocker, record)
        log["last_by_reason"][blocker] = record
    log["recent_deferrals"] = (log["recent_deferrals"] + [record])[-8:]
