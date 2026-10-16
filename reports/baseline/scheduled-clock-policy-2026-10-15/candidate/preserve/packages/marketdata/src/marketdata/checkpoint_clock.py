"""Read-only clock evidence for checkpoint retirement, never a clock controller.

Bounds are conditional on Chrony's source/model assumptions. A completion
witness is an upper bound obtained AFTER verifying a finished checkpoint; it
does not certify or rewrite the checkpoint's original wall-clock timestamp.
"""

import csv
import fcntl
import ipaddress
import json
import math
import os
import re
import stat
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from marketdata.checkpoint_locks import plain_path
from marketdata.domain import json_bytes, sha256


def require(condition, message):
    if not condition:
        raise ValueError(message)


POLICY = {
    "version": "checkpoint-time-bounds-v2",
    "max_uncertainty_seconds": 5.0,
    "retention_margin_seconds": 60,
    "max_observation_age_seconds": 15.0,
    "max_query_seconds": 3.0,
    "max_sample_gap_seconds": 5.0,
    "step_threshold_seconds": 0.25,
    "sample_seconds": 1.0,
    "provider_seconds": 10.0,
}


class ClockHold(RuntimeError):
    pass


def trusted(path, *, owner=0):
    path = Path(path)
    for part in (path, *path.parents):
        s = part.lstat()
        if stat.S_ISLNK(s.st_mode) or s.st_uid != owner or s.st_mode & 0o022:
            raise ClockHold(f"Untrusted clock/approval path: {part}")
    return path


def command(argv):
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=2,
        env={**os.environ, "LC_ALL": "C"},
        check=False,
    )
    if result.returncode:
        raise ClockHold(f"Read-only query failed: {argv[0]} ({result.returncode})")
    return result.stdout


def stamp():
    before = time.monotonic_ns()
    wall = time.time_ns()
    boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    after = time.monotonic_ns()
    return {
        "wall_ns": wall,
        "monotonic_ns": (before + after) // 2,
        "boottime_ns": boot,
        "span_ns": after - before,
    }


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def tracking(raw):
    rows = list(csv.reader(raw.strip().splitlines()))
    if len(rows) != 1 or len(rows[0]) != 14:
        raise ClockHold("Malformed Chrony 4.5 tracking CSV")
    r = rows[0]
    try:
        n = [float(v) for v in r[3:13]]
        if not all(math.isfinite(v) for v in n):
            raise ValueError("nonfinite")
        return {
            "reference_id": r[0],
            "reference_address": r[1],
            "stratum": int(r[2]),
            "reference_time": n[0],
            "correction": n[1],
            "skew_ppm": n[6],
            "root_delay": n[7],
            "root_dispersion": n[8],
            "leap": r[13],
            "raw_csv": raw.strip(),
        }
    except (ValueError, IndexError) as exc:
        raise ClockHold("Invalid Chrony fields") from exc


def assess(t, sample, *, query_seconds=0, sampling_margin=0):
    try:
        address = ipaddress.ip_address(t["reference_address"])
        external = not (address.is_loopback or address.is_unspecified)
    except ValueError:
        external = False
    reasons = []
    if not external or t["reference_id"] in {"00000000", "7F7F0101"}:
        reasons.append("no_external_reference")
    if not 1 <= t["stratum"] <= 15 or t["leap"] != "Normal":
        reasons.append("not_synchronized_or_leap_pending")
    age = sample["wall_ns"] / 1e9 - t["reference_time"]
    error = abs(t["correction"]) + t["root_dispersion"] + max(0, t["root_delay"]) / 2
    radius = error + query_seconds + sampling_margin
    if t["root_dispersion"] < 0 or t["skew_ppm"] < 0:
        reasons.append("invalid_dispersion_or_skew")
    if not math.isfinite(radius) or not 0 <= radius <= POLICY["max_uncertainty_seconds"]:
        reasons.append("total_uncertainty")
    if t["reference_time"] <= 0 or t["reference_time"] * 1e9 > sample["wall_ns"] + math.ceil(
        radius * 1e9
    ):
        reasons.append("future_or_invalid_reference")
    return {
        "ready": not reasons,
        "reasons": reasons,
        "tracking": t,
        "reference_age_seconds": age,
        "conditional_error_bound_seconds": error,
        "chrony_error_estimate": error,
        "query_alignment_margin": query_seconds,
        "sampling_margin": sampling_margin,
        "total_uncertainty": radius,
    }


