"""One-off metadata inventory. Reads candidate trees; writes only report artifacts.

No apply/delete entrypoint. This is not a substitute for a locked pre-delete check.
Data file contents are NOT hashed. Original manifests/receipts are copied exactly.
Run from the repository with its Python; outputs must not already exist.
"""

import gzip
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
ARTIFACTS = ROOT / "reports/trial-artifact-retention-2026-10-05"


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def preserve(path, label):
    raw = path.read_bytes()
    output = ARTIFACTS / (label + ".gz")
    output.write_bytes(gzip.compress(raw, mtime=0))
    assert gzip.decompress(output.read_bytes()) == raw
    return {
        "source": str(path), "copy": str(output.relative_to(ROOT)),
        "bytes": len(raw), "sha256": digest(raw),
        "compressed_bytes": output.stat().st_size,
        "compressed_sha256": digest(output.read_bytes()),
    }


def manifest(path):
    value = json.loads(path.read_bytes())
    content = {k: v for k, v in value.items() if k != "backup_id"}
    assert digest(encoded(content)) == value["backup_id"], path
    files = value["files"]
    assert len({r["path"] for r in files}) == len(files)
    for row in files:
        rel = Path(row["path"])
        assert not rel.is_absolute() and ".." not in rel.parts
    return value


def signature(st):
    return {k: getattr(st, "st_" + k) for k in (
        "dev", "ino", "mode", "uid", "gid", "size", "mtime_ns", "ctime_ns", "nlink", "blocks"
    )}


