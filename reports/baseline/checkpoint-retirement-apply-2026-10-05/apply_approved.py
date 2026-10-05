"""Apply only the user's explicitly approved plan, with bounded observation.

Calls the committed retirement implementation without modifying or bypassing it.
No restic forget/prune, timer changes, service restarts, or source/kit deletion.
"""

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
READY = OUT.parent / "checkpoint-retention-readiness-2026-10-05"
PLAN_ID = "720264ba82bd7a994c0ac3dc73e157b814a7b63042b3a56cb5c96da1b787a200"
sys.path.insert(0, str(ROOT))

from marketdata import backup  # noqa: E402
from marketdata.domain import json_bytes, now, sha256  # noqa: E402

from scripts import archive_backups as archive  # noqa: E402
from scripts import retire_checkpoints as retention  # noqa: E402
from scripts.checkpoint_code import current_code  # noqa: E402
from scripts.restic_trial import sample_streams  # noqa: E402


def read(path):
    return json.loads(path.read_bytes())


def save(directory, name, value):
    with (directory / name).open("xb") as handle:
        handle.write(json_bytes(value))


def manifests(profile):
    return {
        str(p): sha256(p.read_bytes()) for p in sorted(profile.backup_dir.glob("*/*/backup.json"))
    }


def retained_metadata(plan):
    result = {}
    for entry in plan["keep"]:
        path = Path(entry["path"])
        inventory = retention.tree_inventory(path, read(path / "backup.json"))
        result[entry["backup_id"]] = {
            "path": str(path),
            "tree_sha256": sha256(json_bytes(inventory["records"])),
            "records": len(inventory["records"]),
        }
    return result


