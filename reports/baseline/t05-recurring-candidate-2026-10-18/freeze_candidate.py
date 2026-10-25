"""Freeze a separate T05 candidate; never install or change operating state."""

import difflib
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "t05-completion-evidence-2026-10-11"
WORK = HERE / "work"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    base = json.loads((BASE / "manifest.json").read_bytes())
    installed = Path("/opt/marketdata/releases") / base["release_id"]
    names = sorted(set(base["source_sha256"]) | {"tests/test_recurring_deferral.py"})
    files, changed, patches = {}, [], []
    for name in names:
        raw = (WORK / name).read_bytes()
        files[name] = sha(raw)
        original = b""
        if name in base["source_sha256"]:
            original = (installed / name).read_bytes()
            assert sha(original) == base["source_sha256"][name], name
        if files[name] != base["source_sha256"].get(name):
            changed.append(name)
            patches.extend(
                difflib.unified_diff(
                    original.decode().splitlines(keepends=True),
                    raw.decode().splitlines(keepends=True),
                    fromfile="a/" + name if original else "/dev/null",
                    tofile="b/" + name,
                )
            )
    assert set(changed) == {
        "scripts/clock_retirement.py",
        "scripts/retire_checkpoints.py",
        "tests/test_recurring_deferral.py",
    }
    content = sha(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())
    release = "t05-recurring-retirement-20261018-" + content[:16]
    with (
        (HERE / "source.tar.gz").open("wb") as output,
        gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as stream,
    ):
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name in names:
                raw = (WORK / name).read_bytes()
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(raw), 0o644, 0
                archive.addfile(info, io.BytesIO(raw))
    (HERE / "candidate.patch").write_text("".join(patches))
    result = {
        "release_id": release,
        "base_release": base["release_id"],
        "source_sha256": files,
        "changed_files": changed,
        "runtime_changed_files": [n for n in changed if n.startswith("scripts/")],
        "source_archive_sha256": sha((HERE / "source.tar.gz").read_bytes()),
        "patch_sha256": sha((HERE / "candidate.patch").read_bytes()),
        "content_manifest_sha256": content,
        "freeze_script_sha256": sha(Path(__file__).read_bytes()),
    }
    (HERE / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    # Preserve the reviewed mount correction. Never invoke the old renderer.
    output = HERE / "deployment"
    output.mkdir(exist_ok=True)
    config = Path("/etc/marketdata/checkpoint-clock-retirement")
    hashes = {}
    for name in (
        "marketdata-checkpoint-retirement.service",
        "marketdata-checkpoint-retirement.timer",
        "marketdata-checkpoint-retirement-preview.service",
        "marketdata-checkpoint-retirement-apply@.service",
    ):
        raw = (config / "units" / name).read_text()
        after = raw.replace(
            "/opt/marketdata/releases/" + base["release_id"], "/opt/marketdata/releases/" + release
        )
        if name in (
            "marketdata-checkpoint-retirement.service",
            "marketdata-checkpoint-retirement-apply@.service",
        ):
            assert "ReadWritePaths=/mnt/backup/marketdata-historical\n" in after
            assert "/mnt/backup/marketdata-historical/automatic " not in after
        (output / name).write_text(after)
        hashes[name] = {"before_sha256": sha(raw.encode()), "sha256": sha(after.encode())}
    (HERE / "deployment-files.json").write_text(
        json.dumps(
            {
                "release_id": release,
                "files": hashes,
                "construction": "current canonical files; replace release path only; no renderer",
            },
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            {"release_id": release, "source_files": len(files), "changed_files": changed}, indent=2
        )
    )


if __name__ == "__main__":
    main()