def continuous(previous, current, *, maximum_gap=True):
    dm = (current["monotonic_ns"] - previous["monotonic_ns"]) / 1e9
    dw = (current["wall_ns"] - previous["wall_ns"]) / 1e9
    db = (current["boottime_ns"] - previous["boottime_ns"]) / 1e9
    margin = (current["span_ns"] + previous["span_ns"]) / 1e9
    if dm < 0 or db < 0 or dw < 0:
        raise ClockHold("Clock moved backwards")
    if maximum_gap and dm > POLICY["max_sample_gap_seconds"]:
        raise ClockHold("Clock observation gap")
    if max(current["span_ns"], previous["span_ns"]) / 1e9 > 0.05:
        raise ClockHold("Clock sampling uncertainty")
    if abs(dw - dm) > POLICY["step_threshold_seconds"] + margin:
        raise ClockHold("Wall-clock discontinuity")
    if abs(db - dm) > POLICY["step_threshold_seconds"] + margin:
        raise ClockHold("Suspend/resume or boottime discontinuity")


def provider_identity(runner=command):
    raw = runner(
        [
            "/usr/bin/systemctl",
            "show",
            "chrony.service",
            "--no-pager",
            "--property=ActiveState,MainPID,InvocationID",
        ]
    )
    fields = {}
    for line in raw.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            if key in fields:
                raise ClockHold("Duplicate Chrony manager property")
            fields[key] = value
    if fields.get("ActiveState") != "active" or not re.fullmatch(
        r"[0-9a-f]{32}", fields.get("InvocationID", "")
    ):
        raise ClockHold("Chrony has no active invocation")
    pid = int(fields["MainPID"])
    if pid <= 0:
        raise ClockHold("Chrony has no PID")
    argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode().rstrip("\0").split("\0")
    for arg in argv:
        if arg.startswith("-") and not arg.startswith("--") and set(arg[1:]) & set("xqQs"):
            raise ClockHold("Clock-disabled/one-shot/RTC Chrony mode")
    return {"pid": pid, "invocation_id": fields["InvocationID"], "argv": argv}


