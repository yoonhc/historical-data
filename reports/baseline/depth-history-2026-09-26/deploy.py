"""Install the hash-pinned release and restart only the depth service after validation.

Requires access to the user's systemd bus. Restores old files and starts the old
service if installation or startup fails. Does not alter unit files or timers.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

OUT = Path(__file__).resolve().parent
release = json.loads((OUT / "release.json").read_text())
ROOT, STAGE = Path(release["destination_root"]), Path(release["source_root"])
SERVICE = "marketdata-depth.service"
assert release["service"] == SERVICE and release["full_suite"]["passed"] == 256


def digest(path):
    return sha256(path.read_bytes()).hexdigest() if path.exists() else None


def read_collection(identity=None):
    with sqlite3.connect((ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        sql = "SELECT report FROM event_collections WHERE dataset='book_depth' "
        sql += "AND id=?" if identity else "ORDER BY started_at DESC LIMIT 1"
        return json.loads(db.execute(sql, (identity,) if identity else ()).fetchone()[0])


def control(action):
    subprocess.run(["systemctl", "--user", action, SERVICE], check=True, timeout=125)


def atomic_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(destination.name + ".depth-history-new")
    shutil.copy2(source, staged)
    os.replace(staged, destination)


pilot = json.loads((OUT / "pilot.json").read_text())
assert pilot["capture"]["complete"] and pilot["integrity"]["ok"]
assert json.loads((OUT / "pilot-replay.json").read_text())["replay_matches_live"]
assert json.loads((OUT / "pilot-parity.json").read_text())["raw_to_canonical_equal"]
historical = json.loads((OUT / "historical-cases.json").read_text())
assert len(historical["cases"]) == 6
for name in ("depth.py", "depth_stream.py"):
    assert (
        historical["source_sha256"][name]
        == release["files"][f"packages/marketdata/src/marketdata/{name}"]["new_sha256"]
    )
for name, hashes in release["files"].items():
    assert digest(ROOT / name) == hashes["old_sha256"], f"Destination changed: {name}"
    assert digest(STAGE / name) == hashes["new_sha256"], f"Staged file changed: {name}"
service_file = Path("/home/yoonhc/.config/systemd/user/marketdata-depth.service")
service_hash = digest(service_file)
backup = STAGE / "predeployment"
backup.mkdir(exist_ok=False)
for name, hashes in release["files"].items():
    if hashes["old_sha256"] is not None:
        atomic_copy(ROOT / name, backup / name)
before = read_collection()
control("stop")
stopped = read_collection(before["id"])
try:
    assert stopped["state"] != "running"
    assert stopped["messages"] == stopped["persisted_messages"]
    for name in release["files"]:
        atomic_copy(STAGE / name, ROOT / name)
    control("start")
    deadline = time.monotonic() + 45
    while True:
        current = read_collection()
        checks = [
            c for e in current.get("epochs", []) for c in (e.get("final") or {}).get("checks", [])
        ]
        ready = (
            current["id"] != stopped["id"]
            and current.get("history_policy") == historical["policy"]
            and current["state"] == "running"
            and current.get("synchronized")
            and not current.get("sync_losses")
            and sum(bool(c.get("equal")) for c in checks) >= 2
        )
        if ready:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("New depth service did not reach two successful snapshot checks")
        time.sleep(1)
except BaseException:
    control("stop")
    for name, hashes in release["files"].items():
        if hashes["old_sha256"] is None:
            (ROOT / name).unlink(missing_ok=True)
        else:
            atomic_copy(backup / name, ROOT / name)
    control("start")
    (OUT / "deployment-failed.json").write_text(
        json.dumps({"rolled_back": True, "service": SERVICE}) + "\n"
    )
    raise

statuses = {}
for command in ("depth-status", "aggtrade-status", "maintenance-status"):
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/marketdata"), "--root", str(ROOT), command],
        capture_output=True,
        text=True,
        check=False,
    )
    value = json.loads(proc.stdout)
    statuses[command] = {"exit_code": proc.returncode, "healthy": value["healthy"]}
with sqlite3.connect((ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro", uri=True) as db:
    first_receipt = db.execute(
        "SELECT MIN(a.observed_at) FROM artifacts a JOIN fetches f ON f.artifact_id=a.id "
        "WHERE a.kind='depth_bootstrap' AND json_extract(f.metadata,'$.collection_id')=?",
        (current["id"],),
    ).fetchone()[0]
result = {
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "service": SERVICE,
    "previous_collection": {
        k: stopped.get(k)
        for k in (
            "id",
            "state",
            "last_received_at",
            "messages",
            "persisted_messages",
            "sync_losses",
            "error",
        )
    },
    "new_collection": {
        k: current.get(k)
        for k in (
            "id",
            "state",
            "started_at",
            "updated_at",
            "last_received_at",
            "history_policy",
            "history_limits",
            "messages",
            "persisted_messages",
            "sync_losses",
            "coverage_resets",
            "lagged_snapshots",
            "synchronized",
        )
    },
    "new_first_received_at": first_receipt,
    "restart_receive_gap_seconds": (
        datetime.fromisoformat(first_receipt) - datetime.fromisoformat(stopped["last_received_at"])
    ).total_seconds(),
    "boundary_semantics": "Application receipt timestamp gap; no historical depth repair.",
    "independent_checks_equal": sum(bool(c.get("equal")) for c in checks),
    "service_configuration_unchanged": digest(service_file) == service_hash,
    "installed_hashes_match": all(
        digest(ROOT / name) == hashes["new_sha256"] for name, hashes in release["files"].items()
    ),
    "statuses": statuses,
}
assert result["service_configuration_unchanged"] and result["installed_hashes_match"]
(OUT / "deployment.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
