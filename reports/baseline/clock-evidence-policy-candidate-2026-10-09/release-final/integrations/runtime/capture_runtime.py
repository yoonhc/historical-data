"""Collector-only policy v2; initial bootstrap and scheduled job gates stay strict.

The supervisor remains alive across normal one-hour children, so the exception
does not rely on pretending that an unobserved process restart was continuous.
After a supervisor crash/restart, a fresh strict clock observation is required.
"""

import argparse
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from marketdata.clock_evidence import POLICY, Journal, publish_chunk
from marketdata.domain import DataError
from marketdata.store import Store

SPEC = importlib.util.spec_from_file_location(
    "runtime_v1", Path(__file__).with_name("marketdata_runtime.py")
)
rt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rt)


def assessment(clock, policy, at):
    """Fresh provider observation first; never waive a reason string blindly."""
    result = {
        "reference_stale": None,
        "current_estimate_usable": False,
        "reasons": [],
        "raw_start_allowed": False,
    }
    try:
        elapsed = (at["boottime_ns"] - clock["sample"]["boottime_ns"]) / 1e9
        if not 0 <= elapsed <= policy["provider_seconds"] + policy["max_sample_gap_seconds"]:
            raise ValueError("provider_observation_expired")
        if not clock.get("provider"):
            raise ValueError("provider_identity_missing")
        current = rt.assess_clock(clock["tracking"], policy, at["wall_ns"] / 1e9)
        result["reference_stale"] = (
            current["reference_age_seconds"] > policy["max_reference_age_seconds"]
        )
        result["reasons"] = [
            r
            for r in current["reasons"]
            if not (r == "reference_age" and result["reference_stale"])
        ]
        # Both the observation-time verdict and the current projection must be
        # otherwise good. No projection rescues a failed query or invalid sample.
        observed_reasons = clock.get("reasons", [])
        observed_stale_only = (
            observed_reasons == ["reference_age"]
            and clock.get("reference_age_seconds", -1) > policy["max_reference_age_seconds"]
        )
        if not clock.get("ready") and not observed_stale_only:
            result["reasons"].append("observation_not_usable")
        result["current_estimate_usable"] = not result["reasons"]
        result["reference_age_seconds"] = current["reference_age_seconds"]
        result["conditional_error_bound_seconds"] = current["conditional_error_bound_seconds"]
    except (KeyError, ValueError, TypeError) as exc:
        result["reasons"].append(str(exc))
    return result


class Monitor:
    def __init__(self, profile, initial, at, boot_receipt):
        if not initial["ready"] or not boot_receipt.get("ready"):
            raise rt.NotReady("Initial strict preparation required")
        self.policy = profile["policy"]
        self.legacy = rt.ClockMonitor(self.policy, initial, at)
        self.hard_fault_seen = False
        self.stale_seen = False
        self.boot_receipt = boot_receipt

    def observe(self, at, clock):
        legacy = self.legacy.observe(at, clock)
        decision = assessment(clock, self.policy, at)
        structural = [r for r in legacy["reasons"] if r != "provider_unready"]
        if structural or not decision["current_estimate_usable"]:
            self.hard_fault_seen = True
        self.stale_seen |= bool(decision["reference_stale"])
        decision["reasons"].extend(structural)
        decision["current_estimate_usable"] &= not structural
        # An earlier hard fault is not erased by an hourly boundary. A strictly
        # fresh observation may start a later child; stale-only permission stays
        # disabled for this supervisor after any hard fault.
        decision["raw_start_allowed"] = decision["current_estimate_usable"] and (
            not decision["reference_stale"] or not self.hard_fault_seen
        )
        if decision["reference_stale"] and self.hard_fault_seen:
            decision["reasons"].append("prior_hard_fault_blocks_stale_exception")
        return {
            "decision": decision,
            "clock": clock,
            "clock_usable": legacy["clock_usable"],
            "history": {
                "reference_stale_seen": self.stale_seen,
                "hard_fault_seen": self.hard_fault_seen,
                "legacy_violations": legacy["violations"],
            },
            "uncertain_interval": legacy["uncertain_interval"],
        }


