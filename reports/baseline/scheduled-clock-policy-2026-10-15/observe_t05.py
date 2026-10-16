"""Read only the third successor's existing receipts and small explicit bundle."""

import fcntl
import hashlib
import json
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

PARENT = Path("/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7")
sys.path.insert(0, str(PARENT / "packages/marketdata/src"))
from marketdata.checkpoint_evidence import decode_bundle, Graph

HERE = Path(__file__).resolve().parent / "t05-observation"
HERE.mkdir(exist_ok=True)


def h(v):
    return hashlib.sha256(v).hexdigest()


def save(n, v):
    raw = v if isinstance(v, bytes) else (json.dumps(v, indent=2, default=str) + "\n").encode()
    (HERE / n).write_bytes(raw)
    return {"path": n, "sha256": h(raw), "bytes": len(raw)}


p = tomllib.loads(Path("/home/yoonhc/.config/marketdata/restic-archive.toml").read_text())
root = Path(p["state_root"]) / p["expected_repository_id"]
BID = "8dba1987cd50c0b8dff5a7e059e0194eb1925e43ed6a5d7653d32c75e3fe5113"
with (root / "archive.lock").open("rb") as lock:
    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
    s = json.loads((root / p["archive_scope"] / "state.json").read_bytes())
    r = s["checkpoints"][BID]
    check = s["repository_check"]
    sid = r["stored"]["snapshot_id"]
    assert (
        sid in check["snapshot_ids"]
        and check["ok"]
        and check["read_data"]
        and check["receipt"]["returncode"] == 0
    )
    cp = Path(r["entry"]["path"])
    bundle = (cp / "checkpoint-evidence.json").read_bytes()
    assert h((cp / "backup.json").read_bytes()) == r["entry"]["manifest_sha256"]
    assert h(bundle) == r["stored"]["checkpoint_evidence"]["sha256"]
    base = [
        p["restic_binary"],
        "-r",
        p["restic_repository"],
        "--password-file",
        p["restic_password_file"],
        "--no-cache",
        "--no-lock",
    ]
    response = subprocess.run(
        base + ["dump", sid, "/checkpoint-evidence.json"], capture_output=True, timeout=45
    )
    assert response.returncode == 0, response.stderr.decode()
    assert response.stdout == bundle
    saved = save("explicit-snapshot-bundle.json", response.stdout)
    full_check_stdout = save(
        "previous-full-check.stdout", Path(check["receipt"]["stdout"]).read_bytes()
    )
    save("archive-evidence.json", {"checkpoint": r, "repository_check": check})
value = decode_bundle(bundle)
g = Graph(value["bundle"]["objects"], value["bundle"]["scope"])
proof = g.proof(value["bundle"]["identity"])
ancestors = g.ancestors(BID)
assert len(ancestors) == 9
result = {
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "backup_id": BID,
    "snapshot_id": sid,
    "stored_and_fully_checked": True,
    "objects": len(value["object_ids"]),
    "ancestors": sorted(ancestors),
    "completion_proof": proof,
    "snapshot_bundle": saved,
    "previous_full_check_stdout": full_check_stdout,
    "post_baseline_successors": 3,
    "bulk_restore": False,
    "new_backup": False,
    "new_repository_check": False,
    "delete_preview_performed": False,
    "deletion_authority": False,
    "next": "Current-day backup/archive and a new preview/grant are still required; mask remains.",
}
save("result.json", result)
print(
    json.dumps(
        {
            k: result[k]
            for k in (
                "backup_id",
                "snapshot_id",
                "objects",
                "post_baseline_successors",
                "delete_preview_performed",
            )
        }
    )
)
