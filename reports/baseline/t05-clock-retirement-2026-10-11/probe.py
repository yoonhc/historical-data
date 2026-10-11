"""Runs only a non-deleting T05 preview inside the temporary system unit."""

import json
import os
import sys
from pathlib import Path

release, config, unit = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path[:0] = [str(release), str(release / "packages/marketdata/src")]
from marketdata.domain import json_bytes  # noqa: E402

from scripts import clock_retirement as safe  # noqa: E402
from scripts import restic_schedule  # noqa: E402
from scripts import retirement_clock as clock  # noqa: E402

settings = safe.Settings.load(config)
context = restic_schedule.service_context(unit, "md-t05-unarmed.timer", manager="system")
if not (
    context.get("service_verified")
    and context.get("start_job")
    and context.get("classification") == "manual_service_or_unknown"
    and not context.get("scheduled")
    and not context.get("provenance_error")
):
    raise RuntimeError("Temporary system invocation could not be proved: " + repr(context))
status = dict(
    line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line
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
    release,
    config.parent,
]
mounts = {str(p): bool(os.statvfs(p).f_flag & os.ST_RDONLY) for p in readonly}
assert all(mounts.values()), mounts
test = settings.state_root / "permission-test.json"
safe.immutable(test, {"scope": "test state only", "uid": os.getuid()})
with clock.Host(json.loads(settings.clock_binding_file.read_text())).lock() as host:
    guard = clock.Guard(host.query, settings.state_root / "access-clock.jsonl").start()
    try:
        observation = guard.check(fresh=True)
    finally:
        guard.close()
# Read-only repository access is checked separately from today's retirement
# prerequisites. No check --read-data, backup, forget, prune or restore is run.
with safe.archive.repository_lock(profile):
    restic = safe.archive.Restic(profile, settings.state_root / "permission-commands")
    restic.identify()
    snapshots = restic.snapshots()
result = safe.execute(settings, "preview")
assert not result["deletion_attempted"] and result["deleted_checkpoints"] == 0
record = {
    "access_passed": True,
    "context": context,
    "readonly_mounts": mounts,
    "uid": os.getuid(),
    "capabilities": {k: status[k].strip() for k in ("CapEff", "CapBnd", "NoNewPrivs")},
    "clock_observation": observation,
    "repository_snapshots_read": len(snapshots),
    "preview": result,
    "preview_completed": result["state"] in {"preview_ready", "no_candidates"},
}
safe.immutable(settings.state_root / "probe-result.json", record)
print(json_bytes(record).decode())
raise SystemExit(result["exit_code"])