class Evidence:
    def __init__(self, profile, stream, *, journal_factory=Journal):
        self.profile, self.stream = profile, stream
        self.root = Path(profile["evidence_root"]) / "v2" / stream
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.root / "supervisor.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise rt.NotReady("Another v2 supervisor owns this stream")
        self.run_id = uuid.uuid4().hex
        self.context = {
            "schema": 2,
            "run_id": self.run_id,
            "boot_id": rt.boot_id(),
            "stream": stream,
            "policy_version": POLICY,
        }
        self.base = {
            **self.context,
            "supervisor_pid": os.getpid(),
            "supervisor_start_ticks": rt.process_start(os.getpid()),
            "systemd_invocation_id": os.environ.get("INVOCATION_ID"),
            "profile_sha256": rt.profile_hash(profile),
            "policy": profile["policy"],
            "scope": "sampled_process_clock_not_per_event",
        }
        self.store = Store(profile["data_root"])
        self.publisher = Publisher(profile["data_root"])
        try:
            self.journal = journal_factory(
                self.store, self.root / "spool", self.base, publisher=self.publisher.submit
            )
        except BaseException:
            self.store.close()
            self.publisher.close()
            self.lock.close()
            raise

    def write(self, state, **extra):
        self.publisher.check()
        row = self.journal.write(state, **extra)
        rt.atomic_json(self.root / "latest.json", row)
        return row

    def flush(self):
        self.journal.flush()
        self.publisher.drain()

    def partial_child_finished(self, child_id):
        dataset = "book_depth" if self.stream == "depth" else "agg_trades"
        row = self.store.db.execute(
            "SELECT state,report FROM event_collections WHERE dataset=? AND kind='websocket' "
            "ORDER BY rowid DESC LIMIT 1",
            (dataset,),
        ).fetchone()
        if row is None or row["state"] != "partial":
            return False
        report = json.loads(row["report"])
        context = report.get("runtime_clock", {})
        return (
            context.get("run_id") == self.run_id
            and context.get("child_id") == child_id
            and context.get("boot_id") == self.context["boot_id"]
            and context.get("terminal_state") == "partial"
            and bool(context.get("collection_end"))
        )

    def close(self):
        self.publisher.close()
        self.store.close()
        self.lock.close()


class Publisher:
    """Ordered bounded commits; a checkpoint writer does not block clock sampling."""

    def __init__(self, root):
        self.root, self.error = root, None
        self.queue = queue.Queue(maxsize=4)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        store = None
        try:
            store = Store(self.root)
            while True:
                path = self.queue.get()
                try:
                    if path is None:
                        return
                    if self.error is None:
                        publish_chunk(store, path)
                except Exception as exc:
                    self.error = exc
                finally:
                    self.queue.task_done()
        except Exception as exc:
            self.error = exc
        finally:
            if store is not None:
                store.close()

    def check(self):
        if self.error is not None:
            raise DataError(f"Clock evidence publication failed: {self.error}") from self.error

    def submit(self, store, path):
        self.check()
        identity = hashlib.sha256(gzip.compress(path.read_bytes(), mtime=0)).hexdigest()
        try:
            self.queue.put_nowait(path)
        except queue.Full as exc:
            raise DataError("Clock publication backlog exceeded four closed chunks") from exc
        return identity

    def drain(self):
        deadline = time.monotonic() + 90
        while self.queue.unfinished_tasks:
            self.check()
            if time.monotonic() >= deadline:
                raise DataError("Clock publication timeout; spool retained")
            time.sleep(0.01)
        self.check()

    def close(self):
        try:
            self.queue.put(None, timeout=1)
        except queue.Full:
            return
        self.thread.join(timeout=90)


def stop_child(child, timeout):
    if child is not None and child.poll() is None:
        os.killpg(child.pid, signal.SIGINT)
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()


def supervise(
    profile,
    stream,
    evidence,
    stop,
    boot_receipt,
    *,
    checker=rt.probe,
    env_check=rt.environment_check,
    spawn=subprocess.Popen,
    stamp=rt.stamp,
    monotonic=time.monotonic,
    max_children=None,
):
    initial = rt.wait_ready(
        profile, evidence, stop, checker=checker, env_check=env_check, monotonic=monotonic
    )
    initial = checker(profile)
    if not initial["ready"] or stop.is_set():
        raise rt.NotReady("Strict readiness changed before first child")
    monitor = Monitor(profile, initial, stamp(), boot_receipt)
    child, sessions, next_start, next_probe = None, 0, 0.0, 0.0
    child_start = None
    child_id = None
    clock, exit_code = initial, 0
    # Backup authority for this run is committed before the child can create a
    # collection. Open evidence after this point has an explicit unknown tail.
    evidence.write("prepared", boot_receipt=boot_receipt, clock=initial, clock_usable=False)
    evidence.flush()
    try:
        while not stop.is_set():
            tick = monotonic()
            if tick >= next_probe:
                clock = checker(profile)
                next_probe = monotonic() + profile["policy"]["provider_seconds"]
            at = stamp()
            observation = monitor.observe(at, clock)
            evidence.write(
                "running" if child else "between_sessions",
                sample_value=at,
                child_pid=child.pid if child else None,
                child_start_ticks=child_start if child else None,
                **observation,
            )
            if child is not None and child.poll() is not None:
                exit_code = child.returncode
                partial_finished = exit_code == 2 and evidence.partial_child_finished(child_id)
                evidence.write(
                    "child_finished",
                    child_pid=child.pid,
                    child_id=child_id,
                    collector_exit_code=exit_code,
                    terminal_partial_confirmed=partial_finished,
                    **observation,
                )
                evidence.flush()
                child = None
                if (exit_code != 0 and not partial_finished) or (
                    max_children is not None and sessions >= max_children
                ):
                    break
                next_start = monotonic() + 15.0
            if (
                child is None
                and monotonic() >= next_start
                and observation["decision"]["raw_start_allowed"]
            ):
                # Refresh the query at the actual child start, not only at the
                # ten-second monitor cadence. This also catches a provider restart.
                clock = checker(profile)
                observation = monitor.observe(stamp(), clock)
                if observation["decision"]["raw_start_allowed"]:
                    evidence.write("starting_child", **observation)
                    evidence.flush()
                    clock = checker(profile)
                    observation = monitor.observe(stamp(), clock)
                    if not observation["decision"]["raw_start_allowed"]:
                        evidence.write("start_refused_after_commit", **observation)
                        continue
                    child_id = uuid.uuid4().hex
                    env = {
                        **os.environ,
                        "MARKETDATA_RUNTIME_RUN_ID": evidence.run_id,
                        "MARKETDATA_CLOCK_CONTEXT": json.dumps(
                            {**evidence.context, "child_id": child_id}
                        ),
                    }
                    child = spawn(
                        rt.collector_command(profile, stream), start_new_session=True, env=env
                    )
                    child_start = rt.process_start(child.pid)
                    sessions += 1
            stop.wait(profile["policy"]["sample_seconds"])
        stop_child(child, profile["policy"]["stop_timeout_seconds"])
        # The child has saved its terminal collection interval before this final
        # sample is published. Interrupted/crashed reports can lack an end.
        observation = monitor.observe(stamp(), checker(profile))
        evidence.write("finished", children=sessions, **observation)
        evidence.flush()
        return exit_code if exit_code >= 0 else 128 - exit_code
    finally:
        stop_child(child, profile["policy"]["stop_timeout_seconds"])


