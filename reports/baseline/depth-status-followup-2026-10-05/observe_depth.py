"""Observe the deployed depth status patch read-only, without exchange requests."""

import gzip
import hashlib
import json
import runpy
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

from marketdata import depth
from marketdata.domain import json_bytes, now

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
FIRST = "2903de655fe94ecc9922e4aa3f529f01"
INTERRUPTED = "665908dfa94b4ee5bfb144e96d953b7b"
PARTIAL = "6a88104dce5345c99dee182bb3bca1c8"
ReadOnlyStore = runpy.run_path(str(OUT.parent / "depth-retry-2026-09-29/check_operating_capture.py"))["ReadOnlyStore"]
sys.path.insert(0, str(ROOT))
from scripts.verify_depth_capture import verify  # noqa: E402


def save(name, value):
    raw = json_bytes(value)
    with (OUT / name).open("xb") as handle:
        handle.write(raw + b"\n")


def catalog():
    captured = now()
    reader = ReadOnlyStore(ROOT)
    try:
        reader.db.execute("BEGIN")
        rows = reader.db.execute(
            "SELECT * FROM event_collections WHERE dataset='book_depth' AND started_at>=? ORDER BY started_at",
            ("2026-10-05T05:01:48.123680+00:00",),
        ).fetchall()
        assert rows[0]["id"] == FIRST
        closed = [r for r in rows if r["state"] != "running"]
        assert len(closed) == 7, "Reassess selection if a new session has closed"
        index = []
        for row in closed:
            report = json.loads(row["report"])
            raw = row["report"].encode()
            (OUT / (row["id"] + ".collection.json.gz")).write_bytes(gzip.compress(raw, mtime=0))
            assert report["state"] == row["state"]
            assert report["messages"] == report["persisted_messages"]
            segments = reader.db.execute(
                "SELECT first_id,last_id,row_count FROM event_segments WHERE collection_id=? ORDER BY first_id",
                (row["id"],),
            ).fetchall()
            expected = count = 0
            for s in segments:
                assert s["first_id"] == expected
                assert s["row_count"] == s["last_id"] - s["first_id"] + 1
                expected = s["last_id"] + 1
                count += s["row_count"]
            assert count == report["messages"] + report["snapshots"]
            checks = [c for e in report["epochs"] for c in e["final"]["checks"]]
            fields = ["id", "state", "complete", "started_at", "updated_at", "messages", "persisted_messages",
                      "snapshots", "connections", "connection_errors", "sync_losses", "coverage_resets",
                      "coverage_top_shortfalls", "lagged_snapshots", "unreconstructed_messages", "error", "errors", "flush_error"]
            index.append({**{k: report.get(k) for k in fields},
                          "segment_count": len(segments), "canonical_rows": count,
                          "segment_metadata_sequence_contiguous": True,
                          "report_sha256": hashlib.sha256(raw).hexdigest(),
                          "rest_checks": len(checks), "rest_equal": sum(c["equal"] is True for c in checks),
                          "rest_uncomparable": sum(not c["comparable"] for c in checks),
                          "rest_mismatches": sum(c["comparable"] and not c["equal"] for c in checks),
                          "rest_pending": sum(e["final"]["pending_check_sequence"] is not None for e in report["epochs"]),
                          "failure_notice": report.get("failure_notice"),
                          "flush_failure_notice": report.get("flush_failure_notice"),
                          "epochs": [{k: e.get(k) for k in ("epoch", "started_at", "ended_at", "reason", "end_reason")} for e in report["epochs"]]})
        running = [json.loads(r["report"]) for r in rows if r["state"] == "running"]
        reader.db.execute("COMMIT")
        save("closed-index.json", index)
        save("running-at-selection.json", running)
    finally:
        reader.close()
    release = json.loads((OUT.parent / "depth-status-release-2026-10-05/release.json").read_bytes())
    hashes = {}
    for changed in release["changed_files"]:
        path = ROOT / changed["path"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == changed["after_sha256"]
        hashes[changed["path"]] = {"sha256": actual, "mtime_ns": path.stat().st_mtime_ns}
    refresh_path = "packages/marketdata/src/marketdata/depth_refresh.py"
    refresh_hash = hashlib.sha256((ROOT / refresh_path).read_bytes()).hexdigest()
    assert refresh_hash == release["final_files_sha256"][refresh_path]
    network = []
    source = ROOT / "reports/network-observer/2026-10-05.jsonl"
    for number, line in enumerate(source.read_bytes().splitlines(), 1):
        r = json.loads(line)
        if "2026-10-05T10:55" <= r["started_at"] <= "2026-10-05T11:06":
            network.append({"source": str(source.relative_to(ROOT)), "line": number,
                            "sha256": hashlib.sha256(line).hexdigest(), "record": r})
    save("network-boundary.json", network)
    numeric = ["messages", "persisted_messages", "snapshots", "canonical_rows", "connection_errors", "sync_losses",
               "coverage_resets", "coverage_top_shortfalls", "unreconstructed_messages", "rest_checks", "rest_equal", "rest_pending"]
    summary = {"captured_at": captured, "release_id": release["release_id"], "source_files": hashes,
               "refresh_policy_sha256": refresh_hash, "closed_sessions": len(index),
               "states": dict(Counter(r["state"] for r in index)),
               "totals": {k: sum(r[k] for r in index) for k in numeric},
               "first_started_at": index[0]["started_at"], "last_closed_at": index[-1]["updated_at"],
               "deep_check_ids": [FIRST, INTERRUPTED, PARTIAL],
               "catalog_mode": "mode=ro honoring live WAL; short transaction for metadata only",
               "services_changed": False, "new_exchange_requests": 0}
    save("catalog-summary.json", summary)
    print(json_bytes({k: summary[k] for k in ("closed_sessions", "states", "totals")}).decode(), flush=True)


def audit():
    index = {r["id"]: r for r in json.loads((OUT / "closed-index.json").read_bytes())}
    outputs = []
    for identity in (FIRST, INTERRUPTED, PARTIAL):
        start = time.monotonic()
        reader = ReadOnlyStore(ROOT)
        try:
            before = depth.collection_record(reader, identity)
            assert hashlib.sha256(before["report"].encode()).hexdigest() == index[identity]["report_sha256"]
            replayed = depth.replay(reader, identity)
            save(identity + ".replay.json", replayed)
            assert replayed["replay_matches_live"]
            assert replayed["valid"] == (identity == FIRST)
            assert replayed["rows"] == index[identity]["canonical_rows"]
            assert replayed["verification_complete"]
            # Full raw parity on the first success and the post-reboot DNS partial.
            parity = None
            if identity != INTERRUPTED:
                parity = verify(reader, identity, replayed=replayed)
                save(identity + ".raw-parity.json", parity)
                assert parity["raw_to_canonical_equal"]
            after = depth.collection_record(reader, identity)
            assert before["report"] == after["report"]
            result = {"collection_id": identity, "checked_at": now(), "elapsed_seconds": time.monotonic() - start,
                      "replay": {k: replayed[k] for k in ("state", "rows", "valid", "continuous", "replay_matches_live",
                                                         "verification_checks", "verification_equal", "verification_complete")},
                      "raw_parity": parity, "historical_report_unchanged": True}
            save(identity + ".audit.json", result)
            outputs.append(result)
            print(json_bytes({"id": identity, "rows": replayed["rows"], "valid": replayed["valid"],
                              "matches": replayed["replay_matches_live"], "raw_checked": parity is not None,
                              "seconds": result["elapsed_seconds"]}).decode(), flush=True)
        finally:
            reader.close()
    save("audit-results.json", outputs)


if __name__ == "__main__":
    assert len(sys.argv) == 2 and sys.argv[1] in {"catalog", "audit"}
    if sys.argv[1] == "catalog":
        catalog()
    else:
        audit()
