"""Small, explicit T18 deployment paths and immutable-file helpers."""

import hashlib
import json
import os
import stat
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path("/home/yoonhc/projects/algoTrade/historical_data")
BASE = Path("/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11")
PYTHON = BASE / "preserve/.venv/bin/python"
PROFILE = "/etc/marketdata/clock-bootstrap.json"
TOPOLOGY = "/etc/marketdata/scheduled-system/topology.json"
BINDING = "/etc/marketdata/scheduled-clock-policy/clock-binding.json"
T05_BINDING = "/etc/marketdata/checkpoint-clock-retirement/clock-binding.json"
GRANT = "/etc/marketdata/checkpoint-clock-retirement/recurring.json"
MANUAL = "/etc/marketdata/checkpoint-clock-retirement/manual.json"
LEDGER = "/var/lib/marketdata-clock/boot-state.json"
SERVICE = "marketdata-restic-archive.service"
TIMER = "marketdata-restic-archive.timer"
T05_SERVICE = "marketdata-checkpoint-retirement.service"
T05_TIMER = "marketdata-checkpoint-retirement.timer"
SERVICE_PATH = "/etc/systemd/system/" + SERVICE
T05 = Path("/opt/marketdata/releases/t05-recurring-retirement-20261018-e57a7995f03fb301")
JOBS = (
    "oi",
    "oi-probe",
    "oi-quality",
    "daily",
    "pack",
    "backup",
    "restic-archive",
    "network-observer",
)
PROCESS_UNITS = (
    "chrony.service",
    "marketdata-collector@depth.service",
    "marketdata-collector@aggtrade.service",
)


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def node(path, *, text=False):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return {"kind": "absent"}
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return {"kind": "symlink", "target": os.readlink(path)}
    require(stat.S_ISREG(info.st_mode), "Expected regular file: " + str(path))
    raw = path.read_bytes()
    result = {"kind": "file", "sha256": sha(raw), "mode": stat.S_IMODE(info.st_mode)}
    if text:
        result["text"] = raw.decode()
    return result


def parse_show(raw):
    result = {}
    for block in raw.strip().split("\n\n"):
        item = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if item:
            require(item["Id"] not in result, "Repeated unit")
            result[item["Id"]] = item
    return result


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
