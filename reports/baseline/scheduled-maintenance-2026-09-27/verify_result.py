"""Check the completed automatic backup's accounting, hard links and sample readback.

The daily job already hashes every file and validates packed members before its
completion marker. This check adds filesystem sharing and selected logical reads;
it does not launch a second backup or rewrite either catalog.
"""

import json
import sqlite3
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from marketdata.backup import CATALOG, load_backup, open_catalog
from marketdata.domain import json_bytes, now, sha256, utc
from marketdata.fileio import packed_record, read_file, read_packed, safe_file

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent


def save(name, value):
    (OUT / name).write_text(json.dumps(json.loads(json_bytes(value)), indent=2) + "\n")


def main():
    daily = json.loads((OUT / "daily-result.json").read_bytes())
    assert daily["state"] == "complete" and daily["ok"] and daily["validation"]["ok"]
    backup, manifest = load_backup(daily["backup"]["path"])
    assert not daily["backup"]["reused"]
    assert manifest["backup_id"] == daily["backup"]["backup_id"]
    storage = manifest["storage"]
    assert storage["mode"] == "linked_snapshot"
    previous = Path(storage["reuse_from"])
    old = json.loads((previous / "backup.json").read_bytes())
    identity = old.pop("backup_id")
    assert sha256(json_bytes(old)) == identity == storage["reuse_backup_id"]
    previous_files = {r["path"]: r for r in old["files"]}
    new_bytes, new_files, reused_bytes, reused_files = Counter(), Counter(), 0, 0
    for record in manifest["files"]:
        relative = record["path"]
        current = (backup / relative).stat()
        assert current.st_size == record["bytes"]
        prior = previous_files.get(relative)
        reusable = relative != CATALOG and prior == record
        if reusable:
            archived = (previous / relative).stat()
            assert (current.st_dev, current.st_ino) == (archived.st_dev, archived.st_ino)
            reused_files += 1
            reused_bytes += record["bytes"]
        else:
            new_files[record["kind"]] += 1
            new_bytes[record["kind"]] += record["bytes"]
    assert sum(new_bytes.values()) == storage["new_bytes"]
    assert sum(new_files.values()) == storage["new_files"]
    assert reused_bytes == storage["reused_bytes"] and reused_files == storage["reused_files"]
    assert not (backup / CATALOG).samefile(previous / CATALOG)
    assert not (backup / CATALOG).samefile(ROOT / CATALOG)

    db = open_catalog(backup / CATALOG)
    live = sqlite3.connect((ROOT / CATALOG).as_uri() + "?mode=ro", uri=True)
    live.row_factory = sqlite3.Row
    samples = []
    try:
        for dataset in ("book_depth", "agg_trades"):
            segments = db.execute(
                "SELECT * FROM event_segments WHERE dataset=? ORDER BY last_time DESC LIMIT 2",
                (dataset,),
            ).fetchall()
            assert len(segments) == 2
            for record in segments:
                packed = packed_record(db, record["path"])
                # The archive is closed and immutable; do not create a live
                # layout lock (or any other runtime file) inside the backup.
                archived = (
                    read_packed(backup, packed)
                    if packed
                    else safe_file(backup, record["path"]).read_bytes()
                )
                current = read_file(ROOT, live, record["path"])
                assert sha256(archived) == record["file_sha"] and archived == current
                table = pq.read_table(pa.BufferReader(archived))
                assert table.num_rows == record["row_count"]
                samples.append(
                    {
                        "dataset": dataset,
                        "path": record["path"],
                        "rows": table.num_rows,
                        "source_equal": True,
                        "sha256": record["file_sha"],
                    }
                )
        probes = []
        for record in live.execute(
            "SELECT f.metadata FROM fetches f JOIN artifacts a ON a.id=f.artifact_id "
            "WHERE a.kind='oi_probe' AND f.observed_at>=? AND f.observed_at<? ORDER BY f.id",
            (
                (utc(daily["started_at"]) - timedelta(minutes=1)).isoformat(),
                (utc(daily["finished_at"]) + timedelta(minutes=1)).isoformat(),
            ),
        ):
            probe = json.loads(record["metadata"])["oi_probe"]
            if utc(daily["started_at"]) <= utc(probe["requested_at"]) < utc(daily["finished_at"]):
                probes.append(probe)
        probes.sort(key=lambda item: item["requested_at"])
        gaps = [
            (utc(b["requested_at"]) - utc(a["requested_at"])).total_seconds()
            for a, b in zip(probes, probes[1:])
        ]
        oi = {
            "observations": len(probes),
            "states": Counter(p["state"] for p in probes),
            "http_statuses": Counter(str(p["status"]) for p in probes),
            "max_request_interval_seconds": max(gaps, default=None),
            "request_intervals_over_45_seconds": sum(gap > 45 for gap in gaps),
        }
    finally:
        live.close()
        db.close()

    observation = json.loads((OUT / "observation.json").read_bytes())
    monitoring = {}
    for stream in ("depth", "aggtrade"):
        selected = [
            s[stream]
            for s in observation["samples"]
            if utc(daily["started_at"]) <= utc(s["at"]) <= utc(daily["finished_at"])
        ]
        assert selected
        ages = [s["receive_age_seconds"] for s in selected if s["receive_age_seconds"] is not None]
        monitoring[stream] = {
            "samples_during_job": len(selected),
            "collection_ids": sorted({s["id"] for s in selected}),
            "states": Counter(s["state"] for s in selected),
            "maximum_reported_receive_age_seconds": max(ages, default=None),
            "samples_without_receive_timestamp": len(selected) - len(ages),
            "maximum_sync_losses": max(s["sync_losses"] or 0 for s in selected),
            "maximum_unrepaired_gaps": max(s["unrepaired_gaps"] or 0 for s in selected),
            "maximum_out_of_order_messages": max(s["out_of_order_messages"] or 0 for s in selected),
            "errors": [s for s in selected if s["error"] or s["flush_error"]],
            "first": selected[0],
            "last": selected[-1],
        }
    save(
        "verification.json",
        {
            "checked_at": now(),
            "backup_id": manifest["backup_id"],
            "backup_path": str(backup),
            "backup_manifest_sha256": sha256((backup / "backup.json").read_bytes()),
            "checkpoint_started_at": manifest["checkpoint_started_at"],
            "checkpoint_finished_at": manifest["checkpoint_finished_at"],
            "writer_lock_seconds": manifest["writer_lock_seconds"],
            "catalog_counts": manifest["catalog_counts"],
            "storage": storage,
            "new_bytes_by_kind": new_bytes,
            "new_files_by_kind": new_files,
            "inode_records_checked": len(manifest["files"]),
            "shared_inodes_verified": reused_files,
            "catalogs_independent": True,
            "sample_readback": samples,
            "collection_monitoring": monitoring,
            "oi_during_job": oi,
            "scope": "Daily job verified all stored bytes; sampled reports do not prove wire-level continuity.",
            "script_sha256": sha256(Path(__file__).read_bytes()),
        },
    )
    print(
        json_bytes(
            {
                "storage": storage,
                "new_bytes_by_kind": new_bytes,
                "writer_lock_seconds": manifest["writer_lock_seconds"],
                "oi": oi,
                "sample_readbacks": len(samples),
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
