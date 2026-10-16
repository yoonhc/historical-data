"""Read-only candidate review: reproduce reported wrapper faults in isolation.

This detects defects in the original candidate; it is not a passing safety test.
The candidate modules and real threads are used without editing them. Provider,
work payload, and (for the finalization probe) close failure are test doubles.
All request/config files are temporary; no service or operating data is accessed.
"""

import contextlib
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
from unittest.mock import patch


REPORT = Path(__file__).resolve().parents[1]
CANDIDATE = REPORT / "candidate"
sys.path.insert(0, str(CANDIDATE / "base/packages/marketdata/src"))
from marketdata import job_clock as jc


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "reviewed_run_job", CANDIDATE / "policy/run_job.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def query():
    return {
        "tracking": {
            "reference_id": "C0000201", "reference_address": "192.0.2.1",
            "stratum": 3, "reference_time": 1_700_000_000.0,
            "correction": .001, "root_delay": .02,
            "root_dispersion": .01, "skew_ppm": 1., "leap": "Normal",
        },
        "boot_id": "isolated-boot", "provider": {"pid": 1},
        "config_sha256": "chrony", "bootstrap_sha256": "bootstrap",
        "runtime_profile_sha256": "runtime", "ledger_sha256": "closed",
    }


def dead_monitor():
    errors, events, reached = [], [], []

    def sampler():
        if threading.current_thread().name == "scheduled-clock":
            raise jc.ClockDeferred("injected_monitor_sample_error")
        return jc.sample()

    def emit(event):
        if event.get("reason") == "monitor":
            raise OSError("injected_monitor_journal_error")
        events.append(event)

    def hook(args):
        errors.append({"type": args.exc_type.__name__, "message": str(args.exc_value)})

    context = jc.Context(query, emit, sampler=sampler)
    runner = load_runner()
    with patch.object(threading, "excepthook", hook):
        try:
            context.start()
            context.thread.join(3)
            assert not context.thread.is_alive(), "Fault injection did not terminate monitor"
            before = {"thread_exists": context.thread is not None,
                      "alive": context.thread.is_alive(), "fault": context.fault}

            def work():
                # Same gate as run_job.main.work, not a repaired lifecycle.
                if context.thread is None:
                    context.start()
                reached.append({"alive": context.thread.is_alive(), "fault": context.fault})
                return 0

            code = runner.run_with_resume(context, work, sleep=lambda _: None, emit=events.append)
            return {"defect_present": bool(reached and not reached[0]["alive"]),
                    "before_resume": before, "work_entries": reached,
                    "result": code, "thread_errors": errors}
        finally:
            context.close()


def point_io_and_unknown():
    rows = {}
    for kind in ("journal_io", "unknown_query_bug"):
        recorded = []

        def emit(event):
            if kind == "journal_io" and event["kind"] == "time_decision":
                raise OSError("injected_success_journal_error")
            recorded.append(event)

        def provider():
            if kind == "unknown_query_bug":
                raise KeyError("injected_unexpected_response_key")
            return query()

        context = jc.Context(provider, emit)
        try:
            context.point("review")
            raise AssertionError("Expected injected failure")
        except jc.ClockDeferred as exc:
            rows[kind] = {"defect_present": True, "classified_as": type(exc).__name__,
                          "cause": type(exc.__cause__).__name__, "recorded": recorded}
    return rows


def call_main(root, job, invoke, *, close_error=False, extra=()):
    runner = load_runner()
    binding = root / "binding.json"
    if not binding.exists():
        binding.write_text("{}\n")
    argv = ["run_job", "--job", job, "--binding", str(binding),
            "--state-root", str(root / "state"), "--run", "--", *extra]
    old_signal, old_path, old_argv = signal.getsignal(signal.SIGTERM), sys.path[:], sys.argv[:]
    original_close = jc.Context.close

    def failed_close(context):
        original_close(context)
        raise jc.ClockDeferred("monitor_stop_timeout")

    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(runner, "query_factory", lambda _: query))
            stack.enter_context(patch.object(runner, "invoke", invoke))
            stack.enter_context(patch.object(sys, "argv", argv))
            if close_error:
                stack.enter_context(patch.object(jc.Context, "close", failed_close))
            try:
                outcome = {"returned": runner.main()}
            except BaseException as exc:
                outcome = {"exception": type(exc).__name__, "message": str(exc)}
    finally:
        signal.signal(signal.SIGTERM, old_signal)
        sys.path[:], sys.argv[:] = old_path, old_argv
    return outcome, json.loads((root / "state" / job / "request.json").read_bytes())


