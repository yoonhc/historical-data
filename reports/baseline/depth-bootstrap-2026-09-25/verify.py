"""Verify a finished capture, including every pre-anchor receipt, without altering old journals."""

import argparse
import gzip
import json
import runpy
from pathlib import Path

from marketdata import depth
from marketdata.domain import json_bytes, now, utc
from marketdata.store import Store

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
workspace = Path(__file__).resolve().parents[3]
store = Store(args.root)
try:
    record = depth.collection_record(store)
    report = json.loads(record["report"])
    assert record["state"] != "running"
    replay = depth.replay(store, report["id"])
    parity = runpy.run_path(str(workspace / "scripts/verify_depth_capture.py"))["verify"](
        store, report["id"], replayed=replay
    )
    rows, sources = [], set()
    for segment in store.db.execute(
        "SELECT * FROM event_segments WHERE collection_id=? ORDER BY first_id", (report["id"],)
    ):
        rows.extend(store._revision_table(segment).to_pylist())
        sources.add(segment["source_id"])
    known = set()
    for source in sources:
        blob, _ = store.raw(source)
        kind = store.db.execute("SELECT kind FROM artifacts WHERE id=?", (source,)).fetchone()[0]
        if kind == "depth_websocket":
            for item in json.loads(gzip.decompress(blob)):
                known.add((item["received_at"], item["message"]))
    prefixes = []
    for artifact in store.db.execute("SELECT * FROM artifacts WHERE kind='depth_bootstrap'"):
        payload = json.loads(gzip.decompress(store.raw(artifact["id"])[0]))
        assert all((r["received_at"], r["message"]) in known for r in payload)
        prefixes.append(
            {"source_id": artifact["id"], "messages": len(payload), "all_journaled": True}
        )
    assert sum(p["messages"] for p in prefixes) == report["bootstrap_messages"]
    epochs = []
    for epoch in report["epochs"]:
        selected = [r for r in rows if r["epoch"] == epoch["epoch"]]
        anchor = next(r for r in selected if r["kind"] == "anchor")
        updates = [r for r in selected if r["kind"] == "update"]
        bridge = next(r for r in updates if r["final_update_id"] >= anchor["final_update_id"])
        metadata = [
            json.loads(r["metadata"])
            for r in store.db.execute(
                "SELECT metadata FROM fetches WHERE artifact_id=?", (anchor["source_id"],)
            )
        ]
        request = next(
            m
            for m in metadata
            if m.get("collection_id") == report["id"] and m.get("sequence") == anchor["sequence"]
        )
        request_started = utc(request["request_started_at"])
        assert updates[0]["received_at"] <= request_started
        strict_bridge = (
            bridge["first_update_id"] <= anchor["final_update_id"] <= bridge["final_update_id"]
        )
        epochs.append(
            {
                "epoch": epoch["epoch"],
                "strict_bridge": strict_bridge,
                "anchor_id": anchor["final_update_id"],
                "bridge_U": bridge["first_update_id"],
                "bridge_u": bridge["final_update_id"],
                "first_received_at": updates[0]["received_at"],
                "anchor_received_at": anchor["received_at"],
                "request_started_at": request_started,
                "first_receive_to_request_seconds": (
                    request_started - updates[0]["received_at"]
                ).total_seconds(),
                "pre_anchor_stale_updates": sum(
                    r["final_update_id"] < anchor["final_update_id"] for r in updates
                ),
            }
        )
    assert all(e["strict_bridge"] for e in epochs)
    integrity = store.validate()
    assert integrity["ok"] and replay["replay_matches_live"] and parity["raw_to_canonical_equal"]
    result = {
        "checked_at": now(),
        "collection_id": report["id"],
        "ok": True,
        "bootstrap_receipts": prefixes,
        "epochs": epochs,
        "integrity": integrity,
    }
    for name, value in (("replay", replay), ("raw-parity", parity), ("receipt-checks", result)):
        (args.output / f"{name}.json").write_bytes(json_bytes(value))
    print(
        json_bytes(
            {
                "ok": True,
                "collection_id": report["id"],
                "epochs": len(epochs),
                "bootstrap_messages": report["bootstrap_messages"],
                "rows": replay["rows"],
                "replay_valid": replay["valid"],
                "verification_equal": replay["verification_equal"],
                "verification_uncomparable": replay["verification_uncomparable"],
                "integrity": integrity,
            }
        ).decode()
    )
finally:
    store.close()
