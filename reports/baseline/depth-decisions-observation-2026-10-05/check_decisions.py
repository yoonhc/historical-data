"""Replay every retained deferral sample and both failure neighborhoods, read-only."""

import gzip
import importlib.util
import json
import os
from datetime import timedelta

from audit import OUT, ROOT, ReadOnlyStore, save
from marketdata import depth
from marketdata.depth_refresh import headroom
from marketdata.domain import json_bytes, now, utc


def normalized(value):
    return json.loads(json_bytes(value))


def brief(row):
    return {
        k: row[k]
        for k in (
            "sequence",
            "epoch",
            "kind",
            "received_at",
            "timestamp",
            "source_id",
            "source_row",
            "first_update_id",
            "previous_update_id",
            "final_update_id",
        )
    }


def view(session):
    if session.book is None:
        return None
    return {
        "last_update_id": session.book.last,
        "headroom": headroom(session.book, session.top),
        "pending_sequence": session.pending[1] if session.pending else None,
        "pending_update_id": session.pending[0].last if session.pending else None,
        "refreshes": session.refreshes,
        "error": session.error,
    }


def samples(report):
    logs = [report["refresh_decisions"]] + [
        e["prior_refresh_decisions"] for e in report["epochs"] if "prior_refresh_decisions" in e
    ]
    records = {}
    for log in logs:
        for item in [
            *log["first_by_reason"].values(),
            *log["last_by_reason"].values(),
            *log["recent_deferrals"],
        ]:
            key = item["epoch"], item["after_sequence"], item["evaluated_at"]
            assert key not in records or records[key] == item
            records[key] = item
    return [records[k] for k in sorted(records)]