def finalization(root):
    calls = []

    def work(*_):
        calls.append(0)
        return 0

    outcome, request = call_main(root, "backup", work, close_error=True)
    return {"defect_present": request["state"] == "pending" and "exit_code" not in request,
            "work_returns": calls, "outcome": outcome, "disk_request": request,
            "limit": "close timeout injected after a normal real-thread join"}


def config_identity(root):
    config = root / "job.toml"
    config.write_text("scope = 'A'\n")
    entries = []

    def work(*_):
        context = jc.active()
        entries.append({"config": config.read_text(), "pins_at_entry": dict(context.pins)})
        if len(entries) == 1:
            context.pin_day("original_day", context.point("pin"))
            raise RuntimeError("injected_unfinished_work")
        return 0

    first, req1 = call_main(root, "oi", work, extra=("update", "--config", str(config)))
    config.write_text("scope = 'B'\n")
    second, req2 = call_main(root, "oi", work, extra=("update", "--config", str(config)))
    return {"wrapper_allows_changed_config": req1["identity"] == req2["identity"] and len(entries) == 2,
            "first": first, "second": second, "entries": entries,
            "limit": "Wrapper only; Host and business guards are reviewed separately in source"}


def code_two(root):
    entries = []

    def work(*_):
        context = jc.active()
        entries.append(dict(context.pins))
        if len(entries) == 1:
            context.pin_day("backup_day", context.point("pin"))
            return 2
        return 0

    first, req1 = call_main(root, "backup", work)
    second, req2 = call_main(root, "backup", work)
    return {"unconditional_code_two_terminal": req1["state"] == "complete",
            "first": first, "second": second, "first_disk_request": req1,
            "next_work_pins": entries[1], "next_disk_request": req2,
            "limit": "Numeric wrapper behavior; actual job meanings require separate evidence"}


def main():
    manifest = json.loads((REPORT / "manifest.json").read_bytes())
    source_hashes = {
        str(path.relative_to(CANDIDATE)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [CANDIDATE / "policy/job_clock.py", CANDIDATE / "policy/run_job.py",
                     CANDIDATE / "base/packages/marketdata/src/marketdata/job_clock.py"]
    }
    result = {
        "observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "python": sys.version,
        "source_hashes": source_hashes,
        "release_id": manifest.get("release_id", manifest.get("candidate")),
        "purpose": "defect reproduction on unchanged original candidate, not safety validation",
        "operating_services_accessed": False, "operating_data_accessed": False,
        "provider_and_work": "test doubles", "monitor_thread": "real",
    }
    result["dead_monitor_resume"] = dead_monitor()
    result["point_exception_classification"] = point_io_and_unknown()
    with tempfile.TemporaryDirectory(prefix="t16-wrapper-review-") as temp:
        for name, probe in (("close_failure", finalization), ("config_identity", config_identity),
                            ("exit_two", code_two)):
            root = Path(temp) / name
            root.mkdir()
            result[name] = probe(root)
    result["source_hashes_unchanged"] = all(
        hashlib.sha256((CANDIDATE / name).read_bytes()).hexdigest() == expected
        for name, expected in source_hashes.items()
    )
    target = Path(__file__).with_name("probe-results.json")
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"report": str(target), "python": sys.version.split()[0],
                      "dead_monitor_resume": result["dead_monitor_resume"]["defect_present"],
                      "io_reclassified": result["point_exception_classification"]["journal_io"]["defect_present"],
                      "close_skips_final_save": result["close_failure"]["defect_present"],
                      "source_hashes_unchanged": result["source_hashes_unchanged"]}, indent=2))


if __name__ == "__main__":
    main()
