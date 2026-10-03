"""Replay the fixed normal and pending samples; explain the physical rotation boundary."""

import gzip
import json
import os

from analyze_failures import brief, raw_parity
from audit import OUT, ROOT, ReadOnlyStore, save
from marketdata import depth
from marketdata.domain import json_bytes, now

from scripts.verify_depth_capture import verify


def pending_boundary(reader, cid, replayed):
    report = json.loads(depth.collection_record(reader, cid)["report"])
    epochs = [e for e in report["epochs"] if e["final"]["pending_check_sequence"] is not None]
    assert len(epochs) == 1
    epoch = epochs[0]
    seq, ep = epoch["final"]["pending_check_sequence"], epoch["epoch"]
    first_updates, last_updates, last_rows, following = {}, {}, {}, 0
    pending, records = None, {}
    for segment in reader.db.execute(
        "SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id", (cid,)
    ).fetchall():
        cols = ["sequence", "epoch", "kind", "received_at", "final_update_id", "source_id"]
        for row in reader._revision_table(segment, columns=cols).to_pylist():
            records[row["sequence"]] = segment
            last_rows[row["epoch"]] = row
            if row["kind"] == "update":
                first_updates.setdefault(row["epoch"], row)
                last_updates[row["epoch"]] = row
            if row["sequence"] == seq:
                pending = row
            if row["epoch"] == ep and row["sequence"] > seq:
                following += 1
    update = last_updates[ep]
    assert pending["kind"] == "check" and pending == last_rows[ep]
    assert update["final_update_id"] == epoch["final"]["book"]["last_update_id"]
    assert pending["final_update_id"] > update["final_update_id"]
    pairs, detailed = 0, []
    for row in (update, pending):
        full = next(
            r
            for r in reader._revision_table(records[row["sequence"]]).to_pylist()
            if r["sequence"] == row["sequence"]
        )
        blob = reader.raw(full["source_id"])[0]
        payload = json.loads(gzip.decompress(blob) if full["kind"] == "update" else blob)
        raw_parity(full, payload)
        pairs += len(full["bids"]) + len(full["asks"])
        detailed.append(brief(full))
    metadata = next(
        json.loads(r[0])
        for r in reader.db.execute(
            "SELECT metadata FROM fetches WHERE artifact_id=?", (pending["source_id"],)
        )
        if json.loads(r[0]).get("collection_id") == cid and json.loads(r[0]).get("sequence") == seq
    )
    assert epoch["end_reason"] == "rotation"
    assert report["epochs"][ep + 1]["reason"] == "planned_rotation"
    assert replayed["valid"] and replayed["replay_matches_live"]
    assert replayed["verification_pending"] == 1 and not replayed["verification_complete"]
    return {
        "collection_id": cid,
        "epoch": ep,
        "epoch_ended_at": epoch["ended_at"],
        "end_reason": epoch["end_reason"],
        "last_update": detailed[0],
        "pending_snapshot": detailed[1],
        "snapshot_request": metadata,
        "following_rows_in_same_epoch": following,
        "pending_is_final_epoch_row": True,
        "next_epoch": report["epochs"][ep + 1]["epoch"],
        "next_epoch_reason": report["epochs"][ep + 1]["reason"],
        "next_epoch_first_update": first_updates[ep + 1],
        "tail_raw_price_quantity_pairs_equal": pairs,
        "replay_matches_live": True,
        "replay_valid": True,
        "verification_pending": 1,
        "verification_complete": False,
        "interpretation": "The REST check is ahead of the last update when the physical socket rotates; no following update exists in that epoch. Keep it pending and do not splice the next socket into a successful comparison.",
        "live_record_changed": False,
    }


def main():
    os.nice(10)
    selection = json.loads((OUT / "audit.json").read_bytes())
    normal, pending = selection["last_closed_sample_id"], selection["pending_collection_ids"][0]
    reader = ReadOnlyStore(ROOT)
    result = {"started_at": now(), "complete": False}
    try:
        for cid in (normal, pending):
            print("replay " + cid, flush=True)
            replayed = depth.replay(reader, cid)
            save(cid + "-replay.json", replayed)
            if cid == normal:
                print("raw parity " + cid, flush=True)
                parity = verify(reader, cid, replayed=replayed)
                save(cid + "-parity.json", parity)
                assert replayed["valid"] and replayed["replay_matches_live"]
                assert parity["raw_to_canonical_equal"] and parity["all_snapshot_checks_verified"]
                result["normal"] = parity
            else:
                boundary = pending_boundary(reader, cid, replayed)
                save("pending-boundary.json", boundary)
                result["pending"] = boundary
    finally:
        reader.close()
    result.update(complete=True, finished_at=now())
    save("sample-verification.json", result)
    print(
        json_bytes(
            {
                "complete": True,
                "normal": result["normal"],
                "pending_raw_pairs": result["pending"]["tail_raw_price_quantity_pairs_equal"],
            }
        ).decode(),
        flush=True,
    )


if __name__ == "__main__":
    main()
