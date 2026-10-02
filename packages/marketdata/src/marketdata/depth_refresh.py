"""Bounded early snapshot checks for a finite, known order-book price range."""

from collections import deque
from decimal import Decimal

POLICY = "bounded_headroom_v1"
MIN_INTERVAL = 1.0
EXTRA_REQUESTS = 12
WINDOW = 60.0
LEVEL_FRACTION = Decimal("0.75")
PRICE_FRACTION = Decimal("0.25")


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

    def decision(self, book, top, *, current, last_request, periodic_seconds):
        while self.extra and self.extra[0] <= current - WINDOW:
            self.extra.popleft()
        age = current - last_request
        if age < MIN_INTERVAL:
            return None
        margin = headroom(book, top)
        if age >= periodic_seconds:
            return {"reason": "periodic", "headroom": margin}
        if margin["low"] and len(self.extra) < EXTRA_REQUESTS:
            return {"reason": "headroom", "headroom": margin}
        return None

    def claim(self, decision, current):
        if decision["reason"] == "headroom":
            while self.extra and self.extra[0] <= current - WINDOW:
                self.extra.popleft()
            if len(self.extra) >= EXTRA_REQUESTS:
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
