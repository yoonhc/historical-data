"""Temporary protected-unit worker. No enabled grant or operating apply call."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--timer", required=True)
    args = parser.parse_args()
    assert os.getuid() == 1000
    assert args.stage.parent == Path("/opt") and args.stage.name.startswith("md-t05-recurring-")
    assert args.state == Path("/var/lib") / args.stage.name
    assert args.service == args.stage.name + ".service" and args.timer == args.stage.name + ".timer"
    release = args.stage / "release"
    sys.path[:0] = [str(release), str(release / "packages/marketdata/src")]
    from marketdata.domain import json_bytes

    from scripts import clock_retirement as safe
    from scripts import retirement_clock as clock

    settings_path = args.stage / "config/settings.toml"
    settings = safe.Settings.load(settings_path)
    assert settings.state_root == args.state / "control"
    assert settings.authorization_file == args.stage / "config/recurring.json"
    grant = json.loads(settings.authorization_file.read_bytes())
    assert (
        grant["enabled"] is False
        and grant["approved_at"] is None
        and grant["approval_reference"] is None
    )
    help_result = subprocess.run(
        [
            release / ".venv/bin/python",
            "-B",
            "-I",
            release / "scripts/run_clock_retirement.py",
            "--help",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    actual = safe.system_context(args.service, args.timer)
    wrong = safe.system_context(
        args.service.removesuffix(".service") + "-wrong.service", args.timer
    )
    wrong_timer = safe.system_context(
        args.service, args.timer.removesuffix(".timer") + "-wrong.timer"
    )
    assert actual["service_verified"] and actual["manager"] == "system" and not wrong["scheduled"]
    assert not wrong_timer["scheduled"]
    safe.installed_service(settings, args.service, "run")
    binding = json.loads(settings.clock_binding_file.read_bytes())
    inv = os.environ["INVOCATION_ID"]
    with clock.Host(binding).lock() as host:
        guard = clock.Guard(host.query, args.state / f"clock-{inv}.jsonl").start()
        try:
            observation = guard.check(fresh=True)
        finally:
            guard.close()
    # Root-owned disabled grant is enforced by the actual runner before any
    # archive, plan, deletion, clock or hard-coded production-service lookup.
    before = settings.authorization_file.read_bytes()
    result = safe.execute(settings, "run")
    assert result["state"] == "deferred" and result["reason"] == "authorization_pending"
    assert not result["deletion_attempted"] and result["deleted_this_run"] == 0
    assert settings.authorization_file.read_bytes() == before
    output = {
        "passed": True,
        "uid": os.getuid(),
        "invocation": inv,
        "actual": actual,
        "wrong_unit": wrong,
        "wrong_timer": wrong_timer,
        "installed_guard_passed": True,
        "clock": observation,
        "disabled_run": result,
        "entrypoint_help": help_result.returncode,
        "module": str(Path(safe.__file__).resolve()),
        "operating_deletion": False,
        "limits": "Actual timer provenance and guard; recurring authority stays disabled. "
        "15:40 eligibility and successful automatic deletion use isolated tests, not this trial.",
    }
    (args.state / f"result-{inv}.json").write_bytes(json_bytes(output))


if __name__ == "__main__":
    main()
