"""Check completed retirement and run the unchanged archiver once, manually."""

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
RUN = OUT / "20261004T180904Z-be841c1fd08b493f8afb2e0d5dc2a0be"
READY = OUT.parent / "checkpoint-retention-readiness-2026-10-05"
sys.path.insert(0, str(ROOT))

from marketdata.domain import json_bytes, now, sha256  # noqa: E402

from scripts import archive_backups as archive  # noqa: E402
from scripts import restic_schedule  # noqa: E402
from scripts.checkpoint_code import current_code  # noqa: E402


def read(path):
    return json.loads(path.read_bytes())


def save(name, value):
    with (OUT / name).open("xb") as handle:
        handle.write(json_bytes(value))


def main():
    after = read(RUN / "after.json")
    applied = read(RUN / "apply-receipt.json")
    plan = read(READY / "operational-plan.json")["result"]
    assert after["ok"] and applied["ok"] and after["plan_id"] == plan["plan_id"]
    assert current_code() == plan["code"]
    profile = archive.Profile.load(Path("/home/yoonhc/.config/marketdata/restic-archive.toml"))
    before = archive.load_state(profile)
    observation = restic_schedule.load(profile)
    local = archive.overview(profile)
    assert local["healthy"] and local["pending_count"] == 0
    assert not local["active_operation_record"] and not local["lock_busy"]
    result = archive.execute(profile, "run")
    save("manual-archiver-after-retirement.json", result)
    assert result["ok"] and result["result"]["processed"] == 0
    state = archive.load_state(profile)
    assert state["checkpoints"] == before["checkpoints"]
    assert restic_schedule.load(profile) == observation
    commands = [
        read(p)
        for p in (profile.reports_root / result["run_id"]).glob("*.json")
        if p.name not in {"run.json", "monitor.json"}
    ]
    snapshots = [c for c in commands if c.get("operation") == "snapshots"]
    assert len(snapshots) == 1
    actual = read(Path(snapshots[0]["stdout"]))
    expected = read(READY / "live-snapshots.json")
    assert sorted(actual, key=lambda s: s["id"]) == sorted(expected, key=lambda s: s["id"])
    assert len(actual) == 8
    save("live-snapshots-after.json", actual)
    save("post-archiver-commands.json", commands)
    samples = [json.loads(line) for line in (RUN / "monitor.jsonl").read_text().splitlines()]
    streams = {}
    for name in ("depth", "aggtrade"):
        records = [r["streams"][name] for r in samples if name in r.get("streams", {})]
        streams[name] = {
            "samples": len(records),
            "states": sorted({r["state"] for r in records}),
            "sampled_connection_errors": max(
                (r.get("connection_errors") or 0 for r in records), default=0
            ),
            "error_samples": sum(bool(r.get("error") or r.get("flush_error")) for r in records),
            "max_receive_age_seconds": max(
                (r.get("receive_age_seconds") or 0 for r in records), default=0
            ),
            "session_ids": sorted({r["id"] for r in records}),
        }
    overview = archive.overview(profile)
    assert overview["healthy"] and overview["pending_count"] == 0
    save("post-status.json", overview)
    save(
        "post-check-results.json",
        {
            "at": now(),
            "ok": True,
            "plan_id": plan["plan_id"],
            "code": current_code(),
            "manual_archiver_ok": True,
            "manual_archiver_processed": 0,
            "snapshot_ids_and_metadata_unchanged": True,
            "restic_snapshots": len(actual),
            "archive_checkpoint_records_unchanged": True,
            "observation_unchanged_by_manual_run": True,
            "monitor_samples": len(samples),
            "monitor_errors": [r for r in samples if r.get("monitor_error")],
            "streams": streams,
            "next_scheduled_new_daily_flow": "Pending actual 2026-10-05 daily and its scheduled archive",
            "manifest_source_roots_changed": False,
            "services_restarted": False,
            "forget_or_prune_run": False,
            "evidence_sha256": {
                str(p.relative_to(OUT)): sha256(p.read_bytes())
                for p in sorted(OUT.rglob("*"))
                if p.is_file() and "__pycache__" not in p.parts
            },
        },
    )
    print(
        json_bytes(
            {
                "ok": True,
                "snapshots_unchanged": len(actual),
                "manual_archiver_processed": 0,
                "monitor_samples": len(samples),
                "streams": streams,
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
