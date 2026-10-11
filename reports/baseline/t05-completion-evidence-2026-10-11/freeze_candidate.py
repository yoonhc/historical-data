"""Freeze this candidate's source; never install or touch operating services."""
import difflib
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "t05-clock-retirement-2026-10-11"
WORK = HERE / "work"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    base = json.loads((BASE / "manifest.json").read_text())
    names = set(base["source_sha256"])
    for directory in ("scripts", "tests"):
        names.update(p.relative_to(WORK).as_posix() for p in (WORK / directory).rglob("*.py"))
    for source in (WORK / "packages").glob("*/src"):
        names.update(p.relative_to(WORK).as_posix() for p in source.rglob("*.py"))
    files, changed, patches = {}, [], []
    for name in sorted(names):
        raw = (WORK / name).read_bytes()
        files[name] = digest(raw)
        if files[name] != base["source_sha256"].get(name):
            changed.append(name)
            original = (BASE / "work" / name).read_text().splitlines(keepends=True) if name in base["source_sha256"] else []
            patches.extend(difflib.unified_diff(original, raw.decode().splitlines(keepends=True),
                                               fromfile="a/" + name if original else "/dev/null",
                                               tofile="b/" + name))
    content = digest(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())
    release = "t05-completion-evidence-20261011-" + content[:16]
    archive = HERE / "source.tar.gz"
    with archive.open("wb") as output, gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
        with tarfile.open(mode="w", fileobj=compressed) as tar:
            for name in sorted(names):
                raw = (WORK / name).read_bytes()
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(raw), 0o644, 0
                tar.addfile(info, io.BytesIO(raw))
    patch = HERE / "candidate.patch"
    patch.write_text("".join(patches))
    manifest = {
        "release_id": release,
        "base_release": base["release_id"],
        "content_manifest_sha256": content,
        "content_hash_encoding": "JSON sorted keys, separators comma/colon, UTF-8",
        "source_sha256": files,
        "changed_files": changed,
        "source_archive_sha256": digest(archive.read_bytes()),
        "patch_sha256": digest(patch.read_bytes()),
        "freeze_script_sha256": digest(Path(__file__).read_bytes()),
        "design_sha256": json.loads((HERE / "base.json").read_text())["design_sha256"],
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"release_id": release, "source_files": len(files), "changed_files": len(changed)}))


if __name__ == "__main__":
    main()
