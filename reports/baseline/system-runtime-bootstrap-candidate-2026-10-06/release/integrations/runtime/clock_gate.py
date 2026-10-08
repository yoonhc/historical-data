"""Boot clock coordinator and read-only job gate. No deployment side effects.

Only `bootstrap` can request a step, through chronyd's Unix socket, as root.
The persistent ledger is enrolled closed on the installation boot. A new boot
opens preparation; publishing ready permanently closes stepping for that boot.
An intent is fsynced BEFORE the sole step attempt. An interrupted/ambiguous step
is never replayed. Missing/corrupt state is an error, never an invitation to step.
"""

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "clock_runtime", Path(__file__).with_name("marketdata_runtime.py")
)
rt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rt)


class Hold(RuntimeError):
    pass


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def trusted(path, *, owner=0, directory=False):
    path = Path(path)
    # Check the entire path: a root-owned file below a writable directory is not trusted.
    for part in [path, *path.parents]:
        s = part.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid != owner or s.st_mode & 0o022:
            raise Hold(f"Untrusted ownership/mode/path: {part}")
    s = path.stat()
    if not (stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode)):
        raise Hold(f"Wrong path type: {path}")
    return path


def read_profile(path):
    trusted(path)
    p = json.loads(Path(path).read_text())
    required = {
        "schema",
        "runtime_profile",
        "runtime_profile_sha256",
        "state_dir",
        "chrony_socket",
        "rtc_confirmation",
        "user",
        "uid",
        "policy",
        "user_units",
        "system_collectors",
        "guard_command",
        "guard_files",
    }
    if set(p) != required or p["schema"] != 1:
        raise Hold("Unknown/missing bootstrap profile fields")
    if (
        not re.fullmatch(r"[a-z_][a-z0-9_-]*", p["user"])
        or type(p["uid"]) is not int
        or p["uid"] <= 0
    ):
        raise Hold("Invalid non-root service account")
    for key in ("runtime_profile", "state_dir", "chrony_socket", "rtc_confirmation"):
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", p[key]) or ".." in Path(p[key]).parts:
            raise Hold(f"Unsafe profile path: {key}")
    expected_policy = {
        "step_above_seconds",
        "max_initial_correction_seconds",
        "source_age_seconds",
        "attempt_seconds",
        "poll_seconds",
        "long_wait_seconds",
    }
    if set(p["policy"]) != expected_policy:
        raise Hold("Explicit bootstrap policy required")
    for v in p["policy"].values():
        if type(v) not in (int, float) or not math.isfinite(v) or v <= 0:
            raise Hold("Invalid bootstrap policy value")
    trusted(p["runtime_profile"])
    if rt.digest(p["runtime_profile"]) != p["runtime_profile_sha256"]:
        raise Hold("Runtime profile changed")
    runtime = rt.load_profile(p["runtime_profile"])
    trusted(runtime["chronyc"])
    if runtime["uid"] != p["uid"]:
        raise Hold("Runtime/bootstrap UID mismatch")
    if p["policy"]["step_above_seconds"] > runtime["policy"]["max_correction_seconds"]:
        raise Hold("Step threshold must not leave an unhandled readiness interval")
    if not p["policy"]["step_above_seconds"] < p["policy"]["max_initial_correction_seconds"]:
        raise Hold("Invalid automatic step range")
    for name, mode in p["user_units"].items():
        if not re.fullmatch(r"marketdata-[a-z0-9-]+\.(service|timer)", name):
            raise Hold("Invalid inventory unit")
        if mode not in {"masked", "guarded", "timer"}:
            raise Hold("Invalid inventory mode")
    if not p["user_units"] or not p["guard_files"] or not p["system_collectors"]:
        raise Hold("Empty activation inventory")
    return p, runtime