class Host:
    """Pin a reviewed bootstrap profile and hold its existing read-only shared lock."""

    def __init__(self, binding):
        self.binding = binding

    @contextmanager
    def lock(self):
        b = self.binding
        profile_path = trusted(b["bootstrap_profile"])
        raw = profile_path.read_bytes()
        if sha256(raw) != b["bootstrap_profile_sha256"]:
            raise ClockHold("Bootstrap profile differs from the reviewed T05 binding")
        p = json.loads(raw)
        runtime_path = trusted(p["runtime_profile"])
        self.runtime_path = runtime_path
        if sha256(runtime_path.read_bytes()) != p["runtime_profile_sha256"]:
            raise ClockHold("Runtime profile differs")
        runtime = json.loads(runtime_path.read_bytes())
        if os.getuid() != runtime["uid"] or os.getuid() == 0:
            raise ClockHold("Expected non-root retirement account")
        self.config = runtime["chrony_config"]
        self.config_sha256 = runtime["chrony_config_sha256"]
        self.chronyc = runtime["chronyc"]
        trusted(self.chronyc)
        trusted(self.config)
        if sha256(Path(self.config).read_bytes()) != self.config_sha256:
            raise ClockHold("Chrony configuration differs")
        self.profile = p
        self.ledger_path = trusted(Path(p["state_dir"]) / "boot-state.json")
        lock_path = trusted(Path(p["state_dir"]) / "coordinator.lock")
        with lock_path.open("rb") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ClockHold("Clock bootstrap/configuration transition in progress") from exc
            self.ledger_raw = self.ledger_path.read_bytes()
            state = json.loads(self.ledger_raw)
            if (
                state.get("schema") != 1
                or state.get("phase") != "closed"
                or state.get("boot_id") != boot_id()
                or state.get("profile_sha256") != sha256(json.dumps(p, sort_keys=True).encode())
                or state.get("closed_observation", {}).get("ready") is not True
                or state.get("step_attempts") not in (0, 1)
            ):
                raise ClockHold("Current boot preparation is not proven closed")
            self.boot = state["boot_id"]
            yield self

    def query(self):
        first = provider_identity()
        if sha256(self.runtime_path.read_bytes()) != self.profile["runtime_profile_sha256"]:
            raise ClockHold("Runtime profile changed during observation")
        if (
            sha256(Path(self.binding["bootstrap_profile"]).read_bytes())
            != self.binding["bootstrap_profile_sha256"]
        ):
            raise ClockHold("Bootstrap profile changed during observation")
        argv = first["argv"]
        if argv.count("-f") != 1 or argv[argv.index("-f") + 1] != self.config:
            raise ClockHold("Chrony is not using the reviewed configuration")
        if sha256(Path(self.config).read_bytes()) != self.config_sha256:
            raise ClockHold("Chrony configuration changed")
        if self.ledger_path.read_bytes() != self.ledger_raw or boot_id() != self.boot:
            raise ClockHold("Boot preparation changed")
        raw = command([self.chronyc, "-n", "-c", "-h", "127.0.0.1", "tracking"])
        if provider_identity() != first:
            raise ClockHold("Chrony restarted during observation")
        return {
            "tracking": tracking(raw),
            "provider": first,
            "boot_id": self.boot,
            "config_sha256": self.config_sha256,
            "bootstrap_sha256": self.binding["bootstrap_profile_sha256"],
            "runtime_profile_sha256": self.profile["runtime_profile_sha256"],
            "ledger_sha256": sha256(self.ledger_raw),
        }


