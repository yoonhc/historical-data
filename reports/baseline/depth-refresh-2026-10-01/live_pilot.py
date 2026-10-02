"""One bounded fresh-store pilot. Existing collectors and catalogs are not opened."""

import hashlib
import json
from pathlib import Path

from marketdata import depth, depth_refresh, depth_stream
from marketdata.domain import json_bytes, now
from marketdata.store import Store

from scripts.verify_depth_capture import verify

ROOT = Path("/home/yoonhc/projects/algoTrade/historical_data")
OUTPUT = Path(__file__).resolve().parent
STORE = ROOT / "reports/depth-refresh-pilot-20261001/store"


def save(name, value):
    (OUTPUT / name).write_bytes(json_bytes(value))


def main():
    assert not STORE.exists(), "Keep every previous trial; use a new root"
    source_files = [Path(module.__file__) for module in (depth, depth_refresh, depth_stream)]
    metadata = {
        "started_at": now(),
        "store_root": str(STORE),
        "seconds": 180,
        "source_hashes": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
    }
    save("pilot-metadata.json", metadata)
    store = Store(STORE)
    try:
        report = depth_stream.capture(
            store,
            seconds=180,
            flush_seconds=5,
            rotate_seconds=90,
            verify_seconds=10,
            top=20,
            min_free_gib=10,
        )
        save("pilot-capture.json", report)
        replay = depth.replay(store, report["id"])
        save("pilot-replay.json", replay)
        verification = verify(store, report["id"], replayed=replay)
        save("pilot-verification.json", verification)
        metadata.update(
            finished_at=now(),
            collection_id=report["id"],
            state=report["state"],
            verification=verification,
        )
        save("pilot-metadata.json", metadata)
        print(
            json.dumps(
                {
                    "state": report["state"],
                    "messages": report["messages"],
                    "coverage_check_requests": report["coverage_check_requests"],
                    "verification": verification,
                },
                default=str,
            ),
            flush=True,
        )
    finally:
        store.close()


if __name__ == "__main__":
    main()
