"""Candidate entrypoint: explicit pinned variants and resumable time deferrals.

No install/service/clock/delete operations. Work executes only with --run; --check
is read-only. Production activation requires a separately reviewed unit transition.
"""

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import signal
import sys
import time
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = {
    "oi": "base",
    "oi-probe": "base",
    "oi-quality": "base",
    "pack": "base",
    "network-observer": "base",
    "daily": "daily",
    "backup": "preserve",
    "restic-archive": "preserve",
}


def encode(value):
    return json.dumps(value, sort_keys=True, default=lambda v: v.isoformat()).encode() + b"\n"


def atomic(path, value):
    temp = path.with_suffix(".tmp")
    with temp.open("wb") as f:
        f.write(encode(value))
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_contract():
    spec = importlib.util.spec_from_file_location(
        "scheduled_request_contract", ROOT / "policy/request_contract.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def observation_query(mod, binding):
    from marketdata import job_clock as jc

    def query():
        try:
            with mod.Host(binding).lock() as host:
                return host.query()
        except subprocess.TimeoutExpired as exc:
            raise jc.ClockDeferred("provider_query_timeout") from exc
        except mod.ClockHold as exc:
            # The unchanged, hash-pinned T05 module has a legacy untyped hold.
            # Only these documented query/readiness cases enter the retry loop.
            expected = {
                "Clock bootstrap/configuration transition in progress",
                "Current boot preparation is not proven closed",
                "Chrony has no active invocation",
                "Chrony has no PID",
                "Chrony restarted during observation",
                "Boot preparation changed",
                "Malformed Chrony 4.5 tracking CSV",
                "Invalid Chrony fields",
            }
            if str(exc) in expected or str(exc).startswith("Read-only query failed: "):
                raise jc.ClockDeferred(str(exc)) from exc
            raise  # trust/configuration errors and unknown holds remain failures

    return query


def query_factory(binding_file):
    binding = json.loads(binding_file.read_bytes())
    source = ROOT / "preserve/packages/marketdata/src/marketdata/checkpoint_clock.py"
    expected = json.loads((ROOT / "parents.json").read_text())["files"][
        "preserve/packages/marketdata/src/marketdata/checkpoint_clock.py"
    ]["sha256"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise RuntimeError("T05 observation identity source changed")
    spec = importlib.util.spec_from_file_location("unchanged_t05_identity", source)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return observation_query(mod, binding)


def invoke(job, args):
    sys.argv = [job, *args]
    if job in ("oi", "oi-probe", "oi-quality", "pack"):
        from marketdata.cli import main

        return main()
    if job == "daily":
        from marketdata.daily_schedule import main

        return main()
    if job == "backup":
        from scripts.checkpoint_evidence import main

        return main()
    if job == "restic-archive":
        from scripts import restic_schedule

        original = restic_schedule.service_context
        try:

            def system_context(*a, **k):
                value = original(*a, manager="system", **k)
                value["manager_adapter"] = {
                    "path": str(Path(__file__).resolve()),
                    "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "parent": str(Path(restic_schedule.__file__).resolve()),
                    "purpose": "scheduled-policy-system-provenance-v1",
                }
                return value

            restic_schedule.service_context = system_context
            return restic_schedule.main()
        finally:
            restic_schedule.service_context = original
    runpy.run_path(str(ROOT / "base/scripts/network_observer.py"), run_name="__main__")
    return 0


def run_with_resume(context, work, sleep=time.sleep, emit=print):
    """Only a time deferral retries here, after the workload has released its locks."""
    from marketdata.job_clock import ClockDeferred

    while True:
        try:
            context.point("resume_before_work")
            return work()
        except ClockDeferred as exc:
            if context.result is not None:
                from marketdata.job_clock import ClockFailure

                raise ClockFailure(
                    "Time deferral after a committed result; reconcile on restart"
                ) from exc
            emit({"kind": "waiting_for_job_time", "reason": str(exc)})
            sleep(5)
            context.restart_observation()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", choices=VARIANTS, required=True)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--state-root", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--wait", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("work_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    variant = ROOT / VARIANTS[args.job]
    sys.path[:0] = [str(variant / "packages/marketdata/src"), str(variant)]
    from marketdata import job_clock as jc

    query = query_factory(args.binding)
    if args.check or args.wait:
        c = jc.Context(query, lambda event: None)
        point = c.wait_point("entry_wait") if args.wait else c.point("entry_check")
        print(encode({"ready": True, "clock": point.evidence}).decode(), end="")
        return 0
    if args.state_root is None or not args.state_root.is_absolute():
        parser.error("--run requires an absolute dedicated state-root")
    contract = load_contract()
    work_args = args.work_args[1:] if args.work_args[:1] == ["--"] else args.work_args
    state = args.state_root / args.job
    state.mkdir(parents=True, exist_ok=True)
    with (state / "request.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        scope = contract.inputs(args.job, work_args, args.binding, ROOT)
        identity = contract.digest(contract.encode(scope))
        pending = state / "request.json"
        previous = json.loads(pending.read_bytes()) if pending.exists() else None
        if previous and previous.get("schema") != contract.VERSION:
            raise contract.RequestError(
                "Legacy request requires explicit reconciliation; not overwritten"
            )
        if previous and previous["state"] != "complete" and previous["identity"] != identity:
            raise contract.RequestError("Pending scope differs; explicit review required")
        if previous is None or previous["state"] == "complete":
            request = {
                "schema": contract.VERSION,
                "request_id": uuid.uuid4().hex,
                "identity": identity,
                "scope": scope,
                "job": args.job,
                "arguments": work_args,
                "pins": {},
                "state": "pending",
            }
        else:
            request = previous
        attempts = state / "attempts" / request["request_id"]
        attempts.mkdir(parents=True, exist_ok=True)
        if previous:
            previous_attempt = previous.get("attempt", {}).get("id")
            previous_dir = state / "attempts" / previous["request_id"]
            previous_dir.mkdir(parents=True, exist_ok=True)
            previous_receipt = previous_dir / (str(previous_attempt) + ".json")
            if not previous_receipt.exists() or previous_receipt.read_bytes() != encode(previous):
                atomic(previous_dir / ("recovered-state-" + uuid.uuid4().hex + ".json"), previous)
        attempt = {
            "id": uuid.uuid4().hex,
            "invocation_id": os.environ.get("INVOCATION_ID"),
            "work": None,
            "monitor": None,
            "errors": [],
        }
        # The prior attempt lives in its immutable ID file before a new attempt starts.
        request["attempt"] = attempt
        atomic(pending, request)

        def emit(event):
            event = {
                **event,
                "job": args.job,
                "request_id": request["request_id"],
                "attempt_id": attempt["id"],
                "invocation_id": attempt["invocation_id"],
            }
            with (state / "decisions.jsonl").open("ab") as f:
                f.write(encode(event))
                f.flush()
                os.fsync(f.fileno())

        def guard():
            if contract.inputs(args.job, work_args, args.binding, ROOT) != scope:
                raise contract.RequestError("Job configuration changed during pending request")

        def save_pins(pins):
            request["pins"] = pins
            atomic(pending, request)

        def save_result(value):
            # Preserve the returned payload even if capturing its references fails.
            request["unsealed_result"] = value
            sealed = contract.seal_result(value)
            request["work_result"] = sealed
            request.pop("unsealed_result", None)
            request["state"] = "result_recorded"
            atomic(pending, request)

        c = jc.Context(
            query,
            emit,
            pins=request["pins"],
            save_pins=save_pins,
            scope_guard=guard,
            save_result=save_result,
        )
        old_handler = signal.signal(
            signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())
        )
        errors = []
        code = None
        disposition = None

        def error(phase, exc):
            attempt["errors"].append(
                {
                    "phase": phase,
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
                    "cause": str(exc.__cause__) if exc.__cause__ else None,
                }
            )
            errors.append(exc)

        def work():
            c.start()  # refuses a dead/failed monitor; never recreates it in place
            guard()
            if request.get("work_result") or request.get("unsealed_result"):
                if request.get("unsealed_result"):
                    save_result(request["unsealed_result"])
                value = request["work_result"]
                recovered_code = result_code(args.job, value["result"])
                contract.classify(args.job, recovered_code, value, request["pins"], work_args)
                attempt["work"] = {
                    "recovered_committed_result": True,
                    "observed_exit_code": None,
                    "result_code": recovered_code,
                }
                c.check_health()
                return recovered_code
            try:
                result = invoke(args.job, work_args)
            except SystemExit as exc:
                result = exc.code
            result = 0 if result is None else result
            attempt["work"] = {"recovered_committed_result": False, "observed_exit_code": result}
            request["work_exit_code"] = result
            c.check_health()
            guard()
            return result

        try:
            with jc.using(c):
                code = run_with_resume(c, work, emit=c.record)
            disposition = contract.classify(
                args.job, code, request.get("work_result"), request["pins"], work_args
            )
            request["disposition"] = disposition
        except BaseException as exc:
            error("work", exc)
        finally:
            try:
                attempt["monitor"] = c.close()
            except BaseException as exc:
                attempt["monitor"] = {
                    "stopped": not bool(c.thread and c.thread.is_alive()),
                    "ok": False,
                }
                error("monitor_close", exc)
            # Always attempt final persistence, including when close failed.
            if (
                c.result is not None
                and not request.get("work_result")
                and not request.get("unsealed_result")
            ):
                request["unsealed_result"] = c.result
            if errors:
                request["state"] = (
                    "interrupted"
                    if any(isinstance(e, KeyboardInterrupt) for e in errors)
                    else "failed"
                )
                if request.get("work_result") or request.get("unsealed_result"):
                    request["state"] = "finalization_failed"
            else:
                request["state"] = disposition["state"]
                # A deferred/failed real job belongs to the next scheduled evaluation;
                # don't mistake its old result for a completed payload on that run.
                if disposition["state"] != "complete":
                    attempt["nonterminal_result"] = request.pop("work_result", None)
            request["exit_code"] = 1 if errors else disposition["returncode"]
            try:
                emit(
                    {
                        "kind": "request_finished",
                        "state": request["state"],
                        "exit_code": request["exit_code"],
                        "work": attempt["work"],
                        "monitor": attempt["monitor"],
                        "errors": attempt["errors"],
                    }
                )
            except BaseException as exc:
                error("final_journal", exc)
                request.update(state="finalization_failed", exit_code=1)
            try:
                atomic(attempts / (attempt["id"] + ".json"), request)
            except BaseException as exc:
                error("attempt_receipt", exc)
                request.update(state="finalization_failed", exit_code=1)
            try:
                atomic(pending, request)
            except BaseException as exc:
                error("request_state", exc)
                request.update(state="finalization_failed", exit_code=1)
                # Rename may have happened before fsync failed. Best effort poison
                # the terminal state; never report success if persistence failed.
                try:
                    atomic(pending, request)
                except BaseException as retry:
                    error("request_state_retry", retry)
            signal.signal(signal.SIGTERM, old_handler)
        if errors:
            raise BaseExceptionGroup("Scheduled request failed; committed data preserved", errors)
        return request["exit_code"]


def result_code(job, result):
    """Derive report semantics only for recovery, never claim an observed exit."""
    if job in ("pack", "backup"):
        return 0 if result["ok"] else 2
    if job == "oi-quality":
        return 2 if result["needs_review"] else 0
    if job == "oi":
        return 0 if all(row.get("complete") for row in result) else 2
    if job == "oi-probe":
        return 0 if result.get("complete") else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
