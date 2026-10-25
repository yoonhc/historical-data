"""Hash-only, resumable installation. No package resolution or operating commands."""

import os
import stat
from pathlib import Path

from vendor import handoff_common as c


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_new(path, raw, mode, hook=lambda stage, path: None):
    """Only our deterministic, unlinked installation temporary can be retried."""
    temporary = path.with_name(".t05-install-" + path.name)
    if temporary.exists() or temporary.is_symlink():
        st = temporary.lstat()
        c.require(
            stat.S_ISREG(st.st_mode)
            and st.st_nlink == 1
            and st.st_uid == os.geteuid()
            and not st.st_mode & 0o022,
            "Unexpected installation temporary",
        )
        temporary.unlink()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as f:
        # A kill during this write leaves only a recognised temporary, never a
        # partially installed final object. Existing final objects are immutable.
        f.write(raw)
        f.flush()
        os.fchmod(f.fileno(), mode)
        os.fsync(f.fileno())
    hook("staged", path)
    c.require(not path.exists() and not path.is_symlink(), "Concurrent installation")
    os.rename(temporary, path)
    fsync_dir(path.parent)
    hook("renamed", path)


def install(destination, entries, identity, *, live=True, hook=lambda stage, path: None):
    destination = Path(destination)
    marker = c.encode({"purpose": "t05-recurring-install-v1", "identity": identity})
    c.require(
        all(
            not Path(n).is_absolute()
            and ".." not in Path(n).parts
            and not any(p.startswith(".t05-install-") for p in Path(n).parts)
            for n in entries
        ),
        "Invalid installation entry",
    )
    for p in (destination.parent, *destination.parent.parents):
        st = p.lstat()
        c.require(stat.S_ISDIR(st.st_mode), "Symlink/non-directory installation parent")
        if live:
            c.require(st.st_uid == 0 and not st.st_mode & 0o022, "Untrusted installation parent")
    # An empty final directory after mkdir is a recognised interruption only
    # under this operation's exclusive lock and exact release destination.
    if not destination.exists():
        destination.mkdir(mode=0o755)
        fsync_dir(destination.parent)
        hook("mkdir", destination)
    c.require(destination.is_dir() and not destination.is_symlink(), "Unexpected installation root")
    st = destination.lstat()
    if live:
        c.require(
            st.st_uid == st.st_gid == 0 and not st.st_mode & 0o022, "Untrusted installation root"
        )
    m = destination / ".install-intent.json"
    if not m.exists():
        c.require(
            all(p.name == ".t05-install-.install-intent.json" for p in destination.iterdir()),
            "Existing installation has no matching intent",
        )
        atomic_new(m, marker, 0o644, hook)
    c.require(not m.is_symlink() and m.read_bytes() == marker, "Installation intent differs")
    ms = m.lstat()
    c.require(
        ms.st_nlink == 1 and stat.S_IMODE(ms.st_mode) == 0o644,
        "Installation intent metadata differs",
    )
    allowed = set(entries) | {".install-intent.json"}
    temporaries = {str(Path(n).with_name(".t05-install-" + Path(n).name)) for n in allowed}
    for p in [destination, *destination.rglob("*")]:
        st = p.lstat()
        if live:
            c.require(
                st.st_uid == st.st_gid == 0
                and (stat.S_ISLNK(st.st_mode) or not st.st_mode & 0o022),
                "Untrusted installed object",
            )
        if p == destination:
            continue
        name = str(p.relative_to(destination))
        if stat.S_ISDIR(st.st_mode):
            c.require(
                any(n.startswith(name + "/") for n in entries), "Unexpected installation directory"
            )
        else:
            c.require(name in allowed or name in temporaries, "Unexpected installation object")
    for name, spec in sorted(entries.items()):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        for p in path.parents:
            if p == destination:
                break
            c.require(p.is_dir() and not p.is_symlink(), "Unsafe entry parent")
        if spec["kind"] == "symlink":
            c.require(
                spec["target"] in ("python", "python3", "lib", "/usr/bin/python3"),
                "Unreviewed link",
            )
            if not path.exists() and not path.is_symlink():
                path.symlink_to(spec["target"])
                fsync_dir(path.parent)
            c.require(
                path.is_symlink() and os.readlink(path) == spec["target"], "Installed link differs"
            )
        else:
            c.require(spec["kind"] == "file", "Unknown entry kind")
            raw = Path(spec["source"]).read_bytes()
            c.require(c.sha(raw) == spec["sha256"], "Installation source changed")
            if not path.exists() and not path.is_symlink():
                atomic_new(path, raw, spec["mode"], hook)
            st = path.lstat()
            c.require(
                stat.S_ISREG(st.st_mode)
                and st.st_nlink == 1
                and stat.S_IMODE(st.st_mode) == spec["mode"]
                and c.sha(path.read_bytes()) == spec["sha256"],
                "Installed file/mode differs",
            )
    # Flush created directory entries as well as individual final files.
    for p in reversed(
        [destination, *[p for p in destination.rglob("*") if p.is_dir() and not p.is_symlink()]]
    ):
        fsync_dir(p)
    return {"objects": len(entries), "path": str(destination), "sealed": True}


def verify(destination, entries, identity, *, live=True):
    """A completed installation is immutable; missing files are not repaired."""
    destination = Path(destination)
    marker = c.encode({"purpose": "t05-recurring-install-v1", "identity": identity})
    expected = set(entries) | {".install-intent.json"}
    objects = set()
    for p in [destination, *destination.rglob("*")]:
        st = p.lstat()
        if live:
            c.require(
                st.st_uid == st.st_gid == 0
                and (stat.S_ISLNK(st.st_mode) or not st.st_mode & 0o022),
                "Installed ownership/permission changed",
            )
        if stat.S_ISDIR(st.st_mode):
            if p != destination:
                name = str(p.relative_to(destination))
                c.require(
                    any(n.startswith(name + "/") for n in entries), "Unexpected completed directory"
                )
            continue
        name = str(p.relative_to(destination))
        objects.add(name)
        c.require(name in expected, "Unexpected completed installation object")
        spec = entries.get(name, {"kind": "file", "sha256": c.sha(marker), "mode": 0o644})
        if spec["kind"] == "symlink":
            c.require(
                stat.S_ISLNK(st.st_mode) and os.readlink(p) == spec["target"],
                "Completed link changed",
            )
        else:
            c.require(
                stat.S_ISREG(st.st_mode)
                and st.st_nlink == 1
                and stat.S_IMODE(st.st_mode) == spec["mode"]
                and c.sha(p.read_bytes()) == spec["sha256"],
                "Completed installation changed",
            )
    c.require(objects == expected, "Completed installation object missing")
    return {"objects": len(entries), "verified": True}