def bootstrap_receipt(path, profile):
    spec = importlib.util.spec_from_file_location(
        "bootstrap_gate", Path(__file__).with_name("clock_gate.py")
    )
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    p, runtime = gate.read_profile(path)
    if runtime != profile:
        raise rt.NotReady("Collector profile differs from the bootstrap-bound profile")
    gate.trusted(runtime["release_root"], directory=True)
    result = gate.check_gate(p, runtime)
    ledger = gate.Ledger(p["state_dir"])
    with ledger.lock(shared=True):
        state = ledger.read()
        if (
            state["boot_id"] != rt.boot_id()
            or state["phase"] != "closed"
            or state["profile_sha256"] != gate.canonical_hash(p)
        ):
            raise rt.NotReady("Bootstrap evidence changed")
        return {
            **result,
            "boot_id": state["boot_id"],
            "profile_sha256": state["profile_sha256"],
            "closed_observation": state["closed_observation"],
            "step_attempts": state["step_attempts"],
            "phase": state["phase"],
        }


def status(profile, stream):
    path = Path(profile["evidence_root"]) / "v2" / stream / "latest.json"
    row = json.loads(path.read_text())
    at = rt.stamp()
    age = (at["boottime_ns"] - row["sample"]["boottime_ns"]) / 1e9
    current = assessment(row.get("clock", {}), profile["policy"], at)
    identity_ok = False
    try:
        identity_ok = (
            rt.process_start(row["supervisor_pid"]) == row["supervisor_start_ticks"]
            and rt.process_start(row["child_pid"]) == row["child_start_ticks"]
        )
    except (KeyError, OSError, TypeError, ValueError):
        pass
    valid = (
        identity_ok
        and row["boot_id"] == rt.boot_id()
        and row["profile_sha256"] == rt.profile_hash(profile)
        and row["state"] == "running"
        and 0 <= age <= profile["policy"]["status_max_age_seconds"]
        and abs((at["wall_ns"] - row["sample"]["wall_ns"]) / 1e9 - age)
        <= profile["policy"]["step_threshold_seconds"]
    )
    return {
        "policy_version": POLICY,
        "raw_running": valid,
        "current_estimate_usable": valid and current["current_estimate_usable"],
        "reference_stale": current["reference_stale"] if valid else None,
        "history": row.get("history"),
        "clock_usable": valid and row.get("clock_usable", False),
        "evidence_age_seconds": age,
        "record": row,
        "note": "Sampled clock status; combine with market data quality. Unknown on stale process evidence.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--bootstrap-profile", type=Path, required=True)
    parser.add_argument("--stream", choices=["depth", "aggtrade"], required=True)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    stop, evidence = threading.Event(), None
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        profile = rt.load_profile(args.profile)
        if args.status:
            result = status(profile, args.stream)
            print(json.dumps(result))
            return 0 if result["raw_running"] else 2
        # Even recovery/import of staged logs must use the reviewed environment.
        rt.environment_check(profile)
        receipt = bootstrap_receipt(args.bootstrap_profile, profile)
        evidence = Evidence(profile, args.stream)
        return supervise(profile, args.stream, evidence, stop, receipt)
    except (
        DataError,
        RuntimeError,
        OSError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
    ) as exc:
        print(json.dumps({"state": "failed", "error": str(exc)}), flush=True)
        return 75 if isinstance(exc, rt.NotReady) else 1
    finally:
        if evidence is not None:
            evidence.close()


if __name__ == "__main__":
    raise SystemExit(main())
