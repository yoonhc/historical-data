"""Offline reference reduction from raw payloads using only the standard library.

No production Book.apply, History.align, Session.compare, parser, or replay is
imported. Snapshot alignment follows USD-M IDs; range replacement is a separate
project policy. Empty post-update maps are retained as calculation results.
"""

import gzip
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parent
SIDES = (("bids", "b"), ("asks", "a"))
FOCUS = {
    "c595e79851ef4ced8e4194f22cf58b84": 25253,
    "54d28ea721ed41fab80928c064514159": 27514,
}


def pairs(values):
    result = {Decimal(p): Decimal(q) for p, q in values}
    assert len(result) == len(values) and all(p > 0 and q >= 0 for p, q in result.items())
    return result


def levels(values):
    return [[str(p), str(q)] for p, q in sorted(values.items())]


@dataclass
class State:
    bids: dict
    asks: dict
    floor: Decimal
    ceiling: Decimal
    identity: int
    timestamp_ms: int
    bridged: bool = False
    last_raw: dict | None = None

    def inside(self, side, price):
        return price >= self.floor if side == "bids" else price <= self.ceiling

    def brief(self):
        return {
            "last_update_id": self.identity,
            "bid_floor": str(self.floor),
            "ask_ceiling": str(self.ceiling),
            **{
                side: {
                    "levels": len(getattr(self, side)),
                    "min_price": str(min(getattr(self, side))) if getattr(self, side) else None,
                    "max_price": str(max(getattr(self, side))) if getattr(self, side) else None,
                }
                for side, _ in SIDES
            },
        }

    def full(self):
        return {**self.brief(), "levels": {s: levels(getattr(self, s)) for s, _ in SIDES}}


def snapshot(raw):
    bids, asks = pairs(raw["bids"]), pairs(raw["asks"])
    assert bids and asks and all(q > 0 for q in [*bids.values(), *asks.values()])
    assert max(bids) < min(asks)
    return State(bids, asks, min(bids), max(asks), raw["lastUpdateId"], raw["T"])


def advance(state, raw):
    """Set subtraction plus absolute assignments, including an empty result."""
    assert raw["U"] <= raw["u"]
    if not state.bridged:
        if raw["u"] < state.identity:
            return state, "stale"
        assert raw["U"] <= state.identity <= raw["u"], "missing initial snapshot bridge"
    else:
        if raw["u"] == state.identity and raw == state.last_raw:
            return state, "duplicate"
        assert raw["pu"] == state.identity and raw["u"] > state.identity, "pu chain"
        assert raw["T"] >= state.timestamp_ms, "transaction time reversed"
    reduced = {}
    for side, field in SIDES:
        changes = {p: q for p, q in pairs(raw[field]).items() if state.inside(side, p)}
        untouched = {p: q for p, q in getattr(state, side).items() if p not in changes}
        reduced[side] = untouched | {p: q for p, q in changes.items() if q > 0}
    return replace(
        state, **reduced, identity=raw["u"], timestamp_ms=raw["T"], bridged=True, last_raw=raw
    ), "applied"


def same_top(a, b, top):
    return all(
        len(getattr(a, side)) >= top
        and len(getattr(b, side)) >= top
        and sorted(getattr(a, side).items(), reverse=side == "bids")[:top]
        == sorted(getattr(b, side).items(), reverse=side == "bids")[:top]
        for side, _ in SIDES
    )


def compare_ranges(old, new):
    assert old.identity == new.identity
    result = {}
    for side, _ in SIDES:
        a, b = getattr(old, side), getattr(new, side)
        common = {p for p in a.keys() | b.keys() if old.inside(side, p) and new.inside(side, p)}
        differences = [
            {
                "price": str(p),
                "old": str(a[p]) if p in a else None,
                "candidate": str(b[p]) if p in b else None,
            }
            for p in sorted(common)
            if a.get(p) != b.get(p)
        ]
        result[side] = {
            "common_prices": len(common),
            "common_equal": not differences,
            "differences": differences,
            "candidate_covers_old_domain": (
                new.floor <= old.floor if side == "bids" else new.ceiling >= old.ceiling
            ),
            "old_prices_outside_candidate": levels(
                {p: q for p, q in a.items() if not new.inside(side, p)}
            ),
            "candidate_prices_outside_old": levels(
                {p: q for p, q in b.items() if not old.inside(side, p)}
            ),
        }
    return result