class Ledger:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "boot-state.json"

    @contextlib.contextmanager
    def lock(self, *, shared=False):
        flags = os.O_RDONLY if shared else os.O_CREAT | os.O_RDWR
        fd = os.open(self.root / "coordinator.lock", flags | os.O_NOFOLLOW, 0o644)
        try:
            if not shared:
                os.fchmod(fd, 0o644)
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            yield
        except BlockingIOError as exc:
            raise Hold("Another bootstrap owns the lock") from exc
        finally:
            os.close(fd)

    def read(self):
        try:
            value = json.loads(self.path.read_text())
            if value["schema"] != 1 or value["phase"] not in {
                "installation_hold",
                "preparing",
                "step_intent",
                "step_returned",
                "closed",
            }:
                raise ValueError("invalid ledger")
            for k in ("boot_id", "profile_sha256", "first_boottime", "step_attempts"):
                if k not in value:
                    raise ValueError(f"missing {k}")
            if type(value["step_attempts"]) is not int or value["step_attempts"] not in (0, 1):
                raise ValueError("invalid attempt count")
            if value["phase"] == "closed" and not value.get("closed_observation", {}).get("ready"):
                raise ValueError("closed state missing readiness evidence")
            if value["phase"] in {"step_intent", "step_returned"} and value["step_attempts"] != 1:
                raise ValueError("invalid intent state")
            return value
        except (OSError, ValueError, KeyError) as exc:
            raise Hold("Missing/corrupt ledger; explicit enrollment/review required") from exc

    def save(self, value):
        # Public read-only receipt; directory and writers remain root-owned.
        rt.atomic_json(self.path, value)
        self.path.chmod(0o644)

    def event(self, kind, **fields):
        record = {"kind": kind, "sample": rt.stamp(), **fields}
        with (self.root / "events.jsonl").open("a") as out:
            out.write(json.dumps(record, sort_keys=True) + "\n")
            out.flush()
            os.fsync(out.fileno())
        return record


def source_assessment(tracking, wall_seconds, runtime_policy, boot_policy):
    # Chrony 4.5 CSV System time is correction to ADD to the raw system clock.
    # See REF_GetTrackingReport(): now_cooked = now_raw + current_correction.
    # Pre-step source freshness/bound must not require the system clock to be corrected already.
    t = {**tracking, "correction": 0.0}
    policy = {**runtime_policy, "max_reference_age_seconds": boot_policy["source_age_seconds"]}
    result = rt.assess_clock(t, policy, wall_seconds + tracking["correction"])
    if abs(tracking["correction"]) > boot_policy["max_initial_correction_seconds"]:
        result["reasons"].append("initial_correction_exceeds_automatic_limit")
    result["ready"] = not result["reasons"]
    result["original_correction_seconds"] = tracking["correction"]
    return result


class Host:
    def __init__(self, p, runtime, runner=rt.command):
        self.p, self.runtime, self.runner = p, runtime, runner

    def rtc_check(self):
        trusted(self.p["rtc_confirmation"])
        receipt = json.loads(Path(self.p["rtc_confirmation"]).read_text())
        if receipt.get("schema") != 1 or receipt.get("windows_utc_confirmed") is not True:
            raise Hold("Windows UTC RTC confirmation absent")
        if not receipt.get("confirmed_at") or not receipt.get("evidence_note"):
            raise Hold("RTC confirmation needs human verification evidence")
        adjtime = Path("/etc/adjtime").read_text().strip().splitlines()
        if not adjtime or adjtime[-1] != "UTC":
            raise Hold("RTC is not UTC in adjtime")
        if (
            self.runner(["/usr/bin/timedatectl", "show", "--property=LocalRTC", "--value"]).strip()
            != "no"
        ):
            raise Hold("RTC is not UTC in timedated")

    def user_command(self, *args):
        return self.runner(
            [
                "/usr/sbin/runuser",
                "-u",
                self.p["user"],
                "--",
                "/usr/bin/env",
                f"XDG_RUNTIME_DIR=/run/user/{self.p['uid']}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{self.p['uid']}/bus",
                "/usr/bin/systemctl",
                "--user",
                *args,
            ]
        )

    def audit_quiescent(self):
        for path, expected in self.p["guard_files"].items():
            trusted(path)
            if rt.digest(path) != expected:
                raise Hold(f"Guard file changed: {path}")
        listed = self.user_command("list-unit-files", "marketdata-*", "--no-legend", "--no-pager")
        names = {line.split()[0] for line in listed.splitlines() if line.strip()}
        if names != set(self.p["user_units"]):
            raise Hold(f"User unit inventory changed: {sorted(names ^ set(self.p['user_units']))}")
        for name, mode in self.p["user_units"].items():
            out = self.user_command(
                "show",
                name,
                "--no-pager",
                "--property=LoadState,ActiveState,MainPID,ControlPID,UnitFileState,ExecStartPre,Unit",
            )
            f = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            if mode == "timer":
                target = name.removesuffix(".timer") + ".service"
                if (
                    f.get("LoadState") != "loaded"
                    or f.get("Unit") != target
                    or self.p["user_units"].get(target) != "guarded"
                ):
                    raise Hold(f"Missing timer: {name}")
                continue  # Only starts a guarded service; does not run marketdata itself.
            if f.get("ActiveState") not in {"inactive", "failed"} or (
                name.endswith(".service")
                and any(f.get(k) != "0" for k in ("MainPID", "ControlPID"))
            ):
                raise Hold(f"Job is not quiescent: {name}")
            if mode == "masked":
                if f.get("LoadState") != "masked" or f.get("UnitFileState") != "masked":
                    raise Hold(f"Persistent mask required: {name}")
            elif f.get("LoadState") != "loaded" or (
                f"argv[]={self.p['guard_command']} ;" not in f.get("ExecStartPre", "")
            ):
                raise Hold(f"Effective startup guard absent: {name}")
        for name in self.p["system_collectors"]:
            out = self.runner(
                [
                    "/usr/bin/systemctl",
                    "show",
                    name,
                    "--property=ActiveState,MainPID,ControlPID",
                    "--no-pager",
                ]
            )
            f = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            if f.get("ActiveState") not in {"inactive", "failed"} or any(
                f.get(k) != "0" for k in ("MainPID", "ControlPID")
            ):
                raise Hold(f"System collector is not quiescent: {name}")

    def observe(self):
        return rt.probe(self.runtime, runner=self.runner)

    def step(self, expected_provider):
        if rt.provider_identity(self.runner) != expected_provider:
            raise Hold("Chrony invocation changed immediately before step")
        # No threshold/count form: do not leave a future automatic step armed.
        return self.runner(
            [self.runtime["chronyc"], "-n", "-h", self.p["chrony_socket"], "makestep"]
        )


