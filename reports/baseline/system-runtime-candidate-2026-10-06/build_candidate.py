"""Freeze the isolated candidate, never the operating editable environment."""

import gzip
import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path

REPORT = Path(__file__).resolve().parent
SOURCE = Path("/tmp/marketdata-system-runtime-20261006/source")


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def main():
    baseline = json.loads((REPORT / "baseline.json").read_text())
    names = set(baseline["files_sha256"])
    names.update(
        str(p.relative_to(SOURCE))
        for p in (SOURCE / "integrations/runtime").iterdir()
        if p.is_file()
    )
    names.add("tests/test_runtime_candidate.py")
    fixture = SOURCE / "reports/baseline/depth-independent-2026-10-05"
    names.update(
        str(p.relative_to(SOURCE))
        for p in fixture.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    files = {name: sha((SOURCE / name).read_bytes()) for name in sorted(names)}
    identity = sha(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())
    meta = {
        "schema": 1,
        "release_id": f"system-runtime-20261006-{identity[:16]}",
        "source_identity": identity,
        "base_git_commit": baseline["git_commit"],
        "source_files_sha256": files,
        "operational_applied": False,
        "build": {
            "python": "3.12.3",
            "uv": "0.12.3",
            "setuptools": "83.0.0",
            "editable": False,
            "lock_sha256": files["uv.lock"],
        },
    }
    release = REPORT / "release"
    release.mkdir()
    for name in files:
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / name, target)
    raw = json.dumps(meta, indent=2, sort_keys=True).encode() + b"\n"
    (release / "candidate.json").write_bytes(raw)
    (SOURCE / "candidate.json").write_bytes(raw)
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for name in sorted([*files, "candidate.json"]):
                blob = (release / name).read_bytes()
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode, entry.mtime = len(blob), 0o644, 0
                tar.addfile(entry, io.BytesIO(blob))
    archive = buffer.getvalue()
    (REPORT / "source.tar.gz").write_bytes(archive)
    (REPORT / "release.json").write_text(
        json.dumps(
            {**meta, "archive_sha256": sha(archive), "archive_bytes": len(archive)},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    for path in release.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    release.chmod(0o555)
    print(
        json.dumps(
            {
                "release_id": meta["release_id"],
                "files": len(files),
                "archive_bytes": len(archive),
                "archive_sha256": sha(archive),
            }
        )
    )


if __name__ == "__main__":
    main()
