"""Export the fixed candidate; never change the operating source or services."""

import gzip
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

OUT = Path(__file__).resolve().parent
CANDIDATE = Path("/tmp/marketdata-checkpoint-automation-20261005")
BASE = "9d2d136e7aadd65c38937095daeb33f5211f94bf"


def git(*args):
    return subprocess.check_output(["git", "-C", str(CANDIDATE), *args])


def main():
    assert not git("status", "--porcelain")
    commit = git("rev-parse", "HEAD").decode().strip()
    paths = [
        p
        for p in git("ls-files", "-z").decode().split("\0")
        if p
        and (
            p.startswith(("scripts/", "packages/", "tests/", "configs/", "integrations/systemd/"))
            or p in {"pyproject.toml", "uv.lock", "docs/checkpoint-scheduled-retirement.md"}
        )
    ]
    release = OUT / "release"
    release.mkdir(exist_ok=False)
    hashes = {}
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as bundle:
        for name in sorted(paths):
            raw = git("show", f"{commit}:{name}")
            destination = release / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            destination.chmod(0o444)
            hashes[name] = hashlib.sha256(raw).hexdigest()
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(raw), 0o644, 0
            bundle.addfile(info, io.BytesIO(raw))
    archive = gzip.compress(tar_buffer.getvalue(), mtime=0)
    (OUT / "source.tar.gz").write_bytes(archive)
    patch = git("diff", "--binary", BASE, commit)
    (OUT / "candidate.patch").write_bytes(patch)
    for path in sorted(release.rglob("*"), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
    release.chmod(0o555)
    result = {
        "base_commit": BASE,
        "candidate_commit": commit,
        "release_root": str(release),
        "files": hashes,
        "source_archive_sha256": hashlib.sha256(archive).hexdigest(),
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "release_id": "checkpoint-automation-" + hashlib.sha256(archive).hexdigest()[:16],
        "operating_source_modified": False,
        "timer_installed": False,
        "recurring_deletion_authorized": False,
    }
    (OUT / "release.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "release_id",
                    "candidate_commit",
                    "source_archive_sha256",
                    "patch_sha256",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
