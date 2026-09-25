"""Compare restored readers and archive inode isolation without changing archived data."""

import json
import os
import shutil
from datetime import timedelta
from pathlib import Path

from marketdata.domain import Key, json_bytes, now, sha256, utc
from marketdata.store import Store

root = Path.cwd()
output = root / "reports/incremental-backup-2026-09-25"
first = Path("/mnt/backup/marketdata-historical/automatic/2026-09-25/initial")
second = first.parent / "linked"
restored = root / "restore-checks/incremental-2026-09-25"
manifest = json.loads((second / "backup.json").read_bytes())
assert (restored / "restore.json").is_file()
previous = {r["path"]: r for r in json.loads((first / "backup.json").read_bytes())["files"]}
shared, checked, unique, allocated, all_paths = 0, 0, set(), 0, 0
for record in manifest["files"]:
    relative = record["path"]
    target = second / relative
    assert not target.samefile(restored / relative)
    if (root / relative).exists():
        assert not target.samefile(root / relative)
    if relative in previous and record["kind"] != "catalog":
        assert target.samefile(first / relative)
        shared += 1
    checked += 1
assert shared == manifest["storage"]["reused_files"]
assert not (first / "state/catalog.sqlite").samefile(second / "state/catalog.sqlite")
for directory in (first, second):
    for file in directory.rglob("*"):
        if file.is_file():
            stat = file.stat()
            all_paths += 1
            identity = (stat.st_dev, stat.st_ino)
            if identity not in unique:
                allocated += stat.st_blocks * 512
                unique.add(identity)

source = Store(root)
target = Store(restored)
reads, segments, raw = [], [], []
try:
    snapshots = list(target.db.execute("SELECT id FROM snapshots ORDER BY id"))
    for row in snapshots:
        identity = row["id"]
        spec = target.snapshot_manifest(identity)
        assert spec == source.snapshot_manifest(identity)
        start = utc(spec["start"])
        end = min(utc(spec["end"]), start + timedelta(days=1))
        for definition in spec["keys"].values():
            key = Key(**definition)
            expected = source.read(key, start, end, identity)
            actual = target.read(key, start, end, identity)
            assert expected.equals(actual)
            reads.append(
                {
                    "snapshot_id": identity,
                    "key": key.id,
                    "start": start,
                    "end": end,
                    "rows": len(actual),
                    "content_sha256": sha256(json_bytes(actual.to_pylist())),
                    "equal": True,
                }
            )
    for dataset_row in target.db.execute("SELECT DISTINCT dataset FROM event_segments"):
        dataset = dataset_row[0]
        for ordering in ("ASC", "DESC"):
            record = target.db.execute(
                f"SELECT * FROM event_segments WHERE dataset=? ORDER BY first_time {ordering} LIMIT 1",
                (dataset,),
            ).fetchone()
            expected = source._revision_table(record)
            actual = target._revision_table(record)
            assert expected.equals(actual)
            segments.append(
                {
                    "dataset": dataset,
                    "path": record["path"],
                    "rows": len(actual),
                    "content_sha256": sha256(json_bytes(actual.to_pylist())),
                    "equal": True,
                }
            )
    for ordering in ("ASC", "DESC"):
        for row in target.db.execute(f"SELECT id FROM artifacts ORDER BY path {ordering} LIMIT 5"):
            expected, actual = source.raw(row[0]), target.raw(row[0])
            assert expected == actual
            raw.append({"id": row[0], "equal": True})
finally:
    source.close()
    target.close()
result = {
    "checked_at": now(),
    "ok": True,
    "backup_id": manifest["backup_id"],
    "inode_checks": checked,
    "shared_between_backups": shared,
    "source_and_restore_independent": True,
    "catalogs_independent": True,
    "archive_storage": {
        "file_paths": all_paths,
        "unique_inodes": len(unique),
        "unique_allocated_bytes": allocated,
        "includes_manifests_excludes_directory_blocks": True,
    },
    "destination_filesystem": {
        "device_id": os.stat(second).st_dev,
        "source_device_id": os.stat(root).st_dev,
        "free_bytes": shutil.disk_usage(second).free,
    },
    "snapshots": len(snapshots),
    "snapshot_reads": reads,
    "event_segment_reads": segments,
    "raw_reads": raw,
}
(output / "readback.json").write_bytes(json_bytes(result))
print(
    json_bytes(
        {
            "ok": True,
            "snapshots": len(snapshots),
            "snapshot_reads": len(reads),
            "event_segments": len(segments),
            "raw_reads": len(raw),
            "inode_checks": checked,
            "archive_storage": result["archive_storage"],
        }
    ).decode()
)