def reader_preflight(directory):
    spec = importlib.util.spec_from_file_location("readonly_host_probe", READY / "host_readonly.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OUT = directory / "reader-preflight"
    module.OUT.mkdir()
    module.main()
    result = read(module.OUT / "reader-snapshot.json")
    assert not result["checkpoint_parent_locks"], "Checkpoint reader lock currently held"
    assert not any(
        r["checkpoint_references"] or r["cwd_in_checkpoints"]
        for r in result["same_uid_related_processes"]
    ), "Checkpoint reader currently active"
    installed = read(OUT.parent / "checkpoint-retention-guards-2026-10-04/source-install.json")
    old_python = [
        r
        for r in result["same_uid_related_processes"]
        if Path(r["executable"]).name.startswith("python")
        and archive.timestamp(r["started_at"]) < archive.timestamp(installed["finished_at"])
    ]
    assert not old_python, "Old project Python reader requires review"
    denied = result["permission_denied_same_uid_pids"]
    names = []
    if denied:
        text = subprocess.check_output(
            ["ps", "-p", ",".join(map(str, denied)), "-o", "pid=,comm="], text=True, timeout=10
        )
        names = [line.split(None, 1) for line in text.strip().splitlines()]
        assert all(name in {"systemd", "(sd-pam)"} for _, name in names), (
            "Unreviewed inaccessible process"
        )
    save(
        directory,
        "reader-check.json",
        {
            "at": now(),
            "ok": True,
            "restricted_process_names": names,
            "scope": result["scope"],
            "limits": result["limits"],
        },
    )


def monitor(agent, directory, stop):
    while not stop.is_set():
        try:
            path = agent.root / "journals" / f"{PLAN_ID}.json"
            journal = read(path) if path.exists() else None
            item = {
                "at": now(),
                "streams": sample_streams(ROOT),
                "retirement_status": journal["status"] if journal else "pre_delete_checks",
                "phases": dict(Counter(v["phase"] for v in journal["items"].values()))
                if journal
                else {},
                "process_io": Path("/proc/self/io").read_text(),
            }
        except Exception as exc:
            item = {"at": now(), "monitor_error": f"{type(exc).__name__}: {exc}"}
        with (directory / "monitor.jsonl").open("ab") as handle:
            handle.write(json_bytes(item) + b"\n")
        print(
            json_bytes(
                {k: v for k, v in item.items() if k not in {"streams", "process_io"}}
            ).decode(),
            flush=True,
        )
        stop.wait(20)


def main():
    os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
    subprocess.run(["ionice", "-c", "2", "-n", "7", "-p", str(os.getpid())], check=True)
    directory = OUT / (now().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex)
    directory.mkdir()
    save(
        directory,
        "authorization.json",
        {
            "recorded_at": now(),
            "plan_id": PLAN_ID,
            "basis": "User replied 오케이 to the immediately preceding request for manual retirement of this exact five-checkpoint plan.",
            "allowed_action": "manual apply of this plan only",
            "excluded": [
                "source data",
                "other checkpoints",
                "restic forget/prune",
                "trial artifacts",
                "recovery kit",
                "service/timer changes",
            ],
        },
    )
    policy = retention.Policy.load(READY / "approval-policy.toml")
    agent = retention.Retirement(policy, directory / "unused-command-report")
    plan = agent.load_plan(PLAN_ID)
    assert plan == read(READY / "operational-plan.json")["result"]
    assert plan["code"] == current_code() and not current_code()["source_dirty"]
    assert len(plan["candidates"]) == 5 and len(plan["keep"]) == 3
    assert not agent.journals(), "Existing retirement must be inspected before resume"
    before_state = archive.load_state(agent.p)
    overview = archive.overview(agent.p)
    assert overview["healthy"] and overview["pending_count"] == 0
    assert not overview["active_operation_record"] and not overview["lock_busy"]
    before = {
        "at": now(),
        "code": current_code(),
        "manifests": manifests(agent.p),
        "retained_metadata": retained_metadata(plan),
        "archive_state": before_state,
        "space": retention.filesystem_sample(agent.p),
        "streams": sample_streams(ROOT),
    }
    save(directory, "before.json", before)
    reader_preflight(directory)
    stop = threading.Event()
    watcher = threading.Thread(target=monitor, args=(agent, directory, stop), daemon=True)
    watcher.start()
    started = time.monotonic()
    try:
        receipt = retention.execute(policy, "apply", PLAN_ID)
        save(directory, "apply-receipt.json", receipt)
    finally:
        stop.set()
        watcher.join(timeout=30)
    journal = receipt["result"]["journal"]
    assert receipt["ok"] and journal["status"] == "complete"
    assert all(i["phase"] == "complete" for i in journal["items"].values())
    expected = {
        path: digest
        for path, digest in before["manifests"].items()
        if path not in {str(Path(c["entry"]["path"]) / "backup.json") for c in plan["candidates"]}
    }
    after_manifests = manifests(agent.p)
    assert after_manifests == expected
    assert all(not Path(c["entry"]["path"]).exists() for c in plan["candidates"])
    assert retained_metadata(plan) == before["retained_metadata"]
    retained = []
    for entry in plan["keep"]:
        path, manifest = backup.load_backup(entry["path"])
        assert manifest["backup_id"] == entry["backup_id"]
        retained.append(
            {"path": str(path), "backup_id": entry["backup_id"], "catalog_and_manifest_valid": True}
        )
    state = archive.load_state(agent.p)
    assert state["checkpoints"] == before_state["checkpoints"]
    replay = retention.execute(policy, "apply", PLAN_ID)
    save(directory, "already-complete-receipt.json", replay)
    assert replay["ok"] and replay["result"]["already_complete"] is True
    assert replay["result"]["journal"] == journal
    after = {
        "at": now(),
        "ok": True,
        "plan_id": PLAN_ID,
        "wall_seconds": time.monotonic() - started,
        "deleted_checkpoints": len(plan["candidates"]),
        "remaining_manifests": after_manifests,
        "retained_catalog_validation": retained,
        "retained_metadata_unchanged": True,
        "archive_checkpoint_records_unchanged": True,
        "same_plan_replay_already_complete": True,
        "code_unchanged": current_code() == plan["code"],
        "space": retention.filesystem_sample(agent.p),
        "streams": sample_streams(ROOT),
        "next_scheduled_new_daily_flow": "pending",
    }
    save(directory, "after.json", after)
    print(
        json_bytes(
            {
                "ok": True,
                "directory": str(directory),
                "deleted": 5,
                "remaining_checkpoints": len(after_manifests),
                "replay_already_complete": True,
                "space_observation": journal["attempts"][-1]["space_observation"],
                "wall_seconds": after["wall_seconds"],
            }
        ).decode(),
        flush=True,
    )


if __name__ == "__main__":
    main()
