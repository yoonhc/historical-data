"""Audit the fixed first capture after decision logging, without changing the store."""

import argparse
import hashlib
import json
import runpy
import sys
import time
from collections import Counter
from pathlib import Path

from marketdata import depth
from marketdata.depth_refresh import headroom
from marketdata.domain import json_bytes, now, utc

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
RELEASE = ROOT / "reports/baseline/depth-decisions-2026-10-03"
COLLECTION = "6d39cd17a2604b97a53e2932ac829e19"
sys.path.insert(0, str(ROOT))

from scripts.verify_depth_capture import verify  # noqa: E402


def normalized(value):
    return json.loads(json_bytes(value))


def save(out, name, value):
    with (out / name).open("x") as handle:
        handle.write(json.dumps(normalized(value), ensure_ascii=False, indent=2) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decision_audit(reader, report):
    """Rebuild book state at each request and the one recorded real deferral."""
    log = report["refresh_decisions"]
    assert log["policy"] == "coverage_decisions_v1"
    assert log["deferred_evaluations"] == 1
    assert len(log["recent_deferrals"]) == 1
    deferred = log["recent_deferrals"][0]
    assert log["primary_reason_counts"] == {"pending_verification": 1}
    assert log["blocker_counts"] == {"minimum_interval": 1, "pending_verification": 1}
    for reason in deferred["blockers"]:
        assert log["first_by_reason"][reason] == log["last_by_reason"][reason] == deferred

    sessions, requests, anchors, early_times, boundaries = {}, [], [], [], []
    trace, proof, resolved = [], None, None
    previous_update, previous_epoch, last_snapshot_metadata = None, None, None
    same_connection_links = 0
    maximum_receive_gap = 0
    minimum_budget_boundary_distance = None
    epoch_reports = {e["epoch"]: e for e in report["epochs"]}
    segments = list(
        reader.db.execute(
            "SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id", (COLLECTION,)
        )
    )
    for segment in segments:
        for row in reader._revision_table(segment).to_pylist():
            epoch, sequence = row["epoch"], row["sequence"]
            if epoch not in sessions:
                sessions[epoch] = depth.new_session(
                    report, epoch_reports[epoch].get("reason", "initial"), sessions.get(epoch - 1)
                )
            session = sessions[epoch]
            if row["kind"] != "update":
                matches = [
                    json.loads(r["metadata"])
                    for r in reader.db.execute(
                        "SELECT metadata FROM fetches WHERE artifact_id=?", (row["source_id"],)
                    )
                ]
                matches = [
                    m
                    for m in matches
                    if m.get("collection_id") == COLLECTION
                    and m.get("sequence") == sequence
                    and m.get("epoch") == epoch
                ]
                assert len(matches) == 1
                metadata = matches[0]
                assert utc(metadata["received_at"]) == row["received_at"]
                entry = {"source_id": row["source_id"], **metadata}
                # Response headers are not needed for this audit.
                entry.pop("headers", None)
                if row["kind"] == "check":
                    context = metadata["refresh_context"]
                    assert context["sequence"] == sequence and context["epoch"] == epoch
                    assert context["action"] == "request" and context["blockers"] == []
                    assert session.book.synced and session.pending is None
                    assert normalized(headroom(session.book, report["top"])) == context["headroom"]
                    assert context["age_seconds"] >= 1
                    expected_reason = "periodic" if context["age_seconds"] >= 10 else "headroom"
                    assert context["reason"] == context["trigger"] == expected_reason
                    if expected_reason == "headroom":
                        assert context["headroom"]["low"]
                    requested = utc(context["requested_at"])
                    # Wall-clock request timestamps independently corroborate quota counts.
                    # They cannot reproduce the unpersisted monotonic clock exactly.
                    ages = [(requested - t).total_seconds() for t in early_times]
                    if ages:
                        distance = min(abs(age - 60) for age in ages)
                        minimum_budget_boundary_distance = (
                            distance
                            if minimum_budget_boundary_distance is None
                            else min(distance, minimum_budget_boundary_distance)
                        )
                        assert distance > 0.01, "Wall-clock quota boundary is ambiguous"
                    used_before = sum(0 <= age < 60 for age in ages)
                    assert context["budget"]["extra_used"] == used_before
                    used_after = used_before + int(expected_reason == "headroom")
                    assert context["budget_after_claim"]["extra_used"] == used_after
                    assert context["budget"]["extra_limit"] == 12
                    assert context["budget_after_claim"]["extra_limit"] == 12
                    assert used_after <= 12
                    if expected_reason == "headroom":
                        early_times.append(requested)
                    requests.append(entry)
                else:
                    assert "refresh_context" not in metadata
                    anchors.append(entry)
                last_snapshot_metadata = entry

            pending_before = session.pending[1] if session.pending else None
            session.take(row)
            if row["kind"] == "update":
                if previous_update is not None:
                    gap = (row["received_at"] - previous_update["received_at"]).total_seconds()
                    assert gap >= 0
                    maximum_receive_gap = max(maximum_receive_gap, gap)
                    if epoch == previous_epoch:
                        assert row["previous_update_id"] == previous_update["final_update_id"]
                        same_connection_links += 1
                    else:
                        boundaries.append(
                            {
                                "previous_epoch": previous_epoch,
                                "next_epoch": epoch,
                                "previous_sequence": previous_update["sequence"],
                                "next_sequence": sequence,
                                "receive_gap_seconds": gap,
                            }
                        )
                previous_update, previous_epoch = row, epoch

            if deferred["after_sequence"] - 3 <= sequence <= deferred["after_sequence"] + 2:
                trace.append(
                    {
                        **{
                            k: row[k]
                            for k in (
                                "sequence",
                                "epoch",
                                "kind",
                                "received_at",
                                "timestamp",
                                "source_id",
                                "first_update_id",
                                "previous_update_id",
                                "final_update_id",
                            )
                        },
                        "book_last_update_id": session.book.last,
                        "pending_snapshot_sequence": session.pending[1]
                        if session.pending
                        else None,
                        "pending_snapshot_update_id": session.pending[0].last
                        if session.pending
                        else None,
                        "headroom_after": headroom(session.book, report["top"]),
                    }
                )

            if sequence == deferred["after_sequence"]:
                assert row["kind"] == "update" and epoch == deferred["epoch"]
                assert deferred["phase"] == "before_flush"
                assert normalized(headroom(session.book, report["top"])) == deferred["headroom"]
                assert session.pending is not None and session.book.synced
                assert 0 <= deferred["age_seconds"] < 1
                assert deferred["headroom"]["low"] and deferred["headroom"]["top_complete"]
                assert deferred["action"] == "defer" and deferred["trigger"] == "headroom"
                assert deferred["blockers"] == ["pending_verification", "minimum_interval"]
                evaluated = utc(deferred["evaluated_at"])
                response_age = (
                    evaluated - utc(last_snapshot_metadata["received_at"])
                ).total_seconds()
                assert 0 <= deferred["age_seconds"] <= response_age < 1
                assert row["received_at"] <= evaluated
                active_early = [t for t in early_times if 0 <= (evaluated - t).total_seconds() < 60]
                assert len(active_early) == deferred["budget"]["extra_used"] == 1
                assert deferred["budget"]["extra_limit"] == 12
                assert session.pending[1] == last_snapshot_metadata["sequence"]
                assert session.book.last < session.pending[0].last
                proof = {
                    "record": deferred,
                    "previous_snapshot": last_snapshot_metadata,
                    "reconstructed_pending_sequence": session.pending[1],
                    "reconstructed_live_update_id": session.book.last,
                    "reconstructed_snapshot_update_id": session.pending[0].last,
                    "wall_seconds_since_rest_received": response_age,
                    "extra_claims_in_prior_60_wall_seconds": len(active_early),
                    "headroom_matches": True,
                    "pending_gate_matches": True,
                    "minimum_interval_supported_by_response_time_bound": True,
                    "extra_budget_exhausted": False,
                }
            if proof and resolved is None and sequence > deferred["after_sequence"]:
                if (
                    pending_before == proof["reconstructed_pending_sequence"]
                    and session.pending is None
                ):
                    check = session.checks[-1]
                    assert check["snapshot_sequence"] == proof["reconstructed_pending_sequence"]
                    assert check["equal"] and check["comparable"] and check["refreshed"]
                    resolved = {
                        "sequence": sequence,
                        "epoch": epoch,
                        "received_at": row["received_at"],
                        "check": check,
                        "headroom_after": headroom(session.book, report["top"]),
                        "receive_time_minus_deferral_time_seconds": (
                            row["received_at"] - utc(deferred["evaluated_at"])
                        ).total_seconds(),
                    }

    counts = Counter(r["refresh_context"]["reason"] for r in requests)
    assert counts == {
        "headroom": report["coverage_check_requests"],
        "periodic": report["periodic_check_requests"],
    }
    assert len(anchors) + len(requests) == report["snapshots"]
    assert log["evaluations"] == report["messages"]  # This capture; not a general guarantee.
    assert proof is not None and resolved is not None
    assert not resolved["headroom_after"]["low"]
    assert normalized({e: s.summary() for e, s in sessions.items()}) == normalized(
        {e: r["final"] for e, r in epoch_reports.items()}
    )
    return {
        "checked_at": now(),
        "collection_id": COLLECTION,
        "requests_checked": len(requests),
        "anchors_checked": len(anchors),
        "request_reason_counts": dict(counts),
        "all_request_headroom_matches_reconstructed_book": True,
        "all_request_gates_and_quota_counts_consistent": True,
        "minimum_wall_quota_boundary_distance_seconds": minimum_budget_boundary_distance,
        "same_connection_update_id_links_checked": same_connection_links,
        "maximum_update_receive_gap_seconds": maximum_receive_gap,
        "physical_connection_boundaries": boundaries,
        "evaluations": log["evaluations"],
        "deferred_evaluations": log["deferred_evaluations"],
        "primary_reason_counts": log["primary_reason_counts"],
        "blocker_counts": log["blocker_counts"],
        "deferral": proof,
        "resolved_on": resolved,
        "nearby_rows": trace,
        "limits": [
            "One closed capture and one observed deferral, not a 24-hour verdict.",
            "Monotonic request completion timestamps are not separately persisted; exact age cannot be independently replayed.",
            "Wall-clock request times corroborate quota counts away from 60-second boundaries.",
            "Next receive time is not the exact comparison processing time.",
            "Physical connection gaps are not reconstructed or declared continuous.",
        ],
    }, requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    out = parser.parse_args().output
    out.mkdir(parents=True, exist_ok=True)
    for name in (
        "capture-final.json",
        "replay.json",
        "raw-parity.json",
        "decision-audit.json",
        "request-contexts.json",
        "verification.json",
        "failure.json",
    ):
        if (out / name).exists():
            raise FileExistsError(f"Preserve existing evidence; use a new --output: {out / name}")
    started, clock = now(), time.monotonic()
    reader_type = runpy.run_path(
        str(ROOT / "reports/baseline/depth-retry-2026-09-29/check_operating_capture.py")
    )["ReadOnlyStore"]
    reader = reader_type(ROOT)
    try:
        activation = json.loads((RELEASE / "activation.json").read_bytes())
        release = json.loads((RELEASE / "release.json").read_bytes())
        assert activation["new_collection_id"] == COLLECTION
        record = reader.db.execute(
            "SELECT state,report FROM event_collections WHERE id=?", (COLLECTION,)
        ).fetchone()
        report = json.loads(record["report"])
        assert record["state"] == report["state"] == "complete" and report["complete"]
        assert report["epochs"][-1]["end_reason"] == "duration"
        save(out, "capture-final.json", report)
        protected = {p: sha(Path(p)) == h for p, h in release["protected_input_sha256"].items()}
        deployed = {p: sha(ROOT / p) == info["new_sha256"] for p, info in release["files"].items()}
        assert all(protected.values()) and all(deployed.values())
        print(
            "Frozen first completed capture; production source and protected files match release.",
            flush=True,
        )
        replayed = depth.replay(reader, COLLECTION)
        save(out, "replay.json", replayed)
        assert replayed["valid"] and replayed["replay_matches_live"]
        print(
            f"Replay passed: {replayed['rows']} rows, {replayed['verification_equal']} equal REST checks.",
            flush=True,
        )
        parity = verify(reader, COLLECTION, replayed=replayed)
        save(out, "raw-parity.json", parity)
        assert parity["raw_to_canonical_equal"] and parity["all_snapshot_checks_verified"]
        print(
            f"Raw parity passed: {parity['price_quantity_pairs_checked']} price/quantity pairs.",
            flush=True,
        )
        decisions, requests = decision_audit(reader, report)
        save(out, "decision-audit.json", decisions)
        save(out, "request-contexts.json", requests)
        final_record = reader.db.execute(
            "SELECT state,report FROM event_collections WHERE id=?", (COLLECTION,)
        ).fetchone()
        assert dict(final_record) == dict(record)
        assert all(sha(Path(p)) == h for p, h in release["protected_input_sha256"].items())
        assert all(sha(ROOT / p) == info["new_sha256"] for p, info in release["files"].items())
        result = {
            "started_at": started,
            "checked_at": now(),
            "duration_seconds": time.monotonic() - clock,
            "collection_id": COLLECTION,
            "capture_started_at": report["started_at"],
            "capture_finished_at": report["updated_at"],
            "state": report["state"],
            "messages": report["messages"],
            "persisted_messages": report["persisted_messages"],
            "snapshots": report["snapshots"],
            "connections": report["connections"],
            "replay_valid": replayed["valid"],
            "replay_matches_live": replayed["replay_matches_live"],
            "continuous_across_connections": replayed["continuous"],
            "raw_parity": parity,
            "requests_checked": decisions["requests_checked"],
            "deferred_evaluations": decisions["deferred_evaluations"],
            "deferral_context_and_resolution_verified": True,
            "production_source_matches_release": deployed,
            "protected_hash_matches": protected,
            "capture_report_unchanged": True,
            "catalog_mode": "ro with live WAL; shared layout locks",
            "forced_restart": False,
            "new_remote_requests": 0,
            "remaining": [
                "24-hour logging observation after 2026-10-04 11:30 KST",
                "2026-10-03 daily and restic completion after their actual scheduled runs",
            ],
            "evidence_sha256": {p.name: sha(p) for p in sorted(out.glob("*.json"))},
        }
        save(out, "verification.json", result)
        print(
            json.dumps(
                normalized(
                    {
                        k: result[k]
                        for k in (
                            "checked_at",
                            "duration_seconds",
                            "collection_id",
                            "messages",
                            "raw_parity",
                            "requests_checked",
                            "deferred_evaluations",
                            "deferral_context_and_resolution_verified",
                        )
                    }
                ),
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
    except Exception as exc:
        save(out, "failure.json", {"at": now(), "type": type(exc).__name__, "error": str(exc)})
        raise
    finally:
        reader.db.close()
        reader._files_db.close()


if __name__ == "__main__":
    main()
