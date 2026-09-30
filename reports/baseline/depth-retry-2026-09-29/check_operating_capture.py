"""Read-only replay and raw parity audit of the first activated depth capture."""

import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from marketdata import depth
from marketdata.domain import json_bytes
from marketdata.store import Store

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scripts.verify_depth_capture import verify  # noqa: E402


class ReadOnlyStore(Store):
    def __init__(self, root):
        self.root = root
        # Honor the live WAL; immutable=1 is inappropriate for this catalog.
        uri = (root / "state/catalog.sqlite").as_uri() + "?mode=ro"
        self.db = sqlite3.connect(uri, uri=True, isolation_level=None)
        self._files_db = sqlite3.connect(uri, uri=True, isolation_level=None)
        self.db.row_factory = self._files_db.row_factory = sqlite3.Row


def save(name, value):
    (OUT / name).write_text(
        json.dumps(json.loads(json_bytes(value)), ensure_ascii=False, indent=2) + "\n"
    )


def main():
    activation = json.loads((OUT / "activation.json").read_bytes())
    release = json.loads((OUT / "release.json").read_bytes())
    assert activation["status"] == "observed"
    identity = activation["new_collection_id"]
    started = datetime.now(timezone.utc)
    reader = ReadOnlyStore(ROOT)
    try:
        # Only the recorded first activated collection is inspected. No new
        # collection is substituted if this one fails or takes too long.
        deadline = time.monotonic() + 120
        while True:
            row = reader.db.execute(
                "SELECT state,report FROM event_collections WHERE id=?", (identity,)
            ).fetchone()
            assert row is not None
            if row["state"] != "running":
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("First activated capture is still running; no complete verdict")
            time.sleep(5)
        report = json.loads(row["report"])
        save("operating-capture-final.json", report)
        replayed = depth.replay(reader, identity)
        save("operating-capture-replay.json", replayed)
        parity = verify(reader, identity, replayed=replayed)
        save("operating-capture-parity.json", parity)
        previous_row = reader.db.execute(
            "SELECT state,report FROM event_collections WHERE id=?",
            (activation["original_collection_id"],),
        ).fetchone()
        previous = json.loads(previous_row["report"])
        protected = {
            p: hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
            for p, h in release["protected_input_hashes_unchanged"].items()
        }
        source_hash = hashlib.sha256(
            (ROOT / "packages/marketdata/src/marketdata/depth_stream.py").read_bytes()
        ).hexdigest()
        latest = json.loads((ROOT / "state/depth-latest.json").read_bytes())
        checked = datetime.now(timezone.utc)
        age = (checked - datetime.fromisoformat(latest["last_received_at"])).total_seconds()
        result = {
            "started_at": started,
            "checked_at": checked,
            "scope": "first completed operating capture replay and raw parity; no 24-hour verdict",
            "collection_id": identity,
            "capture_started_at": report["started_at"],
            "capture_finished_at": report["updated_at"],
            "state": row["state"],
            "policy": report.get("lagged_retry_policy"),
            "messages": report["messages"],
            "persisted_messages": report["persisted_messages"],
            "snapshots": report["snapshots"],
            "connections": report["connections"],
            "connection_errors": report["connection_errors"],
            "sync_losses": report["sync_losses"],
            "unreconstructed_messages": report["unreconstructed_messages"],
            "coverage_resets": report["coverage_resets"],
            "lagged_snapshots": report["lagged_snapshots"],
            "lagged_retry_requests": report["lagged_retry_requests"],
            "previous_state": previous_row["state"],
            "previous_end_reason": previous["epochs"][-1].get("end_reason"),
            "replay": {
                k: replayed[k]
                for k in (
                    "rows",
                    "replay_matches_live",
                    "valid",
                    "continuous",
                    "verification_checks",
                    "verification_equal",
                    "verification_complete",
                    "verification_pending",
                    "verification_uncomparable",
                )
            },
            "raw_parity": parity,
            "source_sha256": source_hash,
            "source_matches_deployment": source_hash == activation["source_sha256"],
            "protected_hash_matches": protected,
            "current_capture": {
                **{
                    k: latest.get(k)
                    for k in (
                        "id",
                        "state",
                        "started_at",
                        "updated_at",
                        "last_received_at",
                        "synchronized",
                        "messages",
                        "persisted_messages",
                        "sync_losses",
                        "lagged_retry_policy",
                        "lagged_retry_requests",
                    )
                },
                "receive_age_seconds": age,
            },
            "forced_restart": False,
            "catalog_open_mode": "ro; live WAL honored",
            "pending": ["real lagged retry occurrence and timing", "24-hour operating quality"],
        }
        save("operating-check.json", result)
        assert report["state"] == "complete" and report["complete"]
        assert replayed["valid"] and replayed["replay_matches_live"]
        assert parity["raw_to_canonical_equal"] and parity["all_snapshot_checks_verified"]
        assert all(protected.values()) and result["source_matches_deployment"]
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "checked_at",
                        "collection_id",
                        "state",
                        "messages",
                        "persisted_messages",
                        "lagged_retry_requests",
                        "replay",
                        "raw_parity",
                        "current_capture",
                    )
                },
                default=str,
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        reader.db.close()
        reader._files_db.close()


if __name__ == "__main__":
    main()