def scan(root, label, expected, allowed_extra, shared):
    assert root.is_absolute() and root.resolve() == root
    for p in [root, *root.parents]:
        assert not p.is_symlink(), p
    before = root.stat()
    assert stat.S_ISDIR(before.st_mode)
    assert before.st_dev == Path("/mnt/backup").stat().st_dev
    output = ARTIFACTS / (label + ".inventory.jsonl.gz")
    files, directories, singles, directory_blocks = 0, 0, 0, 0
    apparent, allocated_paths = 0, 0
    seen, actual_dirs, extras, size_differences = set(), set(), [], []
    hasher = hashlib.sha256()

    def record(path, st, stream):
        nonlocal files, directories, singles, directory_blocks, apparent, allocated_paths
        assert st.st_dev == before.st_dev, path
        rel = path.relative_to(root).as_posix()
        row = {"path": rel, **signature(st)}
        line = encoded(row) + b"\n"
        hasher.update(line)
        stream.write(line)
        if stat.S_ISDIR(st.st_mode):
            directories += 1
            directory_blocks += st.st_blocks * 512
            actual_dirs.add(rel)
        else:
            assert stat.S_ISREG(st.st_mode), path
            files += 1
            apparent += st.st_size
            allocated_paths += st.st_blocks * 512
            if st.st_nlink == 1:
                singles += st.st_blocks * 512
            else:
                key = (st.st_dev, st.st_ino)
                group = shared.setdefault(key, {"nlink": st.st_nlink, "blocks": st.st_blocks,
                                               "paths": []})
                assert (group["nlink"], group["blocks"]) == (st.st_nlink, st.st_blocks)
                group["paths"].append(str(path))
            seen.add(rel)
            if rel in expected:
                if st.st_size != expected[rel]:
                    size_differences.append({"path": rel, "expected": expected[rel], "actual": st.st_size})
            elif rel not in allowed_extra:
                extras.append(rel)

    def onerror(error):
        raise error

    with output.open("xb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            record(root, before, stream)
            for current, dirs, names, fd in os.fwalk(root, follow_symlinks=False, onerror=onerror):
                dirs.sort()
                names.sort()
                for name in dirs + names:
                    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    record(Path(current) / name, st, stream)
    assert signature(root.stat()) == signature(before), "Root changed during inventory"
    expected_dirs = {"."}
    for rel in seen:
        expected_dirs.update(str(p) for p in Path(rel).parents)
    return {
        "path": str(root), "root": signature(before), "file_paths": files,
        "directory_paths": directories, "apparent_file_bytes": apparent,
        "regular_allocated_path_bytes": allocated_paths,
        "single_link_regular_allocated_bytes": singles,
        "directory_allocated_bytes": directory_blocks,
        "missing_manifest_files": sorted(set(expected) - seen),
        "unexpected_files": extras, "size_differences": size_differences,
        "allowed_extra_files_present": sorted(seen & set(allowed_extra)),
        "extra_empty_directories": sorted(actual_dirs - expected_dirs),
        "inventory": str(output.relative_to(ROOT)), "inventory_sha256": hasher.hexdigest(),
        "inventory_gzip_sha256": digest(output.read_bytes()),
        "inventory_gzip_bytes": output.stat().st_size,
    }


def main():
    started = now()
    assert not (BASE / "inventory.json").exists(), "Use a fresh report location"
    ARTIFACTS.mkdir(mode=0o700, parents=True, exist_ok=False)
    cases = json.loads((BASE / "cases.json").read_bytes())
    paths = [Path(case[role]) for case in cases for role in ("extracted", "store", "new-backup")]
    assert len(paths) == len(set(paths)) == 12
    assert all(not a.is_relative_to(b) for a in paths for b in paths if a != b)
    shared, rows, copies = {}, [], []
    for case in cases:
        original = manifest(Path(case["extracted"]) / "backup.json")
        new = manifest(Path(case["new-backup"]) / "backup.json")
        assert original["backup_id"] == case["backup_id"]
        assert original["source_root"] == str(ROOT)
        assert new["backup_id"] == case["new_backup_id"]
        assert new["source_root"] == case.get("namespace_store", case["store"])
        restored = json.loads((Path(case["store"]) / "restore.json").read_bytes())
        assert restored["ok"] and not restored["services_started"]
        assert restored["backup_id"] == case["backup_id"]
        assert restored["path"] == case.get("namespace_store", case["store"])
        assert restored["source"] == case.get("namespace_extracted", case["extracted"])
        for role in ("extracted", "store", "new-backup"):
            label = case["case"] + "-" + role
            root = Path(case[role])
            meta = "restore.json" if role == "store" else "backup.json"
            copied = preserve(root / meta, label + "-" + meta)
            copies.append(copied)
            records = new["files"] if role == "new-backup" else original["files"]
            expected = {r["path"]: r["bytes"] for r in records}
            extras = ["restore.json", "state/writer.lock", "state/file-layout.lock"] if role == "store" else ["backup.json"]
            row = scan(root, label, expected, extras, shared)
            row.update(case=case["case"], role=role, metadata=copied)
            rows.append(row)
            print(json.dumps({"case": case["case"], "role": role, "files": row["file_paths"],
                              "unexpected": len(row["unexpected_files"]),
                              "missing": len(row["missing_manifest_files"]),
                              "size_differences": row["size_differences"]}), flush=True)
    shared_rows = [{"dev": key[0], "ino": key[1], **group} for key, group in shared.items()]
    assert all(len(g["paths"]) <= g["nlink"] for g in shared_rows)
    outside = [g for g in shared_rows if len(g["paths"]) < g["nlink"]]
    complete = [g for g in shared_rows if len(g["paths"]) == g["nlink"]]
    estimate = sum(r["single_link_regular_allocated_bytes"] + r["directory_allocated_bytes"] for r in rows)
    estimate += sum(g["blocks"] * 512 for g in complete)
    result = {
        "started_at": started, "finished_at": now(), "mode": "metadata_inventory_only",
        "candidates": rows, "preserved_metadata": copies, "shared_inodes": shared_rows,
        "hardlinked_inodes_with_links_outside_candidates": len(outside),
        "external_link_allocated_bytes_excluded": sum(g["blocks"] * 512 for g in outside),
        "estimated_reclaimable_allocated_bytes": estimate,
        "apparent_file_bytes": sum(r["apparent_file_bytes"] for r in rows),
        "file_paths": sum(r["file_paths"] for r in rows),
        "directory_paths": sum(r["directory_paths"] for r in rows),
        "data_content_hashes_rechecked": False, "deleted_files": 0,
        "source_code_sha256": digest(Path(__file__).read_bytes()),
        "cases_sha256": digest((BASE / "cases.json").read_bytes()),
    }
    (BASE / "inventory.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: result[k] for k in ("estimated_reclaimable_allocated_bytes", "file_paths", "deleted_files")}))


if __name__ == "__main__":
    main()
