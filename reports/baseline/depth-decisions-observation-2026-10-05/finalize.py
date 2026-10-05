"""Join recorded proofs and freeze scope/limits without changing operating state."""

import hashlib
import json
from collections import Counter

from audit import OUT, ROOT, ReadOnlyStore, save
from marketdata.domain import now, utc

from scripts.checkpoint_code import current_code


def read(path):
    return json.loads(path.read_bytes())


def main():
    index = read(OUT / "book_depth-index.json")
    reports = {r["id"]: r for r in read(OUT / "book_depth-reports.json")}
    decisions = read(OUT / "decisions-verification.json")
    failures = read(OUT / "failures-verification.json")
    receipts = read(OUT / "receipt-continuity.json")
    pending = read(OUT / "pending-boundary.json")
    assert decisions["ok"] and failures["complete"] and receipts["complete"]
    assert not receipts["same_socket_id_chain_errors"] and not receipts["receive_time_regressions"]
    assert pending["replay_matches_live"] and pending["verification_pending"] == 1
    proofs = [
        {"collection_id": item["collection_id"], **proof}
        for item in decisions["captures"]
        for proof in read(OUT / f"{item['collection_id']}-decisions.json")["proofs"]
    ]
    assert len(proofs) == decisions["unique_retained_samples"] == 67
    assert all("first_headroom_normal" in p for p in proofs)
    with_pending = [p for p in proofs if p["pending_sequence"] is not None]
    assert all(p["pending_resolution"]["check"]["equal"] for p in with_pending)
    relations = []
    for capture in failures["cases"]:
        cid = capture["collection_id"]
        checked = read(OUT / f"{cid}-decisions.json")
        for case in capture["failures"]:
            epoch = case["epoch"]
            log = reports[cid]["epochs"][epoch]["prior_refresh_decisions"]
            trace = [
                r
                for r in checked["trace"]
                if case["failure"]["sequence"] - 4
                <= r["sequence"]
                <= case["failure"]["sequence"] + 3
            ]
            same_epoch_deferrals = [
                p
                for p in checked["proofs"]
                if p["record"]["epoch"] == epoch
                and p["record"]["after_sequence"] < case["failure"]["sequence"]
            ]
            assert all(
                p["first_headroom_normal"]["sequence"] < case["failure"]["sequence"]
                for p in same_epoch_deferrals
            )
            assert case["before"]["pending_check_sequence"] is None
            relations.append(
                {
                    "collection_id": cid,
                    "epoch": epoch,
                    "failure_type": case["failure_type"],
                    "failure": case["failure"],
                    "before": case["before"],
                    "independent_raw_change": case["independent_raw_change"],
                    "seconds_since_last_refresh_response": case[
                        "seconds_since_last_refresh_response"
                    ],
                    "same_socket_recovered_row": case["same_socket_first_synchronized_row"],
                    "receive_gap_to_recovered_row_seconds": case[
                        "failure_receipt_to_recovered_row_seconds"
                    ],
                    "prior_decision_log": log,
                    "same_epoch_retained_deferrals": same_epoch_deferrals,
                    "nearby_trace": trace,
                }
            )
    network = read(OUT / "network-samples.json")
    near = []
    for item in network:
        relevant = item["status"] != "ok" or any(
            abs((utc(item["started_at"]) - utc(case["failure"]["received_at"])).total_seconds())
            < 120
            for case in relations
        )
        if relevant:
            line = (ROOT / item["source"]).read_bytes().splitlines()[item["line"] - 1]
            assert hashlib.sha256(line).hexdigest() == item["sha256"]
            near.append({"reference": item, "original": json.loads(line)})
    save("network-context.json", near)
    first = read(ROOT / "reports/maintenance/2026-10-05/a63f9b4112024dcabcab4546d403f2fa.json")
    retry = read(ROOT / "reports/maintenance/2026-10-05/7e4d5d82fda4430f86fa56144f484665.json")
    journal = [
        json.loads(line) for line in (OUT / "maintenance-journal.jsonl").read_text().splitlines()
    ]
    assert not first["ok"] and first["error"].startswith("WriterBusy:") and retry["ok"]
    assert any("Scheduled restart job" in r["MESSAGE"] for r in journal)
    assert any(r["MESSAGE"].startswith("Finished marketdata-pack.service") for r in journal)
    assert any(r["MESSAGE"].startswith('{"id": "' + retry["id"]) for r in journal)
    save("maintenance-first-attempt.json", first)
    save("maintenance-retry.json", retry)
    reader = ReadOnlyStore(ROOT)
    try:
        for expected in index:
            text = reader.db.execute(
                "SELECT report FROM event_collections WHERE id=?", (expected["id"],)
            ).fetchone()[0]
            assert hashlib.sha256(text.encode()).hexdigest() == expected["catalog_report_sha256"]
        current = {}
        for dataset in ("book_depth", "agg_trades"):
            row = reader.db.execute(
                "SELECT report FROM event_collections WHERE dataset=? AND kind='websocket' ORDER BY started_at DESC LIMIT 1",
                (dataset,),
            ).fetchone()
            report = json.loads(row[0])
            current[dataset] = {
                **{
                    k: report.get(k)
                    for k in (
                        "id",
                        "state",
                        "updated_at",
                        "last_received_at",
                        "connection_errors",
                        "unreconstructed_messages",
                        "unrepaired_gaps",
                    )
                },
                "sampled_at": now(),
                "receive_age_seconds": (now() - utc(report["last_received_at"])).total_seconds(),
            }
        save("current-streams.json", current)
    finally:
        reader.close()
    code = current_code()
    prior = read(OUT.parent / "checkpoint-retirement-apply-2026-10-05/post-check-results.json")
    assert code == prior["code"]
    result = {
        "checked_at": now(),
        "ok": True,
        "all_45_closed_reports_unchanged": True,
        "source_code_unchanged": code,
        "runtime_policy_changed": False,
        "services_manually_restarted": False,
        "new_exchange_requests": 0,
        "total_deferral_evaluations": decisions["deferred_evaluations"],
        "retained_samples_verified": len(proofs),
        "individual_samples_not_retained": decisions["deferred_evaluations"] - len(proofs),
        "retained_primary_reasons": dict(Counter(p["record"]["reason"] for p in proofs)),
        "retained_samples_with_headroom_relieved": len(proofs),
        "retained_pending_resolved_equal": len(with_pending),
        "max_receive_gap_to_headroom_relief_seconds": max(
            (
                utc(p["first_headroom_normal"]["received_at"]) - utc(p["record"]["evaluated_at"])
            ).total_seconds()
            for p in proofs
        ),
        "max_receive_gap_to_pending_resolution_seconds": max(
            (
                utc(p["pending_resolution"]["received_at"]) - utc(p["record"]["evaluated_at"])
            ).total_seconds()
            for p in with_pending
        ),
        "failure_relations": relations,
        "failure_captures_raw_rows": sum(c["rows_checked"] for c in failures["cases"]),
        "failure_captures_raw_price_quantity_pairs": sum(
            c["price_quantity_pairs_checked"] for c in failures["cases"]
        ),
        "deferral_captures": len(decisions["captures"]),
        "selected_epochs_replayed": sum(len(c["verified_epochs"]) for c in decisions["captures"]),
        "selected_epoch_request_contexts_checked": sum(
            c["request_contexts_checked"] for c in decisions["captures"]
        ),
        "canonical_segments_checked": sum(
            c["canonical_segments_hashed"] for c in receipts["sessions"]
        ),
        "network_max_sample_interval_seconds": max(
            (utc(b["started_at"]) - utc(a["started_at"])).total_seconds()
            for a, b in zip(network, network[1:])
        ),
        "maintenance": {
            "first_failed_id": first["id"],
            "retry_success_id": retry["id"],
            "automatic_restart_journal_confirmed": True,
            "lock_owner_identity_was_not_recorded": True,
        },
        "limits": [
            "Aggregate deferral counters are complete for closed reports; individual retained contexts are bounded",
            "Normal 29 no-deferral captures received canonical hash/ID checks, not full raw book replay",
            "All-capture REST counts are recorded-live aggregates; independent replay covers the selected scopes",
            "Receiving the next event is not the exact time comparison processing finished",
            "Minute network probes do not exclude short outages",
            "Two observed failures do not prove another policy would prevent or never prevent failures",
        ],
    }
    save("analysis.json", result)
    print(
        json.dumps(
            {
                "ok": True,
                "samples": len(proofs),
                "pending_resolved": len(with_pending),
                "failure_cases": len(relations),
                "source_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