def changes(before, raw):
    result = {}
    for side, field in SIDES:
        old, delta = getattr(before, side), pairs(raw[field])
        inside = {p: q for p, q in delta.items() if before.inside(side, p)}
        result[side] = {
            "message_prices": len(delta),
            "in_range_prices": len(inside),
            "deleted_existing": sum(q == 0 and p in old for p, q in inside.items()),
            "zero_for_absent": sum(q == 0 and p not in old for p, q in inside.items()),
            "added": sum(q > 0 and p not in old for p, q in inside.items()),
            "quantity_changed": sum(q > 0 and p in old and q != old[p] for p, q in inside.items()),
            "quantity_unchanged": sum(
                q > 0 and p in old and q == old[p] for p, q in inside.items()
            ),
            "outside_positive": levels(
                {p: q for p, q in delta.items() if q > 0 and not before.inside(side, p)}
            ),
            "outside_zero_count": sum(
                q == 0 and not before.inside(side, p) for p, q in delta.items()
            ),
        }
    return result


def align(raw, history, target):
    candidate = snapshot(raw)
    used = []
    if candidate.identity == target:
        return candidate, used
    for row in history:
        candidate, outcome = advance(candidate, row["raw"])
        if outcome == "applied":
            used.append(row["sequence"])
    assert candidate.identity == target and candidate.bridged, "snapshot did not reach target ID"
    return candidate, used


