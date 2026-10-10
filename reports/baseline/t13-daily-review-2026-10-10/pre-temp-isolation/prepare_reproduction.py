"""Restore the pinned baseline into a NEW directory, apply the patch, verify every file.

Does not install dependencies, access the network, or run against an operating store.
"""

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    evidence = Path(__file__).resolve().parent
    manifest = json.loads((evidence / "manifest.json").read_text())
    baseline = json.loads((evidence / "baseline.json").read_text())
    archive, patch = evidence / "source.tar.gz", evidence / "candidate.patch"
    if digest(archive.read_bytes()) != manifest["source_archive_sha256"]:
        raise RuntimeError("Archive hash mismatch")
    if digest(patch.read_bytes()) != manifest["candidate_patch_sha256"]:
        raise RuntimeError("Patch hash mismatch")
    files = manifest["source_sha256"]
    if digest(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()) != manifest["content_manifest_sha256"]:
        raise RuntimeError("Content manifest mismatch")
    target = args.destination.resolve()
    target.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if {m.name for m in members} != {"source/" + name for name in files}:
            raise RuntimeError("Unexpected archive entries")
        for member in members:
            name = member.name.removeprefix("source/")
            if not member.isfile() or Path(name).is_absolute() or ".." in Path(name).parts:
                raise RuntimeError("Unsafe archive entry")
            blob = source.extractfile(member).read()
            if digest(blob) != files[name]:
                raise RuntimeError("Source content mismatch")
            if name in manifest["new_files"]:
                continue
            if digest(blob) != baseline["source_sha256"][name]:
                raise RuntimeError("Baseline differs from copied repository source")
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=target, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=target, check=True)
    for name, expected in files.items():
        if digest((target / name).read_bytes()) != expected:
            raise RuntimeError("Patched source differs: " + name)
    print(json.dumps({"release_id": manifest["release_id"], "destination": str(target),
                      "verified_files": len(files), "patch_applied": True,
                      "next": "uv sync --frozen --no-editable, then installed-package tests"}, indent=2))


if __name__ == "__main__":
    main()