def attempt(p, runtime, ledger, host, *, current_boot=None, boottime=None):
    """One locked attempt. No command retry after a persisted step intent."""
    current_boot = current_boot or rt.boot_id()
    now = time.clock_gettime(time.CLOCK_BOOTTIME) if boottime is None else boottime
    identity = canonical_hash(p)
    state = ledger.read()
    if state["profile_sha256"] != identity:
        raise Hold("Bootstrap profile changed; enrollment cannot be silently reused")
    if state["boot_id"] == current_boot and state["phase"] == "installation_hold":
        raise Hold("Installation boot remains held; arm only on a subsequent boot")
    if state["boot_id"] != current_boot:
        state = {
            "schema": 1,
            "boot_id": current_boot,
            "profile_sha256": identity,
            "phase": "preparing",
            "first_boottime": now,
            "step_attempts": 0,
        }
        ledger.save(state)
    if state["phase"] == "closed":
        return {"ready": True, "phase": "closed", "step_reopened": False}
    host.rtc_check()
    host.audit_quiescent()
    observation = host.observe()
    reason = observation.get("reasons", [])
    source = None
    if observation.get("tracking") and observation.get("provider"):
        source = source_assessment(
            observation["tracking"],
            observation["sample"]["wall_ns"] / 1e9,
            runtime["policy"],
            p["policy"],
        )
        reason = source["reasons"]
    if source and source["ready"]:
        correction = abs(observation["tracking"]["correction"])
        if state["phase"] == "preparing" and correction > p["policy"]["step_above_seconds"]:
            # Re-audit after the potentially slow observation, then persist intent.
            host.audit_quiescent()
            state.update(
                phase="step_intent",
                step_attempts=1,
                step_result="unknown",
                step_observation=observation,
            )
            ledger.save(state)
            ledger.event("step_intent", boot_id=current_boot, observation=observation)
            host.step(observation["provider"])
            state["phase"] = "step_returned"
            state["step_result"] = "acknowledged"
            ledger.save(state)
            ledger.event("step_returned", boot_id=current_boot)
            observation = host.observe()
        if observation["ready"]:
            # If an intent was interrupted, only an independent current quality check
            # can release jobs; the prior command's result remains unknown.
            host.audit_quiescent()
            ledger.event("closing_step_window", boot_id=current_boot, observation=observation)
            state.update(phase="closed", closed_observation=observation, closed_boottime=now)
            ledger.save(state)
            return {"ready": True, "phase": "closed", "step_attempts": state["step_attempts"]}
        reason = observation.get("reasons", [])
    elapsed = max(0, now - state["first_boottime"])
    result = {
        "ready": False,
        "phase": state["phase"],
        "reasons": reason,
        "step_attempts": state["step_attempts"],
        "wait_seconds": elapsed,
        "needs_review": state["phase"] == "step_intent",
        "long_wait": elapsed >= p["policy"]["long_wait_seconds"],
    }
    state["last_wait"] = result
    ledger.save(state)
    return result


