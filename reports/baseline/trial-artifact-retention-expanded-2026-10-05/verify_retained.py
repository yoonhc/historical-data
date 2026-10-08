"""Verify only the preserved pre-enrollment 09-26 checkpoint; no restore or mutation."""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from marketdata.backup import verify_backup
from marketdata.domain import json_bytes


def main():
    base = Path(__file__).resolve().parent
    source = Path("/mnt/backup/marketdata-historical/automatic/2026-09-26/d4e81e6b027349cdb2e372da4f47288d")
    output = base / "retained-checkpoint-verification.json"
    assert not output.exists()
    started = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    result = verify_backup(source)
    assert result["ok"]
    assert result["backup_id"] == "e6e2e02c95a0498aa88c6199866cddde05f3a0bba67b81d0566f57289dddfac7"
    value = {
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "wall_seconds": time.monotonic() - start, "result": result,
        "scope": "Preserved original checkpoint physical file hashes, catalog and packed layout; no new restore or live-store logical audit.",
        "previous_attempt": {
            "evidence": "retained-checkpoint-verification-incomplete.json.txt",
            "outcome": "verify_backup returned ok; report JSON serialization then failed on datetime checked_at",
            "error": "TypeError: Object of type datetime is not JSON serializable",
            "remedy": "Use the project's datetime-aware json_bytes and serialize before opening the output",
        },
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "deleted_files": 0,
    }
    raw = json_bytes(value)
    with output.open("xb") as stream:
        stream.write(raw)
        stream.write(b"\n")
    print(json.dumps({"ok": result["ok"], "files": result["files"], "bytes": result["bytes"],
                      "wall_seconds": value["wall_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