def audit(reader, report, parity):
    cid = report["id"]
    selected = samples(report)
    metadata = {}
    for row in reader.db.execute(
        "SELECT artifact_id,metadata FROM fetches WHERE observed_at>=? AND observed_at<?",
        (
            (utc(report["started_at"]) - timedelta(seconds=10)).isoformat(),
            (utc(report["updated_at"]) + timedelta(seconds=10)).isoformat(),
        ),
    ):
        m = json.loads(row["metadata"])
        if m.get("collection_id") == cid and m.get("kind") in ("anchor", "check"):
            assert m["sequence"] not in metadata
            metadata[m["sequence"]] = {**m, "source_id": row["artifact_id"]}
    assert len(metadata) == report["snapshots"]
    early = sorted(
        utc(m["refresh_context"]["requested_at"])
        for m in metadata.values()
        if m.get("refresh_context", {}).get("reason") == "headroom"
    )
    assert len(early) == report["coverage_check_requests"]
    failure_path = OUT / f"{cid}.json"
    failures = json.loads(failure_path.read_bytes())["failures"] if failure_path.exists() else []
    needed = {d["epoch"] for d in selected}
    for failure in failures:
        needed.add(failure["epoch"])
        needed.add(failure["same_socket_recovered_epoch"])
    for epoch in sorted(needed, reverse=True):
        while report["epochs"][epoch]["reason"] in depth.SAME_SOCKET_REASONS:
            epoch -= 1
            needed.add(epoch)
    anchors = {m["epoch"]: m["sequence"] for m in metadata.values() if m["kind"] == "anchor"}
    ends = {ep: anchors.get(ep + 1, report["messages"] + report["snapshots"]) - 1 for ep in anchors}
    wanted_rows = {
        seq for d in selected for seq in range(d["after_sequence"] - 2, d["after_sequence"] + 5)
    }
    wanted_rows.update(
        seq
        for f in failures
        for seq in range(f["failure"]["sequence"] - 4, f["failure"]["sequence"] + 4)
    )
    proofs, trace, sessions, raw_rows, pairs, request_checks = [], [], {}, 0, 0, 0
    by_sequence = {d["after_sequence"]: d for d in selected}
    assert len(by_sequence) == len(selected)
    segments = reader.db.execute(
        "SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id", (cid,)
    ).fetchall()
    for segment in segments:
        if not any(
            segment["last_id"] >= anchors[e] and segment["first_id"] <= ends[e] for e in needed
        ):
            continue
        payloads = {}
        for row in reader._revision_table(segment).to_pylist():
            ep, seq = row["epoch"], row["sequence"]
            if ep not in needed:
                continue
            if ep not in sessions:
                sessions[ep] = depth.new_session(
                    report, report["epochs"][ep]["reason"], sessions.get(ep - 1)
                )
            session = sessions[ep]
            before = view(session) if seq in wanted_rows else None
            checks_before = len(session.checks)
            if row["kind"] == "check":
                m = metadata[seq]
                context = m["refresh_context"]
                assert m["source_id"] == row["source_id"]
                assert normalized(headroom(session.book, report["top"])) == context["headroom"]
                assert context["action"] == "request" and not context["blockers"]
                assert (
                    context["age_seconds"] >= 1 and session.pending is None and session.book.synced
                )
                request_checks += 1
                for proof in proofs:
                    if proof["record"]["epoch"] == ep and "next_request" not in proof:
                        proof["next_request"] = {
                            "sequence": seq,
                            "requested_at": context["requested_at"],
                            "reason": context["reason"],
                        }
            try:
                session.take(row)
            except depth.SyncLost as exc:
                session.error, session.failure = str(exc), exc
                assert any(f["failure"]["sequence"] == seq for f in failures)
            margin = headroom(session.book, report["top"])
            for proof in proofs:
                if proof["record"]["epoch"] != ep or session.error:
                    continue
                if not margin["low"] and "first_headroom_normal" not in proof:
                    proof["first_headroom_normal"] = {
                        **brief(row),
                        "headroom": margin,
                        "refreshes": session.refreshes,
                    }
                if proof["pending_sequence"] is not None and "pending_resolution" not in proof:
                    matched = [
                        c
                        for c in session.checks[checks_before:]
                        if c["snapshot_sequence"] == proof["pending_sequence"]
                    ]
                    if matched:
                        assert len(matched) == 1
                        proof["pending_resolution"] = {
                            **brief(row),
                            "check": matched[0],
                            "headroom": margin,
                        }
            if seq in by_sequence:
                record = by_sequence[seq]
                assert record["epoch"] == ep and record["action"] == "defer"
                assert (
                    normalized(margin) == record["headroom"]
                    and margin["low"]
                    and margin["top_complete"]
                )
                assert session.book.synced and not session.error
                assert bool(session.pending) == ("pending_verification" in record["blockers"])
                assert "minimum_interval" in record["blockers"] and 0 <= record["age_seconds"] < 1
                assessed = utc(record["evaluated_at"])
                ages = [(assessed - t).total_seconds() for t in early if t <= assessed]
                used = sum(0 <= age < 60 for age in ages)
                assert (
                    used == record["budget"]["extra_used"] < record["budget"]["extra_limit"] == 12
                )
                prior = max(m for m in metadata if m <= seq)
                response_age = (assessed - utc(metadata[prior]["received_at"])).total_seconds()
                assert record["age_seconds"] <= response_age + 0.001
                proofs.append(
                    {
                        "record": record,
                        "headroom_and_pending_match": True,
                        "pending_sequence": session.pending[1] if session.pending else None,
                        "pending_update_id": session.pending[0].last if session.pending else None,
                        "live_update_id": session.book.last,
                        "wall_budget_count": used,
                        "seconds_since_previous_snapshot_received": response_age,
                        "wall_budget_boundary_distance_seconds": min(
                            (abs(age - 60) for age in ages), default=None
                        ),
                        "prior_snapshot": {
                            k: metadata[prior][k]
                            for k in (
                                "sequence",
                                "epoch",
                                "kind",
                                "request_started_at",
                                "received_at",
                                "source_id",
                            )
                        },
                    }
                )
            if seq in wanted_rows:
                source = row["source_id"]
                if source not in payloads:
                    blob, _ = reader.raw(source)
                    payloads[source] = json.loads(
                        gzip.decompress(blob) if row["kind"] == "update" else blob
                    )
                parity(row, payloads[source])
                raw_rows += 1
                pairs += len(row["bids"]) + len(row["asks"])
                trace.append(
                    {
                        **brief(row),
                        "before": before,
                        "after": view(session),
                        "new_checks": session.checks[checks_before:],
                        "snapshot_request": metadata.get(seq),
                    }
                )
    assert len(proofs) == len(selected)
    for ep, session in sessions.items():
        assert normalized(session.summary()) == report["epochs"][ep]["final"]
    for proof in proofs:
        record = proof["record"]
        proof["later_failures_same_epoch"] = [
            {
                "sequence": f["failure"]["sequence"],
                "type": f["failure_type"],
                "receive_time_minus_evaluation_seconds": (
                    utc(f["failure"]["received_at"]) - utc(record["evaluated_at"])
                ).total_seconds(),
            }
            for f in failures
            if f["epoch"] == record["epoch"] and f["failure"]["sequence"] > record["after_sequence"]
        ]
    value = {
        "collection_id": cid,
        "report_state": report["state"],
        "ok": True,
        "deferred_evaluations": report["refresh_decisions"]["deferred_evaluations"],
        "unique_retained_samples": len(proofs),
        "verified_epochs": sorted(sessions),
        "selected_epoch_summaries_match_live": True,
        "request_contexts_checked": request_checks,
        "raw_trace_rows": raw_rows,
        "raw_trace_price_quantity_pairs": pairs,
        "proofs": proofs,
        "trace": trace,
    }
    save(f"{cid}-decisions.json", value)
    print(
        json_bytes(
            {
                k: value[k]
                for k in (
                    "collection_id",
                    "unique_retained_samples",
                    "verified_epochs",
                    "raw_trace_rows",
                )
            }
        ).decode(),
        flush=True,
    )
    return {k: v for k, v in value.items() if k not in ("proofs", "trace")}


def main():
    os.nice(10)
    spec = importlib.util.spec_from_file_location(
        "raw_parity_helper", OUT.parent / "depth-observation-2026-10-03/analyze_failures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reports = json.loads((OUT / "book_depth-reports.json").read_bytes())
    started, results = now(), []
    try:
        for report in reports:
            if not report["refresh_decisions"]["deferred_evaluations"]:
                continue
            reader = ReadOnlyStore(ROOT)
            try:
                results.append(audit(reader, report, module.raw_parity))
            finally:
                reader.close()
        save(
            "decisions-verification.json",
            {
                "started_at": started,
                "finished_at": now(),
                "ok": True,
                "captures": results,
                "unique_retained_samples": sum(r["unique_retained_samples"] for r in results),
                "deferred_evaluations": sum(r["deferred_evaluations"] for r in results),
                "raw_trace_rows": sum(r["raw_trace_rows"] for r in results),
                "raw_trace_price_quantity_pairs": sum(
                    r["raw_trace_price_quantity_pairs"] for r in results
                ),
                "limits": [
                    "Bounded logs do not preserve all individual deferrals",
                    "Wall-clock receipt gaps are not exact processing times",
                    "No counterfactual REST responses exist for skipped requests",
                ],
            },
        )
    except Exception as exc:
        save(
            "decisions-audit-error.json",
            {"at": now(), "type": type(exc).__name__, "error": str(exc)},
        )
        raise


if __name__ == "__main__":
    main()
