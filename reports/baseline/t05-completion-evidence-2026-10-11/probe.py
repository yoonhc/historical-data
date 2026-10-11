"""Non-deleting preview and read-only inventory in the temporary system unit."""

import json
import os
import sys
import traceback
from pathlib import Path

release, config, unit = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path[:0] = [str(release), str(release / "packages/marketdata/src")]
from marketdata.checkpoint_evidence import immutable  # noqa: E402
from marketdata.domain import json_bytes  # noqa: E402

from scripts import checkpoint_evidence as registration  # noqa: E402
from scripts import clock_retirement as safe  # noqa: E402
from scripts import restic_schedule  # noqa: E402
from scripts import retirement_clock as clock  # noqa: E402

settings = safe.Settings.load(config)
record = {
    "access_passed": False,
    "preview_completed": False,
    "inventory_completed": False,
    "registration_performed": False,
    "deletion_performed": False,
    "phase": "system_context",
}
code = 1
try:
    context = restic_schedule.service_context(unit, "md-t05-unarmed.timer", manager="system")
    if not (
        context.get("service_verified")
        and context.get("start_job")
        and context.get("classification") == "manual_service_or_unknown"
        and not context.get("scheduled")
        and not context.get("provenance_error")
    ):
        raise RuntimeError("Temporary system invocation could not be proved: " + repr(context))
    record["context"] = context
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    assert os.getuid() == 1000 and int(status["CapEff"], 16) == int(status["CapBnd"], 16) == 0
    assert status["NoNewPrivs"].strip() == "1"
    profile = safe.archive.Profile.load(
        safe.retirement.Policy.load(settings.retention_policy).archive_profile
    )
    readonly = [
        profile.backup_dir,
        profile.restic_repository,
        profile.store_root / "data",
        profile.store_root / "state",
        profile.state_root,
        release,
        config.parent,
    ]
    mounts = {str(p): bool(os.statvfs(p).f_flag & os.ST_RDONLY) for p in readonly}
    assert all(mounts.values()), mounts
    record.update(
        readonly_mounts=mounts,
        uid=os.getuid(),
        capabilities={k: status[k].strip() for k in ("CapEff", "CapBnd", "NoNewPrivs")},
    )
    immutable(
        settings.state_root / "permission-test.json",
        {"scope": "test state only", "uid": os.getuid()},
    )
    record["phase"] = "current_clock"
    with clock.Host(json.loads(settings.clock_binding_file.read_text())).lock() as host:
        guard = clock.Guard(host.query, settings.state_root / "access-clock.jsonl").start()
        try:
            record["clock_observation"] = guard.check(fresh=True)
        finally:
            guard.close()
    # Only identify/list; never backup, check --read-data, restore, forget or prune.
    record["phase"] = "repository_access"
    with safe.archive.repository_lock(profile):
        restic = safe.archive.Restic(profile, settings.state_root / "permission-commands")
        restic.identify()
        snapshots = restic.snapshots()
    record.update(access_passed=True, repository_snapshots_read=len(snapshots))
    assert not (settings.state_root / "evidence").exists()
    record["phase"] = "preview"
    result = safe.execute(settings, "preview")
    assert not result["deletion_attempted"] and result["deleted_checkpoints"] == 0
    record.update(
        preview=result, preview_completed=result["state"] in {"preview_ready", "no_candidates"}
    )
    record["phase"] = "readonly_inventory"
    inventory = registration.inventory(settings)
    assert not (settings.state_root / "evidence").exists()
    if record["preview_completed"]:
        plan = result["plan"]
        selected = {x["backup_id"] for x in plan["keep"]} | {
            x["entry"]["backup_id"] for x in plan["candidates"]
        }
        assert selected == {x["backup_id"] for x in inventory["entries"]}
        record["selection"] = {
            "keep": [
                {k: e[k] for k in ("backup_id", "path", "snapshot_id", "protection")}
                for e in plan["keep"]
            ],
            "candidate_count": len(plan["candidates"]),
            "archived_evidence_objects": len(plan["clock_safety"]["objects"]),
        }
    immutable(settings.state_root / "inventory.json", inventory)
    record.update(
        inventory_completed=True,
        inventory_plan_id=inventory["plan_id"],
        inventory_count=len(inventory["entries"]),
        evidence_registry_absent=True,
        phase="finished",
    )
    code = result["exit_code"]
except BaseException as exc:
    record.update(error=repr(exc), traceback=traceback.format_exc())
finally:
    immutable(settings.state_root / "probe-result.json", record)
    print(json_bytes(record).decode())
raise SystemExit(code)
