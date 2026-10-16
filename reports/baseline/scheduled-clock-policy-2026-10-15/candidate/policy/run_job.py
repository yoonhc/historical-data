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

    def query():
        with mod.Host(binding).lock() as host:
            return host.query()

    return query


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
    work_args = args.work_args[1:] if args.work_args[:1] == ["--"] else args.work_args
    state = args.state_root / args.job
    state.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(
        encode([jc.VERSION, args.job, work_args, str(args.binding), args.binding.read_text()])
    ).hexdigest()
    with (state / "request.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pending = state / "request.json"
        request = json.loads(pending.read_bytes()) if pending.exists() else {}
        if request.get("state") not in (None, "complete") and request.get("identity") != identity:
            raise RuntimeError("Pending scope differs; explicit review required")
        if not request or request.get("state") == "complete":
            request = {
                "identity": identity,
                "job": args.job,
                "arguments": work_args,
                "pins": {},
                "state": "pending",
            }

        def emit(event):
            event = {**event, "job": args.job, "invocation_id": os.environ.get("INVOCATION_ID")}
            with (state / "decisions.jsonl").open("ab") as f:
                f.write(encode(event))
                f.flush()
                os.fsync(f.fileno())

        def save_pins(pins):
            request["pins"] = pins
            atomic(pending, request)

        atomic(pending, request)
        c = jc.Context(query, emit, pins=request["pins"], save_pins=save_pins)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

        def work():
            if c.thread is None:
                c.start()
            try:
                result = invoke(args.job, work_args)
            except SystemExit as exc:
                result = exc.code
            return 0 if result is None else result

        try:
            with jc.using(c):
                code = run_with_resume(c, work, emit=emit)
            request.update(state="complete" if code in (0, 2) else "failed", exit_code=code)
            return code
        except BaseException as exc:
            request.update(
                state="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                error=str(exc),
            )
            raise
        finally:
            c.close()
            atomic(pending, request)


if __name__ == "__main__":
    raise SystemExit(main())
