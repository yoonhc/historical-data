"""Resume a hash-sealed installation; no resolver/network and no operating units."""
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
import handoff_common as c


def install(destination, entries, *, identity, live=True):
    destination = Path(destination)
    marker = {"purpose": "t16-sealed-install-v1", "identity": identity}
    if destination.exists() or destination.is_symlink():
        c.require(destination.is_dir() and not destination.is_symlink(), "Installation is not an ordinary directory")
        intent = destination / ".install-intent.json"
        c.require(intent.is_file() and not intent.is_symlink() and json.loads(intent.read_bytes()) == marker,
                  "Unrecognised existing installation")
    else:
        for parent in (destination.parent, *destination.parent.parents):
            c.require(not parent.is_symlink(), "Symlink installation parent")
            if live:
                meta = c.metadata(parent)
                c.require(meta["kind"] == "directory" and meta["uid"] == 0 and not meta["mode"] & 0o022,
                          "Untrusted installation parent")
        destination.mkdir(mode=0o755)
        _create(destination / ".install-intent.json", c.encode(marker), 0o644)
    if live:
        meta = c.metadata(destination)
        c.require(meta["uid"] == 0 and not meta["mode"] & 0o022, "Untrusted installation")
    for p in destination.rglob("*"):
        name = str(p.relative_to(destination))
        if p.is_dir() and not p.is_symlink():
            c.require(any(k.startswith(name + "/") for k in entries), "Unexpected installation directory")
        else:
            c.require(name in entries or name == ".install-intent.json", "Unexpected installation object")
        if live:
            meta = c.metadata(p)
            c.require(meta["uid"] == 0 and (meta["kind"] == "symlink" or not meta["mode"] & 0o022),
                      "Untrusted installation object")
    for name, spec in sorted(entries.items()):
        c.require(not Path(name).is_absolute() and ".." not in Path(name).parts, "Unsafe installation entry")
        p = destination / name
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if spec["kind"] == "symlink":
            c.require(spec["target"] in ("python", "python3", "lib", "/usr/bin/python3"), "Unexpected interpreter link")
            if not p.exists() and not p.is_symlink():
                p.symlink_to(spec["target"])
            c.require(p.is_symlink() and os.readlink(p) == spec["target"], "Installed link differs")
            continue
        raw = Path(spec["source"]).read_bytes()
        c.require(c.sha(raw) == spec["sha256"], "Source changed during installation")
        if not p.exists() and not p.is_symlink():
            _create(p, raw, spec["mode"])
        c.require(not p.is_symlink() and c.sha(p.read_bytes()) == spec["sha256"]
                  and stat.S_IMODE(p.stat().st_mode) == spec["mode"], "Installed file/mode differs")
    for p in [x for x in destination.rglob("*") if x.is_dir() and not x.is_symlink()] + [destination, destination.parent]:
        fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return {"objects": len(entries), "path": str(destination), "sealed": True}


def _create(path, raw, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fchmod(f.fileno(), mode)
        os.fsync(f.fileno())
