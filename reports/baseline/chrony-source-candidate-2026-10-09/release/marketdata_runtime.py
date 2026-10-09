"""Unprivileged collector supervisor. Never sets time or mutates collection reports.

Clock evidence is sampled, not a per-message timestamp certification. This process
must stay alive alongside its one collector child; systemd owns the cgroup.
"""

import argparse
import csv
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
import platform
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path


class NotReady(RuntimeError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def profile_hash(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


def command(argv, timeout=3):
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode:
        raise NotReady(f"{Path(argv[0]).name} {argv[-1]}: exit {result.returncode}")
    return result.stdout


def load_profile(path):
    value = json.loads(Path(path).read_text())
    required = {
        "schema",
        "uid",
        "release_root",
        "data_root",
        "evidence_root",
        "mount_uuid",
        "chronyc",
        "chrony_config",
        "chrony_config_sha256",
        "policy",
        "dns_hosts",
    }
    if set(value) != required or value["schema"] != 1:
        raise ValueError("Unknown or missing runtime profile fields")
    if type(value["uid"]) is not int or value["uid"] <= 0:
        raise ValueError("A non-root collector UID is required")
    for key in ("release_root", "data_root", "evidence_root", "chronyc", "chrony_config"):
        path_value = Path(value[key])
        if not path_value.is_absolute() or any(c in str(path_value) for c in "\n\r%"):
            raise ValueError(f"Invalid absolute path: {key}")
    defaults = {
        "max_correction_seconds": 0.1,
        "max_error_seconds": 0.5,
        "max_reference_age_seconds": 600.0,
        "max_skew_ppm": 100.0,
        "step_threshold_seconds": 0.25,
        "max_sample_gap_seconds": 5.0,
        "sample_seconds": 1.0,
        "provider_seconds": 10.0,
        "heartbeat_seconds": 10.0,
        "status_max_age_seconds": 25.0,
        "ready_timeout_seconds": 120.0,
        "ready_poll_seconds": 5.0,
        "stop_timeout_seconds": 120.0,
    }
    if set(value["policy"]) != set(defaults):
        raise ValueError("Explicit, complete clock policy required")
    for key, number in value["policy"].items():
        if type(number) not in (float, int) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"Invalid policy value: {key}")
    p = value["policy"]
    if not p["sample_seconds"] < p["max_sample_gap_seconds"] < p["status_max_age_seconds"]:
        raise ValueError("Invalid sample/expiry policy")
    if p["heartbeat_seconds"] >= p["status_max_age_seconds"]:
        raise ValueError("Status must outlive one heartbeat")
    if not value["dns_hosts"] or not all(
        isinstance(h, str)
        and h
        and h.isascii()
        and not h.startswith("-")
        and all(c.isalnum() or c in ".-" for c in h)
        for h in value["dns_hosts"]
    ):
        raise ValueError("Explicit DNS hosts required")
    return value


def stamp():
    # Bracket wall-clock sampling so scheduler preemption is visible as uncertainty.
    before = time.monotonic_ns()
    wall = time.time_ns()
    boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    after = time.monotonic_ns()
    return {
        "wall_ns": wall,
        "monotonic_ns": (before + after) // 2,
        "boottime_ns": boot,
        "sampling_span_ns": after - before,
    }


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def process_start(pid):
    # /proc stat field 22; comm can contain spaces and parentheses.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x") as out:
            json.dump(value, out, sort_keys=True, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def tracking(text):
    rows = list(csv.reader(text.strip().splitlines()))
    # Chrony 4.5 CSV includes reference ID AND numeric address: 14 fields.
    if len(rows) != 1 or len(rows[0]) != 14:
        raise NotReady("Malformed chronyc tracking CSV")
    r = rows[0]
    try:
        numbers = [float(x) for x in r[3:13]]
        if not all(math.isfinite(x) for x in numbers):
            raise ValueError("Non-finite tracking value")
        return {
            "reference_id": r[0],
            "reference_address": r[1],
            "stratum": int(r[2]),
            "reference_time": numbers[0],
            "correction": numbers[1],
            "skew_ppm": numbers[6],
            "root_delay": numbers[7],
            "root_dispersion": numbers[8],
            "leap": r[13],
            "raw_csv": text.strip(),
        }
    except ValueError as exc:
        raise NotReady("Invalid tracking fields") from exc


def assess_clock(t, policy, wall_seconds):
    reasons = []
    try:
        address = ipaddress.ip_address(t["reference_address"])
        remote = not (address.is_loopback or address.is_unspecified)
    except ValueError:
        remote = False
    if not remote or t["reference_id"] in {"00000000", "7F7F0101"}:
        reasons.append("no_external_reference")
    if not 1 <= t["stratum"] <= 15 or t["leap"] != "Normal":
        reasons.append("not_synchronized_or_leap_pending")
    age = wall_seconds - t["reference_time"]
    if not -policy["max_correction_seconds"] <= age <= policy["max_reference_age_seconds"]:
        reasons.append("reference_age")
    error = abs(t["correction"]) + t["root_dispersion"] + max(0, t["root_delay"]) / 2
    if abs(t["correction"]) > policy["max_correction_seconds"]:
        reasons.append("remaining_correction")
    if t["root_dispersion"] < 0 or error > policy["max_error_seconds"]:
        reasons.append("clock_error_bound")
    if not 0 <= t["skew_ppm"] <= policy["max_skew_ppm"]:
        reasons.append("frequency_uncertainty")
    return {
        "ready": not reasons,
        "reasons": reasons,
        "reference_age_seconds": age,
        "conditional_error_bound_seconds": error,
        "tracking": t,
    }


def provider_identity(runner=command):
    text = runner(
        [
            "/usr/bin/systemctl",
            "show",
            "chrony.service",
            "--no-pager",
            "--property=ActiveState,MainPID,InvocationID",
        ]
    )
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    if fields.get("ActiveState") != "active" or not fields.get("InvocationID"):
        raise NotReady("Chrony service is not active with an invocation ID")
    pid = int(fields["MainPID"])
    if pid <= 0:
        raise NotReady("Chrony has no running main process")
    argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
    # -x provides NTP estimates without disciplining the host system clock.
    for arg in argv:
        if arg.startswith("-") and not arg.startswith("--") and any(c in arg[1:] for c in "xqQs"):
            raise NotReady("Chronyd clock-disabled/one-shot/RTC startup mode refused")
    return {"pid": pid, "invocation_id": fields["InvocationID"], "argv": argv}


def probe(profile, runner=command, identity=provider_identity):
    try:
        first = identity(runner)
        argv = first["argv"]
        if "-f" not in argv or argv[argv.index("-f") + 1] != profile["chrony_config"]:
            raise NotReady("Chrony is not using the reviewed explicit configuration")
        if digest(profile["chrony_config"]) != profile["chrony_config_sha256"]:
            raise NotReady("Chrony configuration changed")
        t = tracking(runner([profile["chronyc"], "-n", "-c", "-h", "127.0.0.1", "tracking"]))
        last = identity(runner)
        if first != last:
            raise NotReady("Chrony restarted during observation")
        result = assess_clock(t, profile["policy"], time.time())
        return {**result, "provider": first, "sample": stamp()}
    except (NotReady, OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
        return {"ready": False, "reasons": [str(exc)], "provider": None, "sample": stamp()}


def environment_check(profile, runner=command):
    if os.geteuid() != profile["uid"] or os.geteuid() == 0:
        raise NotReady("Collector must run as the configured non-root UID")
    root = Path(profile["release_root"]).resolve()
    if Path(sys.prefix).resolve() != root / ".venv":
        raise NotReady("Wrong Python environment")
    spec = importlib.util.find_spec("marketdata")
    if not spec or not Path(spec.origin).resolve().is_relative_to(root / ".venv"):
        raise NotReady("Editable or foreign marketdata installation")
    manifest = json.loads((root / "runtime-manifest.json").read_text())
    if manifest["python"] != platform.python_version():
        raise NotReady("Python version differs from the installation receipt")
    for name, expected in manifest["runtime_files_sha256"].items():
        if not (root / name).resolve().is_relative_to(root) or digest(root / name) != expected:
            raise NotReady(f"Release file changed: {name}")
    data = Path(profile["data_root"])
    if data.resolve() != data or not (data / "state/catalog.sqlite").is_file():
        raise NotReady("Missing or aliased existing data root/catalog")
    if (data / "backup.json").exists():
        raise NotReady("Checkpoint cannot be a collector data root")
    if not os.access(data / "state/catalog.sqlite", os.R_OK | os.W_OK):
        raise NotReady("Catalog not accessible to collector UID")
    for name in ("state", "data"):
        if not os.access(data / name, os.R_OK | os.W_OK | os.X_OK):
            raise NotReady(f"Data directory inaccessible: {name}")
    mount = runner(
        ["/usr/bin/findmnt", "--target", str(data), "--noheadings", "--output", "UUID"]
    ).strip()
    if not mount or mount != profile["mount_uuid"]:
        raise NotReady("Data filesystem UUID mismatch")
    # DNS is bounded in a separate process; do not add exchange API requests.
    code = "import socket,sys; [socket.getaddrinfo(h,443,type=socket.SOCK_STREAM) for h in sys.argv[1:]]"
    runner([sys.executable, "-I", "-c", code, *profile["dns_hosts"]])
    return manifest["release_id"]


class Evidence:
    def __init__(self, profile, stream):
        self.root = Path(profile["evidence_root"]) / stream
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.run_id = uuid.uuid4().hex
        self.path = self.root / f"{self.run_id}.jsonl"
        self.base = {
            "schema": 1,
            "run_id": self.run_id,
            "boot_id": boot_id(),
            "supervisor_pid": os.getpid(),
            "stream": stream,
            "supervisor_start_ticks": process_start(os.getpid()),
            "systemd_invocation_id": os.environ.get("INVOCATION_ID"),
            "data_root": profile["data_root"],
            "policy": profile["policy"],
            "profile_sha256": profile_hash(profile),
            "scope": "sampled_process_clock_not_per_event",
        }
        self.lock = (self.root / "supervisor.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock.close()
            raise NotReady("Another runtime supervisor owns this stream") from exc

    def write(self, state, **extra):
        record = {**self.base, "state": state, "sample": stamp(), **extra}
        with self.path.open("a") as out:
            out.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
            out.flush()
            os.fsync(out.fileno())
        atomic_json(self.root / "latest.json", record)
        return record

    def close(self):
        self.lock.close()


def wait_ready(
    profile, evidence, stop, *, checker=probe, env_check=environment_check, monotonic=time.monotonic
):
    deadline = monotonic() + profile["policy"]["ready_timeout_seconds"]
    while not stop.is_set():
        try:
            release_id = env_check(profile)
            result = checker(profile)
        except (NotReady, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            release_id, result = None, {"ready": False, "reasons": [str(exc)]}
        evidence.write(
            "ready" if result["ready"] else "waiting",
            clock=result,
            release_id=release_id,
            child_pid=None,
            clock_usable=False,
        )
        if result["ready"] and monotonic() < deadline:
            return result
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise NotReady("Readiness timeout; collector was not started")
        stop.wait(min(remaining, profile["policy"]["ready_poll_seconds"]))
    raise NotReady("Readiness cancelled; collector was not started")


class ClockMonitor:
    def __init__(self, policy, initial, first_stamp):
        self.policy = policy
        self.provider = initial["provider"]
        self.previous = first_stamp
        self.clean = True
        self.violations = 0

    def observe(self, current, provider):
        prev, self.previous = self.previous, current
        delta_m = (current["monotonic_ns"] - prev["monotonic_ns"]) / 1e9
        delta_b = (current["boottime_ns"] - prev["boottime_ns"]) / 1e9
        delta_w = (current["wall_ns"] - prev["wall_ns"]) / 1e9
        reasons = []
        threshold = self.policy["step_threshold_seconds"]
        if abs(delta_w - delta_b) > threshold:
            reasons.append("wall_clock_discontinuity")
        if abs(delta_b - delta_m) > threshold:
            reasons.append("suspend_or_boottime_discontinuity")
        if delta_m < 0 or delta_m > self.policy["max_sample_gap_seconds"]:
            reasons.append("observation_gap")
        if current["sampling_span_ns"] / 1e9 > threshold:
            reasons.append("sampling_uncertain")
        if not provider["ready"]:
            reasons.append("provider_unready")
        if provider.get("provider") != self.provider:
            reasons.append("provider_instance_changed")
        if reasons:
            self.clean = False
            self.violations += 1
        return {
            "clock_usable": self.clean,
            "violations": self.violations,
            "reasons": reasons,
            "uncertain_interval": {"before": prev, "after": current},
            "wall_minus_boottime_seconds": delta_w - delta_b,
            "boottime_minus_monotonic_seconds": delta_b - delta_m,
        }


def collector_command(profile, stream):
    args = [
        str(Path(profile["release_root"]) / ".venv/bin/python"),
        "-I",
        "-c",
        "from marketdata.cli import main; raise SystemExit(main())",
        "--root",
        profile["data_root"],
        f"{stream}-stream",
        "--seconds",
        "3600",
        "--flush-seconds",
        "5",
        "--rotate-seconds",
        "900" if stream == "depth" else "3600",
        "--min-free-gib",
        "10",
    ]
    if stream == "depth":
        args += ["--verify-seconds", "10", "--top", "20"]
    return args


def supervise(
    profile,
    stream,
    evidence,
    stop,
    *,
    checker=probe,
    env_check=environment_check,
    spawn=subprocess.Popen,
):
    initial = wait_ready(profile, evidence, stop, checker=checker, env_check=env_check)
    # Recheck after readiness; no cached boot marker or pre-start receipt is authority.
    initial = checker(profile)
    if not initial["ready"] or stop.is_set():
        raise NotReady("Readiness changed before collection")
    monitor = ClockMonitor(profile["policy"], initial, stamp())
    child = None
    exit_code = 1
    try:
        evidence.write("starting", clock=initial, child_pid=None, clock_usable=False)
        child = spawn(
            collector_command(profile, stream),
            start_new_session=True,
            env={**os.environ, "MARKETDATA_RUNTIME_RUN_ID": evidence.run_id},
        )
        child_start = process_start(child.pid)
        next_probe = next_heartbeat = 0.0
        current_probe = initial
        last_clock = {"clock_usable": True, "violations": 0}
        while child.poll() is None and not stop.is_set():
            now = time.monotonic()
            if now >= next_probe:
                current_probe = checker(profile)
                next_probe = time.monotonic() + profile["policy"]["provider_seconds"]
            current = stamp()
            last_clock = monitor.observe(current, current_probe)
            if last_clock["reasons"] or now >= next_heartbeat:
                evidence.write(
                    "running",
                    child_pid=child.pid,
                    child_start_ticks=child_start,
                    clock=current_probe,
                    **last_clock,
                )
                next_heartbeat = time.monotonic() + profile["policy"]["heartbeat_seconds"]
            stop.wait(profile["policy"]["sample_seconds"])
        if child.poll() is None:
            evidence.write(
                "stopping", child_pid=child.pid, clock_usable=False, violations=monitor.violations
            )
            os.killpg(child.pid, signal.SIGINT)
            try:
                child.wait(timeout=profile["policy"]["stop_timeout_seconds"])
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                evidence.write("forced_stop", child_pid=child.pid, clock_usable=False)
                return 1
        exit_code = child.returncode
        evidence.write(
            "finished",
            child_pid=child.pid,
            collector_exit_code=exit_code,
            clock_usable=False,
            session_clock_clean=monitor.clean,
            violations=monitor.violations,
        )
        return exit_code if exit_code >= 0 else 128 - exit_code
    finally:
        # An evidence write error must not leave an unmonitored child running.
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGINT)
            try:
                child.wait(timeout=profile["policy"]["stop_timeout_seconds"])
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


def status(profile, stream):
    record = json.loads((Path(profile["evidence_root"]) / stream / "latest.json").read_text())
    current = stamp()
    age = (current["boottime_ns"] - record["sample"]["boottime_ns"]) / 1e9
    current_boot = record["boot_id"] == boot_id()
    alive = False
    child_alive = False
    try:
        alive = process_start(record["supervisor_pid"]) == record["supervisor_start_ticks"]
        if record.get("child_pid") and record.get("child_start_ticks"):
            child_alive = process_start(record["child_pid"]) == record["child_start_ticks"]
    except OSError:
        pass
    wall_delta = (current["wall_ns"] - record["sample"]["wall_ns"]) / 1e9
    continuous = abs(wall_delta - age) <= profile["policy"]["step_threshold_seconds"]
    usable = (
        current_boot
        and alive
        and child_alive
        and record["profile_sha256"] == profile_hash(profile)
        and 0 <= age <= profile["policy"]["status_max_age_seconds"]
        and continuous
        and record["state"] == "running"
        and record.get("clock_usable") is True
    )
    return {
        "clock_usable": usable,
        "evidence_age_seconds": age,
        "record": record,
        "note": "Combine with depth-status/aggtrade-status; does not supersede data quality",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["wait-ready", "run", "status"])
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--stream", required=True, choices=["depth", "aggtrade"])
    args = parser.parse_args()
    evidence = None
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        profile = load_profile(args.profile)
        if args.action == "status":
            result = status(profile, args.stream)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["clock_usable"] else 2
        evidence = Evidence(profile, args.stream)
        if args.action == "wait-ready":
            wait_ready(profile, evidence, stop)
            return 0
        return supervise(profile, args.stream, evidence, stop)
    except (NotReady, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"state": "failed", "error": str(exc)}), file=sys.stderr, flush=True)
        if evidence is not None:
            try:
                evidence.write("failed", error=str(exc), clock_usable=False)
            except OSError:
                pass  # stderr/service failure remains; durable publication is not guaranteed.
        return 75 if isinstance(exc, NotReady) else 1
    finally:
        if evidence is not None:
            evidence.close()


if __name__ == "__main__":
    raise SystemExit(main())