class Guard:
    """Bounded live monitor. A fault latches for this run and cannot be cleared."""

    def __init__(self, query, log_path, *, sampler=stamp):
        self.query, self.sampler = query, sampler
        self.log_path = plain_path(log_path)
        self.mutex = threading.RLock()
        self.stop_event = threading.Event()
        self.last = None
        self.observation = None
        self.fault = None
        self.thread = None
        self.sequence = 0

    def log(self, kind, **fields):
        record = {"sequence": self.sequence, "kind": kind, **fields}
        self.sequence += 1
        with self.log_path.open("ab") as handle:
            handle.write(json_bytes(record) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def sample(self):
        # Called with mutex held, including after slow provider queries: do not
        # evaluate a newer observation against an earlier sampled wall clock.
        current = self.sampler()
        if self.last:
            continuous(self.last, current)
        self.last = current
        return current

    def refresh(self):
        with self.mutex:
            before = self.sample()
            value = self.query()
            after = self.sample()
            elapsed = (after["monotonic_ns"] - before["monotonic_ns"]) / 1e9
            if elapsed < 0 or elapsed > POLICY["max_query_seconds"]:
                raise ClockHold("Provider query expired")
            padding = (before["span_ns"] + after["span_ns"]) / 1e9
            assessment = assess(
                value["tracking"], after, query_seconds=elapsed, sampling_margin=padding
            )
            if not assessment["ready"]:
                raise ClockHold(", ".join(assessment["reasons"]))
            if self.observation and any(
                value[k] != self.observation[k]
                for k in (
                    "boot_id",
                    "provider",
                    "config_sha256",
                    "bootstrap_sha256",
                    "runtime_profile_sha256",
                    "ledger_sha256",
                )
            ):
                raise ClockHold("Provider or bootstrap identity changed")
            radius = assessment["total_uncertainty"]
            observation = {
                **value,
                **assessment,
                "sample": after,
                "query_before": before,
                "query_after": after,
                "query_seconds": elapsed,
                "uncertainty_seconds": radius,
                "lower_utc_ns": after["wall_ns"] - math.ceil(radius * 1e9),
                "upper_utc_ns": after["wall_ns"] + math.ceil(radius * 1e9),
                "policy": POLICY,
            }
            self.log("provider", observation=observation)
            self.observation = observation
            return observation

    def check(self, *, fresh=False, anchor=None):
        with self.mutex:
            if self.fault:
                raise ClockHold(self.fault)
            try:
                if fresh or self.observation is None:
                    self.refresh()
                current = self.sample()
                age = (current["monotonic_ns"] - self.observation["sample"]["monotonic_ns"]) / 1e9
                if not 0 <= age <= POLICY["max_observation_age_seconds"]:
                    raise ClockHold("Current Chrony observation expired")
                if anchor:
                    if any(
                        anchor[k] != self.observation[k]
                        for k in (
                            "boot_id",
                            "provider",
                            "config_sha256",
                            "bootstrap_sha256",
                            "runtime_profile_sha256",
                            "ledger_sha256",
                        )
                    ):
                        raise ClockHold("Plan boot/provider/configuration changed")
                    continuous(anchor["sample"], current, maximum_gap=False)
                # Deliberately do not advance the lower bound with elapsed wall
                # or monotonic time. The fresh observation's lower bound is
                # conservative for this later decision, with a <=15s lifetime.
                return {
                    **self.observation,
                    "decision_sample": current,
                    "observation_age_seconds": age,
                }
            except Exception as exc:
                self.fault = f"{type(exc).__name__}: {exc}"
                try:
                    self.log("hold", reason=self.fault)
                except OSError:
                    pass
                raise ClockHold(self.fault) from exc

    def start(self):
        self.check(fresh=True)

        def monitor():
            while not self.stop_event.wait(POLICY["sample_seconds"]):
                try:
                    with self.mutex:
                        age = (
                            self.sampler()["monotonic_ns"]
                            - self.observation["sample"]["monotonic_ns"]
                        ) / 1e9
                        self.check(fresh=age >= POLICY["provider_seconds"])
                except Exception:
                    return

        self.thread = threading.Thread(target=monitor, name="retirement-clock", daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=8)
            if self.thread.is_alive():
                raise ClockHold("Clock observer did not stop")


def validate_observation(value):
    require(value["policy"] == POLICY, "Completion witness clock policy differs")
    parsed = tracking(value["tracking"]["raw_csv"])
    require(parsed == value["tracking"], "Witness tracking fields differ")
    before, after = value["query_before"], value["query_after"]
    require(after == value["sample"], "Witness center differs")
    continuous(before, after)
    elapsed = (after["monotonic_ns"] - before["monotonic_ns"]) / 1e9
    require(elapsed == value["query_seconds"], "Witness query bounds differ")
    assessment = assess(
        parsed,
        after,
        query_seconds=elapsed,
        sampling_margin=(before["span_ns"] + after["span_ns"]) / 1e9,
    )
    require(assessment["ready"], "Witness was not a usable clock observation")
    require(0 <= value["query_seconds"] <= POLICY["max_query_seconds"], "Invalid witness query")
    radius = value["uncertainty_seconds"]
    require(
        radius == assessment["total_uncertainty"]
        and all(value[k] == v for k, v in assessment.items()),
        "Witness assessment or uncertainty differs",
    )
    require(
        value["upper_utc_ns"] == value["sample"]["wall_ns"] + math.ceil(radius * 1e9)
        and value["lower_utc_ns"] == value["sample"]["wall_ns"] - math.ceil(radius * 1e9),
        "Witness UTC bounds differ",
    )
    require(re.fullmatch(r"[0-9a-f-]{36}", value["boot_id"]), "Witness boot missing")
    for name in ("config_sha256", "bootstrap_sha256", "runtime_profile_sha256", "ledger_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", value[name]), "Witness identity missing")
    require(
        re.fullmatch(r"[0-9a-f]{32}", value["provider"]["invocation_id"]),
        "Witness provider missing",
    )
    require(
        type(value["provider"]["pid"]) is int and value["provider"]["pid"] > 0,
        "Witness provider PID invalid",
    )


def utc_string(ns):
    return datetime.fromtimestamp(ns / 1e9, UTC).isoformat()
