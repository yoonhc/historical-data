"""Audit actual recovery candidates against a separate raw-payload reducer.

Run from the repository root. The catalog is opened read-only. Existing evidence
and historical quality records are inputs and are never rewritten.
"""

import gzip
import importlib.util
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from marketdata import depth
from marketdata.domain import json_bytes, now, sha256, utc
from marketdata.store import Store

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent
PRIOR = ROOT / "reports/baseline/depth-history-2026-09-26/historical-cases.json"


class Reader:
    raw = Store.raw
    file_bytes = Store.file_bytes

    def __init__(self):
        self.root = ROOT
        self.db = sqlite3.connect((ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self._files_db = self.db
        self.payloads = {}

    def original(self, row):
        source = row["source_id"]
        if source not in self.payloads:
            blob, _ = self.raw(source)
            self.payloads[source] = json.loads(
                gzip.decompress(blob) if row["kind"] == "update" else blob
            )
        payload = self.payloads[source]
        return (
            json.loads(payload[row["source_row"]]["message"])
            if row["kind"] == "update"
            else payload
        )


def reduce_raw(reader, snapshot, updates):
    """Absolute changes over raw dictionaries; no production book or parser calls.

    Apply outer-price changes too, then restrict the result to the REST snapshot's
    known range. This oracle does not reuse Book.apply's in-range filtering loop.
    """
    raw = reader.original(snapshot)
    books = {side: {Decimal(p): Decimal(q) for p, q in raw[side]} for side in ("bids", "asks")}
    floor, ceiling = min(books["bids"]), max(books["asks"])
    last, signature, bridged = raw["lastUpdateId"], None, False
    applied = []
    for row in updates:
        event = reader.original(row)
        if not bridged:
            if event["u"] < last:
                continue
            assert event["U"] <= last <= event["u"]
        elif event["u"] == last and event == signature:
            continue
        else:
            assert event["u"] > last and event["pu"] == last
        for side, field in (("bids", "b"), ("asks", "a")):
            for price, quantity in event[field]:
                p, q = Decimal(price), Decimal(quantity)
                if q:
                    books[side][p] = q
                else:
                    books[side].pop(p, None)
        last, signature, bridged = event["u"], event, True
        applied.append(row["sequence"])
    assert bridged
    return (
        {p: q for p, q in books["bids"].items() if p >= floor},
        {p: q for p, q in books["asks"].items() if p <= ceiling},
        last,
        applied,
    )


def main():
    spec = importlib.util.spec_from_file_location(
        "coverage_inputs", ROOT / "reports/baseline/depth-coverage-2026-09-25/analyze.py"
    )
    inputs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inputs)
    reader = Reader()
    cases, alignments = [], []
    try:
        for case in json.loads(PRIOR.read_bytes())["cases"]:
            original = reader.db.execute(
                "SELECT report FROM event_collections WHERE id=?", (case["collection_id"],)
            ).fetchone()[0]
            assert sha256(original.encode()) == case["original_report_sha256"]
            report = json.loads(original)
            epoch = next(e for e in report["epochs"] if e["epoch"] == case["epoch"])
            rows, _, _ = inputs.load_rows(reader.db, report, epoch)
            policy = {
                **report,
                "history_policy": depth.HISTORY_POLICY,
                "history_limits": dict(depth.HISTORY_LIMITS),
            }
            previous, prior_rows = None, []
            if epoch["reason"] in depth.SAME_SOCKET_REASONS:
                prior = next(e for e in report["epochs"] if e["epoch"] == epoch["epoch"] - 1)
                prior_rows, _, _ = inputs.load_rows(reader.db, report, prior)
                previous = depth.new_session(policy, "initial")
                for row in prior_rows:
                    if row["kind"] == "update":
                        previous.remember(row)
            all_rows = {row["sequence"]: row for row in [*prior_rows, *rows]}
            active = depth.new_session(policy, epoch["reason"], previous)
            failed_sequence = None
            for row in rows:
                try:
                    active.take(row)
                except depth.SyncLost as exc:
                    active.error, failed_sequence = str(exc), row["sequence"]
                    break
                if row["kind"] == "anchor":
                    proof = active.anchor_history
                elif row["kind"] == "check" and active.checks:
                    check = active.checks[-1]
                    proof = (
                        check.get("history_alignment")
                        if check.get("refreshed") and check["snapshot_sequence"] == row["sequence"]
                        else None
                    )
                else:
                    proof = None
                if not proof or not proof["aligned"]:
                    continue
                retained = [all_rows[event["sequence"]] for event, _, _ in active.history.events]
                assert all(update["sequence"] < row["sequence"] for update in retained)
                assert all(update["received_at"] <= row["received_at"] for update in retained)
                bids, asks, last, applied = reduce_raw(reader, row, retained)
                assert (active.book.bids, active.book.asks, active.book.last) == (bids, asks, last)
                assert (len(applied), applied[0], applied[-1]) == (
                    proof["applied"],
                    proof["first_sequence"],
                    proof["last_sequence"],
                )
                alignments.append(
                    {
                        "collection_id": case["collection_id"],
                        "epoch": case["epoch"],
                        "kind": row["kind"],
                        "snapshot_sequence": row["sequence"],
                        "snapshot_source_id": row["source_id"],
                        "snapshot_last_update_id": row["final_update_id"],
                        "aligned_last_update_id": last,
                        "applied_sequences": applied,
                        "known_bid_levels_equal": len(bids),
                        "known_ask_levels_equal": len(asks),
                        "all_inputs_precede_snapshot_receipt": True,
                    }
                )
            assert json.loads(json_bytes(active.summary())) == case["scenario_final"]
            assert failed_sequence == case["scenario_failed_sequence"]
            cases.append(
                {
                    "collection_id": case["collection_id"],
                    "epoch": case["epoch"],
                    "recorded_scenario_reproduced": True,
                    "rows_raw_equal": len(rows),
                    "scenario_failed_sequence": failed_sequence,
                }
            )
        record = reader.db.execute(
            "SELECT state, report FROM event_collections WHERE dataset='book_depth' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        live = json.loads(record["report"])
        checks = [c for e in live["epochs"] for c in e.get("final", {}).get("checks", [])]
        output = {
            "checked_at": now(),
            "cases": cases,
            "raw_reference_alignments": alignments,
            "live_observation": {
                "state": record["state"],
                **{
                    key: live.get(key)
                    for key in (
                        "id",
                        "started_at",
                        "updated_at",
                        "messages",
                        "persisted_messages",
                        "connections",
                        "synchronized",
                        "sync_losses",
                        "coverage_resets",
                        "lagged_snapshots",
                        "history_policy",
                    )
                },
                "last_receive_age_seconds": (now() - utc(live["last_received_at"])).total_seconds(),
                "checks": len(checks),
                "equal": sum(c["equal"] for c in checks),
                "history_checks": sum("history_alignment" in c for c in checks),
            },
            "input_evidence_sha256": sha256(PRIOR.read_bytes()),
            "source_sha256": {
                "depth.py": sha256(Path(depth.__file__).read_bytes()),
                "audit_history.py": sha256(Path(__file__).read_bytes()),
                "test_depth_reference.py": sha256(
                    (ROOT / "tests/test_depth_reference.py").read_bytes()
                ),
            },
            "scope": "Original receipt order and epoch boundaries; no live failure-rate estimate.",
        }
        (OUT / "audit.json").write_text(json.dumps(json.loads(json_bytes(output)), indent=2) + "\n")
        print(
            json.dumps(
                {
                    "cases": len(cases),
                    "raw_reference_alignments": len(alignments),
                    "live": output["live_observation"],
                },
                default=str,
                indent=2,
            )
        )
    finally:
        reader.db.close()


if __name__ == "__main__":
    main()
