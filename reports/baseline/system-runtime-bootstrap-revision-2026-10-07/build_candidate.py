"""Freeze a new candidate without changing its parent or operating code."""

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

REPORT = Path(__file__).resolve().parent
PARENT = REPORT.parent / "system-runtime-bootstrap-candidate-2026-10-06"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    source = parser.parse_args().source
    parent = json.loads((PARENT / "release.json").read_text())
    names = set(parent["source_files_sha256"])
    names.update(str(p.relative_to(source)) for p in (source / "integrations/runtime").iterdir() if p.is_file())
    names.add("tests/test_clock_bootstrap_revision.py")
    files = {n: sha((source / n).read_bytes()) for n in sorted(names)}
    identity = sha(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())
    metadata = {
        "schema": 1, "release_id": f"system-bootstrap-20261007-{identity[:16]}",
        "source_identity": identity, "parent_release_id": parent["release_id"],
        "parent_archive_sha256": parent["archive_sha256"],
        "base_git_commit": parent["base_git_commit"], "build": parent["build"],
        "source_files_sha256": files, "operational_applied": False,
    }
    release = REPORT / "release"
    release.mkdir()
    for name in files:
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / name).read_bytes())
    raw = json.dumps(metadata, indent=2, sort_keys=True).encode() + b"\n"
    (release / "candidate.json").write_bytes(raw)
    (source / "candidate.json").write_bytes(raw)
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for name in sorted([*files, "candidate.json"]):
                blob = (release / name).read_bytes()
                item = tarfile.TarInfo(name)
                item.size, item.mode, item.mtime = len(blob), 0o644, 0
                tar.addfile(item, io.BytesIO(blob))
    archive = buffer.getvalue()
    (REPORT / "source.tar.gz").write_bytes(archive)
    (REPORT / "release.json").write_text(json.dumps({**metadata, "archive_sha256": sha(archive),
                                                    "archive_bytes": len(archive)}, indent=2, sort_keys=True) + "\n")
    for path in release.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    release.chmod(0o555)
    print(json.dumps({"release_id": metadata["release_id"], "files": len(files), "archive_sha256": sha(archive)}))


if __name__ == "__main__":
    main()