def inspect_case(manifest):
    cid = manifest["collection_id"]
    blob = (OUT / manifest["bundle"]).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == manifest["bundle_sha256"]
    rows = [json.loads(line) for line in gzip.decompress(blob).splitlines()]
    report_blob = (OUT / f"{cid}-report.json").read_bytes()
    assert hashlib.sha256(report_blob).hexdigest() == manifest["report_file_sha256"]
    report = json.loads(report_blob)
    epoch, top = report["epochs"][manifest["epoch"]], report["top"]
    assert rows[0]["kind"] == "anchor"
    current, pending, history, previous_raw = snapshot(rows[0]["raw"]), None, [], None
    counts, adoptions, protocol, kept, focus, before_failure = Counter(), [], [], None, None, None
    for row in rows[1:]:
        raw, seq = row["raw"], row["sequence"]
        candidate, check_sequence, aligned_rows = None, None, []
        if row["kind"] == "update":
            if previous_raw is not None:
                assert raw == previous_raw or (
                    raw["pu"] == previous_raw["u"] and raw["u"] > previous_raw["u"]
                )
            previous_raw = raw
            history.append(row)
            before_failure = current
            current, outcome = advance(current, raw)
            counts[outcome] += 1
            if kept is not None:
                kept, _ = advance(kept, raw)
            if pending is not None:
                waiting, check_sequence, already = pending
                waiting, outcome = advance(waiting, raw)
                if outcome == "applied":
                    candidate, aligned_rows = waiting, [*already, seq]
                    pending = None
                else:
                    pending = (waiting, check_sequence, already)
        else:
            assert row["kind"] == "check" and pending is None
            check_sequence = seq
            if raw["lastUpdateId"] <= current.identity:
                candidate, aligned_rows = align(raw, history, current.identity)
            else:
                pending = (snapshot(raw), seq, [])
        if candidate is not None:
            assert candidate.identity == current.identity
            assert same_top(current, candidate, top), f"top mismatch at {seq}"
            comparison = compare_ranges(current, candidate)
            adoption = {
                "snapshot_sequence": check_sequence,
                "adopted_at_sequence": seq,
                "last_update_id": current.identity,
                "alignment_update_sequences": aligned_rows,
                "old": current.brief(),
                "candidate": candidate.brief(),
                "comparison": comparison,
            }
            adoptions.append(adoption)
            if check_sequence == FOCUS[cid]:
                assert kept is None
                kept = current
                focus = {**adoption, "old_full": current.full(), "candidate_full": candidate.full()}
            current = replace(
                candidate,
                bridged=True,
                last_raw=current.last_raw,
                timestamp_ms=current.timestamp_ms,
            )
        if row["kind"] == "update":
            protocol.append({"sequence": seq, "U": raw["U"], "u": raw["u"], "pu": raw["pu"]})
            if seq != manifest["last_sequence"]:
                assert current.bids and current.asks and max(current.bids) < min(current.asks)
                assert min(len(current.bids), len(current.asks)) >= top
    assert pending is None and focus is not None and kept is not None
    assert len(rows) == manifest["rows"]
    expected = [
        (c["snapshot_sequence"], c["last_update_id"])
        for c in epoch["final"]["checks"]
        if c.get("refreshed")
    ]
    assert [(a["snapshot_sequence"], a["last_update_id"]) for a in adoptions] == expected
    final = epoch["final"]["book"]
    preserved = not current.bids or not current.asks
    represented = before_failure if preserved else current
    assert represented.identity == final["last_update_id"]
    assert represented.floor == Decimal(final["bid_floor"]) and represented.ceiling == Decimal(
        final["ask_ceiling"]
    )
    for side, _ in SIDES:
        assert len(getattr(represented, side)) == final["known_" + side[:-1] + "_levels"]
        assert sorted(getattr(represented, side).items(), reverse=side == "bids")[:top] == [
            (Decimal(p), Decimal(q)) for p, q in final[side]
        ]
    # Compare the entire independently calculated book, not just top/counts.
    # The stored hash uses fixed 18-decimal strings; normalization here changes
    # only its serialization, never the arithmetic or choice of trusted prices.
    hashed_book = {
        side: [
            [format(p, ".18f"), format(q, ".18f")]
            for p, q in sorted(getattr(represented, side).items(), reverse=side == "bids")
        ]
        for side, _ in SIDES
    }
    hashed_book["u"] = represented.identity
    encoded_book = json.dumps(hashed_book, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded_book).hexdigest() == final["book_sha256"]
    subset = {}
    for side, _ in SIDES:
        premise = focus["comparison"][side]
        # There are no later refreshes after this chosen last adoption.
        assert adoptions[-1]["snapshot_sequence"] == FOCUS[cid]
        a, b = getattr(kept, side), getattr(current, side)
        holds = a.keys() <= b.keys() and all(b[p] == q for p, q in a.items())
        applicable = premise["common_equal"] and premise["candidate_covers_old_domain"]
        assert not applicable or holds
        subset[side] = {
            "premises_met": applicable,
            "values_subset": holds,
            "old_remaining": len(a),
            "candidate_remaining": len(b),
        }
    return {
        "collection_id": cid,
        "epoch": manifest["epoch"],
        "rows": len(rows),
        "independent_counts": dict(counts),
        "wire_chain_links": len(history) - 1,
        "adoptions": adoptions,
        "focus": focus,
        "subset_after_failure": subset,
        "failure": {
            "sequence": rows[-1]["sequence"],
            "received_at": rows[-1]["received_at"],
            "U": rows[-1]["raw"]["U"],
            "u": rows[-1]["raw"]["u"],
            "pu": rows[-1]["raw"]["pu"],
            "before": before_failure.full(),
            "after_raw_reduction": current.full(),
            "keep_previous_range_after_raw_reduction": kept.full(),
            "changes": changes(before_failure, rows[-1]["raw"]),
            "production_preserves_before_on_empty": preserved,
            "existing_report_final_matches_independent": True,
        },
        "protocol": protocol,
        "all_adoptions_common_equal": all(
            v["common_equal"] for a in adoptions for v in a["comparison"].values()
        ),
        "original_partial_preserved": report["state"] == "partial" and not report["complete"],
    }


def main():
    manifest = json.loads((OUT / "inputs.json").read_bytes())
    results = [inspect_case(case) for case in manifest["cases"]]
    with (OUT / "results.json").open("x") as handle:
        json.dump(results, handle, indent=2)
        handle.write("\n")
    for case in results:
        print(
            json.dumps(
                {
                    k: case[k]
                    for k in (
                        "collection_id",
                        "rows",
                        "independent_counts",
                        "all_adoptions_common_equal",
                        "subset_after_failure",
                    )
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
