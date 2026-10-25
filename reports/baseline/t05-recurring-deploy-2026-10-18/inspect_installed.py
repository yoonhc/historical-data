"""UID 1000, read-only inspection. Never calls execute, plan, register or apply."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--release", type=Path, required=True)
    p.add_argument("--settings", type=Path, required=True)
    p.add_argument("--mode", choices=("import", "probe", "unfinished"), required=True)
    a = p.parse_args()
    if os.getuid() != 1000:
        raise RuntimeError("UID 1000 required")
    sys.path[:0] = [str(a.release), str(a.release / "packages/marketdata/src")]
    import marketdata

    from scripts import clock_retirement as safe
    from scripts import retirement_clock as clock

    expected = a.release.resolve()
    for module in (safe, marketdata):
        if not Path(module.__file__).resolve().is_relative_to(expected):
            raise RuntimeError("Import outside sealed release")
    result = {
        "uid": os.getuid(),
        "executable": sys.executable,
        "prefix": sys.prefix,
        "modules": {m.__name__: str(Path(m.__file__).resolve()) for m in (safe, marketdata)},
    }
    if a.mode == "import":
        subprocess.run(
            [
                str(a.release / ".venv/bin/python"),
                "-B",
                "-I",
                str(a.release / "scripts/run_clock_retirement.py"),
                "--help",
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        result["cli_help_passed"] = True
    if a.mode != "import":
        settings = safe.Settings.load(a.settings)
        policy = safe.retirement.Policy.load(settings.retention_policy)
        profile = safe.archive.Profile.load(policy.archive_profile)
        roots = [settings.state_root / "retention", profile.state_file.parent / "working-retention"]
        journals = [
            json.loads(path.read_bytes())
            for root in roots
            for path in (root / "journals").glob("*.json")
        ]
        complete = {
            j["plan_id"]
            for j in journals
            if j["status"] == "complete"
            and all(i.get("phase") == "complete" for i in j["items"].values())
        }
        blocked = [
            {"journal": j["plan_id"], "status": j["status"]}
            for j in journals
            if j["plan_id"] not in complete
        ]
        for path in (settings.state_root / "runs").glob("*/run.json"):
            run = json.loads(path.read_bytes())
            if (
                run.get("action") in ("run", "apply-plan")
                and run.get("deletion_attempted")
                and run.get("plan_id") not in complete
                and not getattr(safe, "safely_deferred_before_application", lambda value: False)(
                    run
                )
            ):
                blocked.append({"run": run["run_id"], "reason": "unfinished_intent"})
        for path in (profile.reports_root / "checkpoint-scheduled").glob("*/run.json"):
            run = json.loads(path.read_bytes())
            if (
                run.get("action") == "run"
                and run.get("state") == "running"
                and run.get("plan_id") not in complete
            ):
                blocked.append({"legacy_run": str(path)})
        result["unfinished"] = blocked
        if a.mode == "probe":
            result["proposal"] = safe.proposal(settings)
            safe.installed_service(settings, "marketdata-checkpoint-retirement.service", "run")
            safe.installed_service(
                settings, "marketdata-checkpoint-retirement-apply@inspection.service", "apply-plan"
            )
            with clock.Host(json.loads(settings.clock_binding_file.read_bytes())).lock() as host:
                before = clock.stamp()
                observation = host.query()
                after = clock.stamp()
                clock.continuous(before, after)
                elapsed = (after["monotonic_ns"] - before["monotonic_ns"]) / 1e9
                if not 0 <= elapsed <= clock.POLICY["max_query_seconds"]:
                    raise RuntimeError("Clock query expired")
                result["clock"] = {
                    **observation,
                    **clock.assess(
                        observation["tracking"],
                        after,
                        query_seconds=elapsed,
                        sampling_margin=(before["span_ns"] + after["span_ns"]) / 1e9,
                    ),
                    "query_before": before,
                    "query_after": after,
                }
            if not result["clock"]["ready"]:
                raise RuntimeError(
                    "Current clock inspection deferred: " + str(result["clock"]["reasons"])
                )
            result["fragment_guards_passed"] = True
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
