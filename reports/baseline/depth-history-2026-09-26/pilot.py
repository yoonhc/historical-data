"""Bounded real-source capture and full replay/raw/integrity checks in a separate store."""

import argparse
import json
import runpy
from pathlib import Path

from marketdata import depth, depth_stream
from marketdata.domain import json_bytes, now
from marketdata.store import Store

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--verifier", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
store = Store(args.root)
try:
    report = depth_stream.capture(store, seconds=180, rotate_seconds=30, verify_seconds=10)
    (args.root / "capture.json").write_bytes(json_bytes(report))
    replayed = depth.replay(store, report["id"])
    parity = runpy.run_path(str(args.verifier))["verify"](store, report["id"], replayed=replayed)
    integrity = store.validate()
    result = {
        "checked_at": now(),
        "capture": {
            k: report.get(k)
            for k in (
                "id",
                "started_at",
                "updated_at",
                "state",
                "history_policy",
                "history_limits",
                "connections",
                "connection_errors",
                "sync_losses",
                "coverage_resets",
                "lagged_snapshots",
                "messages",
                "persisted_messages",
                "bootstrap_messages",
                "snapshots",
                "complete",
            )
        },
        "history_check_attempts": sum(
            "history_alignment" in c for e in report["epochs"] for c in e["final"]["checks"]
        ),
        "history_anchor_alignments": sum(
            bool((e["final"].get("anchor_history") or {}).get("aligned")) for e in report["epochs"]
        ),
        "integrity": integrity,
    }
    for name, value in (("pilot", result), ("pilot-replay", replayed), ("pilot-parity", parity)):
        (args.output / f"{name}.json").write_text(
            json.dumps(json.loads(json_bytes(value)), indent=2) + "\n"
        )
    assert report["complete"] and replayed["valid"] and replayed["replay_matches_live"]
    assert parity["raw_to_canonical_equal"] and integrity["ok"]
    print(json_bytes(result).decode())
finally:
    store.close()