def check_gate(p, runtime, *, ledger=None, checker=rt.probe, current_boot=None):
    ledger = ledger or Ledger(p["state_dir"])
    # A separate consumer cannot observe readiness while the coordinator is
    # still committing it. The public lock is read-only to the service user.
    with ledger.lock(shared=True):
        state = ledger.read()
        if state["boot_id"] != (current_boot or rt.boot_id()) or state["phase"] != "closed":
            raise Hold("This boot has not closed initial clock preparation")
        if state["profile_sha256"] != canonical_hash(p):
            raise Hold("Closed preparation belongs to a different profile")
        clock = checker(runtime)
        if not clock["ready"]:
            raise Hold("Current clock is not ready: " + ", ".join(clock["reasons"]))
        return {"ready": True, "step_window_closed": True, "clock": clock}


def enroll(p, ledger, current_boot=None):
    if ledger.path.exists():
        raise Hold("Existing ledger must never be overwritten by enrollment")
    state = {
        "schema": 1,
        "boot_id": current_boot or rt.boot_id(),
        "profile_sha256": canonical_hash(p),
        "phase": "installation_hold",
        "first_boottime": time.clock_gettime(time.CLOCK_BOOTTIME),
        "step_attempts": 0,
    }
    ledger.save(state)
    ledger.event("enrolled_for_next_boot", boot_id=state["boot_id"])
    return state


def run_bootstrap(p, runtime, ledger, host, stop):
    deadline = time.monotonic() + p["policy"]["attempt_seconds"]
    with ledger.lock():
        while not stop.is_set():
            try:
                result = attempt(p, runtime, ledger, host)
            except (Hold, rt.NotReady, OSError, ValueError, subprocess.SubprocessError) as exc:
                # Inventory/RTC/provider errors also remain a visible wait across retries.
                # Never create or reset a damaged/missing ledger to record the error.
                state = ledger.read()
                elapsed = max(0, time.clock_gettime(time.CLOCK_BOOTTIME) - state["first_boottime"])
                result = {
                    "ready": False,
                    "phase": state["phase"],
                    "reasons": [str(exc)],
                    "wait_seconds": elapsed,
                    "needs_review": True,
                    "long_wait": elapsed >= p["policy"]["long_wait_seconds"],
                }
            print(json.dumps(result, sort_keys=True), flush=True)
            if result["ready"]:
                return 0
            if result["long_wait"]:
                print(
                    "<4>marketdata_clock_long_wait " + json.dumps(result),
                    file=sys.stderr,
                    flush=True,
                )
            left = deadline - time.monotonic()
            if left <= 0:
                return 75
            stop.wait(min(left, p["policy"]["poll_seconds"]))
    return 75


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["enroll", "bootstrap", "rtc-check", "check", "status"])
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        p, runtime = read_profile(args.profile)
        if args.action in {"enroll", "bootstrap", "rtc-check"} and os.geteuid() != 0:
            raise Hold("Only the root system preparation service may perform this action")
        trusted(runtime["release_root"], directory=True)
        trusted(p["state_dir"], directory=True)
        ledger = Ledger(p["state_dir"])
        if args.action != "enroll":
            trusted(ledger.path)
        if args.action == "enroll":
            with ledger.lock():
                result = enroll(p, ledger)
        elif args.action == "rtc-check":
            Host(p, runtime).rtc_check()
            result = {"rtc_utc": True}
        elif args.action == "bootstrap":
            return run_bootstrap(p, runtime, ledger, Host(p, runtime), stop)
        elif args.action == "status":
            result = {"ledger": ledger.read(), "current_boot_id": rt.boot_id()}
        else:
            result = check_gate(p, runtime)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Hold, rt.NotReady, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ready": False, "hold": str(exc)}), file=sys.stderr, flush=True)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
