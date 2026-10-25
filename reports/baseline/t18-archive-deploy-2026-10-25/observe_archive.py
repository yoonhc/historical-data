"""Read current receipts and allocation; never dispatch archive/check/restore work."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REPO = "b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558"
STATE = ROOT / "archive-state" / REPO / "historical-data-original-v1/state.json"
REPORTS = ROOT / "reports/restic-archive-runs"
START = "2026-10-25T07:02:01"


def read(path):
    return json.loads(path.read_bytes())


def ref(path):
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def main():
    current = read(STATE)
    inventory = read(HERE / "current-inventory.json")
    targets = [row for row in inventory["rows"] if row["archive_status"] == "unarchived"]
    assert len(targets) == 6, "Unexpected target contract"
    checked = current.get("repository_check") or {}
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "operating_mutations": False,
              "restic_commands_dispatched": 0, "state_ref": ref(STATE),
              "active_operation": current.get("active_operation"),
              "last_phase_started": current.get("last_phase_started"),
              "last_operation": current.get("last_operation"),
              "repository_check": checked, "targets": [], "runs": [], "errors": []}
    for target in targets:
        item = current["checkpoints"].get(target["backup_id"], {})
        stored = item.get("stored")
        sid = (stored or {}).get("snapshot_id")
        if item:
            assert item["entry"]["manifest_sha256"] == target["manifest_sha256"]
        report["targets"].append({"backup_id": target["backup_id"], "path": target["path"],
            "manifest_sha256": target["manifest_sha256"], "stored": stored,
            "input_verified_at": item.get("input_verified_at"), "error": item.get("error"),
            "attempts": item.get("attempts", []), "reconciliations": item.get("reconciliations", []),
            "included_in_successful_full_check": bool(sid and checked.get("ok") and checked.get("read_data")
                and sid in checked.get("snapshot_ids", []))})
    pool = REPORTS / "manifest-objects-v1"
    bodies = []
    for path in sorted(pool.glob("*/*/*.stdout")):
        st = path.stat()
        bodies.append({"path": str(path), "device": st.st_dev, "inode": st.st_ino,
                       "bytes": st.st_size, "allocated_bytes": st.st_blocks * 512,
                       "nlink": st.st_nlink, "uid": st.st_uid, "gid": st.st_gid,
                       "mode": st.st_mode & 0o777})
    report["body_pool"] = {"objects": bodies, "count": len(bodies),
        "logical_bytes": sum(x["bytes"] for x in bodies),
        "unique_allocated_bytes": sum({(x["device"], x["inode"]): x["allocated_bytes"] for x in bodies}.values())}
    active = current.get("active_operation") or {}
    for path in sorted((REPORTS / "scheduled").glob("*.json")):
        scheduled = read(path)
        if scheduled.get("started_at", "") < START:
            continue
        ctx = scheduled.get("context", {})
        archive = scheduled.get("archive") or {}
        run_id = archive.get("run_id")
        if not run_id and active and active["pid"] == ctx.get("pid"):
            run_id = active["run_id"]
        row = {"scheduled_ref": ref(path), "scheduled": scheduled, "archive_run_id": run_id,
               "commands": [], "shared_stdout_count": 0, "shared_stdout_bytes": 0}
        if run_id:
            folder = REPORTS / run_id
            if (folder / "run.json").exists():
                row["archive_ref"] = ref(folder / "run.json")
                row["archive_result"] = read(folder / "run.json")
            unique = {}
            for cmdpath in sorted(folder.glob("*.json")):
                command = read(cmdpath)
                if "arguments" not in command:
                    continue
                item = {"receipt": ref(cmdpath), "record": command}
                proof = command.get("verified_stdout")
                if proof:
                    out, body = Path(command["stdout"]), Path(proof["body"])
                    a, b = out.stat(), body.stat()
                    same = (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)
                    valid = (same and a.st_size == proof["bytes"] and command["returncode"] == 0
                             and proof["verification"] == "fresh_dump_expected_hash_match"
                             and command["arguments"] == ["dump", proof["snapshot_id"], "/backup.json"])
                    item["shared_inode_verified"] = valid
                    if not valid:
                        report["errors"].append("Shared output differs: " + str(cmdpath))
                    row["shared_stdout_count"] += 1
                    row["shared_stdout_bytes"] += a.st_size
                    unique[(a.st_dev, a.st_ino)] = a.st_blocks * 512
                row["commands"].append(item)
            row["shared_stdout_unique_allocated_bytes"] = sum(unique.values())
            row["run_path_allocated_bytes"] = sum(p.stat().st_blocks * 512 for p in folder.iterdir() if p.is_file())
            row["note"] = "Run path allocation includes shared inodes; it is not additive with body pool allocation."
        report["runs"].append(row)
    request = ROOT / "state/scheduled-clock-policy/restic-archive/request.json"
    report["request_ref"], report["request"] = ref(request), read(request)
    report["collectors"] = {}
    for stream in ("depth", "aggtrade"):
        path = ROOT / "state/runtime-clock/v2" / stream / "latest.json"
        report["collectors"][stream] = {"ref": ref(path), "record": read(path)}
    v = os.statvfs(ROOT)
    report["home_available_bytes"] = v.f_bavail * v.f_frsize
    output = HERE / ("archive-observed-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    with output.open("x") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(output), "active_operation": report["active_operation"],
        "last_phase_started": report["last_phase_started"],
        "stored_targets": sum(bool(x["stored"]) for x in report["targets"]),
        "full_checked_targets": sum(x["included_in_successful_full_check"] for x in report["targets"]),
        "body_count": len(bodies), "body_unique_allocated_bytes": report["body_pool"]["unique_allocated_bytes"],
        "request_state": report["request"]["state"], "home_available_bytes": report["home_available_bytes"],
        "errors": report["errors"]}, indent=2))


if __name__ == "__main__":
    main()
