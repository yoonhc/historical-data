"""UID 1000 trial only: installed modules + real wrapper/CLI on temporary stores."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

VARIANTS = {"daily": "daily", "backup": "preserve", "restic-archive": "preserve"}
SELECTIONS = {
    "oi": ["test_result_connections.py::test_real_oi_update_commit_and_failure_keep_distinct_request_status[False]"],
    "oi-probe": ["test_storage_recovery.py::test_old_runtime_updates_latest_then_reapply_recovers_original[close-oi-probe]"],
    "oi-quality": ["test_wrapper_recovery.py::test_real_quality_findings_are_terminal_and_argparse_two_is_not",
                   "test_wrapper_recovery.py::test_dead_monitor_never_resumes_payload",
                   "test_wrapper_recovery.py::test_success_observation_journal_failure_never_becomes_time_wait",
                   "test_wrapper_recovery.py::test_real_blocked_monitor_close_is_bounded_and_fatal"],
    "daily": ["test_daily_result_connection.py::test_real_daily_cli_ledger_terminal_and_no_duplicate_http"],
    "pack": ["test_result_connections.py::test_real_pack_final_report_controls_terminal_warning[False]"],
    "backup": ["test_preserve_result_connections.py::test_backup_saved_before_close_failure_recovers_original_day_and_T05_graph"],
    "restic-archive": ["test_preserve_result_connections.py::test_restic_real_schedule_entrypoint_binds_committed_receipt"],
    "network-observer": ["test_storage_recovery.py::test_old_runtime_updates_latest_then_reapply_recovers_original[close-network-observer]"],
}


def save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as f:
        f.write(json.dumps(value, sort_keys=True, indent=2) + "\n")
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def paths(trial, job):
    variant = trial / "candidate" / VARIANTS.get(job, "base")
    # Import marketdata from the installed site-packages, never its source copy.
    sys.path[:0] = [str(variant), str(variant / "tests"), str(trial / "tests")]
    import marketdata
    assert Path(marketdata.__file__).is_relative_to(variant / ".venv")
    return variant, marketdata


def process(trial, state):
    from test_clock_decisions import Samples, provider, load_runner
    from marketdata import job_clock as jc
    from marketdata.store import Store
    runner = load_runner()
    s = Samples()
    runner.query_factory = lambda _: lambda: provider(s)
    original = jc.Context
    jc.Context = lambda *a, **kw: original(*a, sampler=s, monitor_interval=0.05, **kw)
    binding = state / "binding.json"
    if not binding.exists():
        binding.write_text("{}")
    store = state / "store"
    Store(store).close()
    pending = state / "requests/oi-quality/request.json"
    resumed = pending.exists()
    old = json.loads(pending.read_bytes()) if resumed else None
    close = original.close
    if not resumed:
        def fail_close(c):
            close(c)
            raise jc.ClockFailure("trial_close_failure")
        original.close = fail_close
    else:
        def forbidden(*a):
            raise AssertionError("Committed payload must not be repeated")
        runner.invoke = forbidden
    sys.argv = ["trial", "--job", "oi-quality", "--binding", str(binding),
                "--state-root", str(state / "requests"), "--run", "--", "--root", str(store),
                "oi-quality", "--days", "7"]
    error = None
    try:
        code = runner.main()
    except BaseExceptionGroup as exc:
        assert not resumed
        error = str(exc)
        code = 70
    finally:
        original.close = close
    value = json.loads(pending.read_bytes())
    assert value["state"] == ("complete" if resumed else "finalization_failed")
    if resumed:
        assert value["request_id"] == old["request_id"] and value["pins"] == old["pins"]
        assert value["attempt"]["work"]["recovered_committed_result"]
    save(state / ("resumed.json" if resumed else "failed.json"), {
        "uid": os.getuid(), "invocation": os.environ.get("INVOCATION_ID"),
        "request": value, "error": error, "actual_work_exit": value.get("work_exit_code")})
    return 0 if resumed else code


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trial", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--job", choices=list(SELECTIONS), required=True)
    p.add_argument("--mode", choices=("job", "process", "provenance"), default="job")
    p.add_argument("--service")
    p.add_argument("--timer")
    p.add_argument("--local-check", action="store_true")
    a = p.parse_args()
    assert os.getuid() == 1000
    assert a.state.is_dir() and not a.state.is_symlink()
    if not a.local_check:
        assert a.trial.parent == Path("/opt") and a.trial.name.startswith("md-t16-")
        assert a.state.is_relative_to(Path("/var/lib") / a.trial.name)
    variant, mod = paths(a.trial, a.job)
    if a.mode == "process":
        return process(a.trial, a.state)
    if a.mode == "provenance":
        from test_clock_decisions import load_runner
        from scripts import restic_schedule as archive
        runner = load_runner()
        captures = {}
        def capture():
            captures["actual"] = archive.service_context(a.service, a.timer)
            captures["wrong_unit"] = archive.service_context(a.service + "-wrong", a.timer)
            return 0
        archive.main = capture  # no archive execution; original service_context is real
        assert runner.invoke("restic-archive", []) == 0
        index = 1 if (a.state / "context-0.json").exists() else 0
        save(a.state / f"context-{index}.json", captures)
        return 0
    # Read-only common coordinator, no private substitute for the real lock.
    with Path("/var/lib/marketdata-clock/coordinator.lock").open("rb") as f:
        fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
    context = {"uid": os.getuid(), "executable": sys.executable, "prefix": sys.prefix,
               "module": mod.__file__, "job": a.job, "variant": variant.name,
               "invocation": os.environ.get("INVOCATION_ID"), "actual_provider_in_work_tests": False}
    save(a.state / "access.json", context)
    import pytest
    args = [str(a.trial / "tests" / name) for name in SELECTIONS[a.job]]
    code = pytest.main([*args, "-q", "--tb=short", "-p", "no:cacheprovider",
                        "--basetemp=" + str(a.state / "pytest"), "--junitxml=" + str(a.state / "tests.xml")])
    save(a.state / "result.json", {**context, "passed": code == 0, "returncode": int(code)})
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
