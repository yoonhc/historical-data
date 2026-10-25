"""Decision-scoped time for scheduled jobs. No clock control or shared gate changes."""

import contextlib
import contextvars
import copy
import dataclasses
import datetime as dt
import ipaddress
import math
import threading
import time

UTC = dt.timezone.utc
VERSION = "scheduled-decisions-v1"
_CURRENT = contextvars.ContextVar("scheduled_clock", default=None)


class ClockDeferred(RuntimeError):
    """An explicitly recognized time condition can be reassessed."""


class ClockFailure(RuntimeError):
    """Monitoring, evidence, configuration, or code failed; never a time retry."""


def finished(job, result, *, files=(), records=()):
    """Called by the real CLI only after its report/ledger has been committed."""
    if active():
        active().finish(job, result, files=files, records=records)


def active():
    return _CURRENT.get()


@contextlib.contextmanager
def using(context):
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


def sample():
    start = time.monotonic_ns()
    wall = time.time_ns()
    boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    end = time.monotonic_ns()
    return dict(
        wall_ns=wall, monotonic_ns=(start + end) // 2, boottime_ns=boot, span_ns=end - start
    )


def continuous(a, b, *, gap=True):
    dm = (b["monotonic_ns"] - a["monotonic_ns"]) / 1e9
    dw = (b["wall_ns"] - a["wall_ns"]) / 1e9
    db = (b["boottime_ns"] - a["boottime_ns"]) / 1e9
    pad = (a["span_ns"] + b["span_ns"]) / 1e9
    if min(dm, dw, db) < 0 or (gap and dm > 5):
        raise ClockDeferred("clock_gap_or_backwards")
    if max(a["span_ns"], b["span_ns"]) > 50_000_000:
        raise ClockDeferred("sample_width")
    if max(abs(dw - dm), abs(db - dm)) > 0.25 + pad:
        raise ClockDeferred("clock_discontinuity_or_suspend")


def instant(ns):
    return dt.datetime(1970, 1, 1, tzinfo=UTC) + dt.timedelta(microseconds=ns // 1000)


@dataclasses.dataclass(frozen=True)
class Point:
    evidence: dict

    @property
    def center(self):
        return instant(self.evidence["sample"]["wall_ns"])

    @property
    def lower(self):
        return instant(self.evidence["lower_utc_ns"])

    @property
    def upper(self):
        # Round outward to datetime's microsecond precision.
        return instant(self.evidence["upper_utc_ns"] + 999)

    def day(self, zone=UTC):
        lo, hi = self.lower.astimezone(zone).date(), self.upper.astimezone(zone).date()
        if lo != hi:
            raise ClockDeferred("ambiguous_calendar_date")
        return lo

    def reached(self, deadline):
        return self.lower >= deadline

    def grid(self, seconds, *, slack=0):
        lo = math.floor((self.lower.timestamp() - slack) / seconds)
        hi = math.floor((self.upper.timestamp() - slack) / seconds)
        if lo != hi:
            raise ClockDeferred("ambiguous_grid_boundary")
        return dt.datetime.fromtimestamp(lo * seconds, UTC)


def assess(value, before, after):
    continuous(before, after)
    elapsed = (after["monotonic_ns"] - before["monotonic_ns"]) / 1e9
    if not 0 <= elapsed <= 3:
        raise ClockDeferred("query_expired")
    t = value["tracking"]
    fields = ("reference_time", "correction", "root_delay", "root_dispersion", "skew_ppm")
    if any(not math.isfinite(t[n]) for n in fields):
        raise ClockDeferred("nonfinite_tracking")
    try:
        ip = ipaddress.ip_address(t["reference_address"])
    except ValueError as exc:
        raise ClockDeferred("invalid_reference_address") from exc
    if ip.is_loopback or ip.is_unspecified or t["reference_id"] in ("00000000", "7F7F0101"):
        raise ClockDeferred("no_external_reference")
    if not 1 <= t["stratum"] <= 15 or t["leap"] != "Normal":
        raise ClockDeferred("not_synchronized_or_leap_pending")
    if min(t["root_dispersion"], t["skew_ppm"]) < 0:
        raise ClockDeferred("invalid_tracking")
    error = abs(t["correction"]) + t["root_dispersion"] + max(0, t["root_delay"]) / 2
    margin = (before["span_ns"] + after["span_ns"]) / 1e9
    # Reuse the reviewed conditional alignment model, not a new precision model.
    radius = error + elapsed + margin
    if not math.isfinite(radius):
        raise ClockDeferred("nonfinite_uncertainty")
    lower = after["wall_ns"] - math.ceil(radius * 1e9)
    upper = after["wall_ns"] + math.ceil(radius * 1e9)
    if t["reference_time"] <= 0 or t["reference_time"] * 1e9 > upper:
        raise ClockDeferred("future_or_invalid_reference")
    point = Point(
        {
            **value,
            "policy": VERSION,
            "sample": after,
            "query_before": before,
            "query_after": after,
            "query_seconds": elapsed,
            "chrony_error_estimate": error,
            "query_alignment_margin": elapsed,
            "sampling_margin": margin,
            "total_uncertainty": radius,
            "lower_utc_ns": lower,
            "upper_utc_ns": upper,
            "reference_age_seconds": after["wall_ns"] / 1e9 - t["reference_time"],
        }
    )
    # No 600s grade and no copied accuracy cap. The actual decision must fit the interval.
    try:
        point.lower
        point.upper
    except (OverflowError, ValueError) as exc:
        raise ClockDeferred("unrepresentable_time_interval") from exc
    return point


class Context:
    def __init__(
        self,
        query,
        emit,
        *,
        sampler=sample,
        sleep=time.sleep,
        pins=None,
        save_pins=None,
        scope_guard=None,
        save_result=None,
        monitor_interval=1,
        stop_timeout=5,
    ):
        self.query, self.emit, self.sampler, self.sleep = query, emit, sampler, sleep
        self.pins = pins if pins is not None else {}
        self.save_pins = save_pins or (lambda value: None)
        self.scope_guard = scope_guard or (lambda: None)
        self.save_result = save_result
        self.monitor_interval, self.stop_timeout = monitor_interval, stop_timeout
        self.mutex = threading.RLock()
        self.stop = threading.Event()
        self.last = self.identity = self.fault = self.fatal = None
        self.thread = None
        self.monitor_required = False
        self.closed = False
        self.result = None

    def fail(self, reason, exc):
        # Latch before attempting any more I/O. close() also uses this without
        # waiting for a mutex possibly held by an unresponsive monitor.
        if self.fatal is None:
            self.fatal = (reason, exc)
        self.check_health()

    def check_health(self):
        if self.fatal is not None:
            reason, exc = self.fatal
            raise ClockFailure(f"{reason}: {type(exc).__name__}: {exc}") from exc
        if self.monitor_required and not self.closed:
            if self.thread is None or not self.thread.is_alive():
                self.fatal = ("monitor_not_running", RuntimeError("required monitor exited"))
                self.check_health()

    def record(self, event):
        try:
            self.emit(event)
        except Exception as exc:
            self.fail("evidence_write_failed", exc)

    def tick(self):
        with self.mutex:
            stamp = self.sampler()
            if self.last:
                continuous(self.last, stamp)
            self.last = stamp
            return stamp

    def point(self, reason):
        with self.mutex:
            self.check_health()
            if self.closed:
                raise ClockFailure("observation_context_closed")
            if self.fault:
                raise ClockDeferred(str(self.fault)) from self.fault
            try:
                self.scope_guard()
                before = self.tick()
                value = self.query()  # only a brief coordinator SH lock
                after = self.tick()
                point = assess(value, before, after)
                self.scope_guard()
                keys = (
                    "boot_id",
                    "provider",
                    "config_sha256",
                    "bootstrap_sha256",
                    "runtime_profile_sha256",
                    "ledger_sha256",
                )
                identity = {k: value[k] for k in keys}
                if self.identity is not None and identity != self.identity:
                    raise ClockDeferred("provider_boot_or_binding_changed")
                self.identity = identity
            except ClockDeferred as exc:
                self.fault = exc
                self.record(
                    {
                        "kind": "clock_deferred",
                        "reason": reason,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                raise
            except Exception as exc:
                self.fail("observation_failed", exc)
            # Evidence I/O is deliberately outside time-condition classification.
            self.record({"kind": "time_decision", "reason": reason, "observation": point.evidence})
            return point

    def restart_observation(self):
        with self.mutex:
            self.check_health()
            if self.closed:
                raise ClockFailure("observation_context_closed")
            try:
                self.scope_guard()
            except Exception as exc:
                self.fail("scope_changed", exc)
            self.record(
                {
                    "kind": "observation_restart",
                    "previous_fault": str(self.fault) if self.fault else None,
                    "previous_sample": self.last,
                }
            )
            self.last = self.identity = self.fault = None

    def wait_point(self, reason):
        while True:
            try:
                return self.point(reason)
            except ClockDeferred:
                self.sleep(5)
                self.restart_observation()

    def pin_day(self, name, point):
        with self.mutex:
            self.check_health()
            if name not in self.pins:
                self.pins[name] = {"day": point.day().isoformat(), "observation": point.evidence}
                try:
                    self.save_pins(self.pins)
                except Exception as exc:
                    self.fail("request_write_failed", exc)
            return self.pins[name]["day"]

    def finish(self, job, result, *, files=(), records=()):
        # The payload may have already committed. Preserve it even if the monitor
        # failed meanwhile; the wrapper must distinguish this from new work.
        with self.mutex:
            value = {
                "job": job,
                "result": copy.deepcopy(result),
                "files": list(map(str, files)),
                "records": list(records),
            }
            if self.result is not None:
                self.fail("duplicate_work_result", RuntimeError(job))
            self.result = value
            try:
                self.scope_guard()
                if self.save_result:
                    self.save_result(value)
            except Exception as exc:
                self.fail("work_result_write_failed", exc)
            self.check_health()

    def start(self):
        with self.mutex:
            self.check_health()
            if self.closed:
                raise ClockFailure("observation_context_closed")
            if self.thread is not None:
                return self
            self.point("entry")

            def monitor():
                try:
                    while not self.stop.wait(self.monitor_interval):
                        with self.mutex:
                            if self.fatal is not None:
                                return
                            if self.fault is not None:
                                continue
                            try:
                                self.tick()
                            except ClockDeferred as exc:
                                self.fault = exc
                                self.record(
                                    {
                                        "kind": "clock_deferred",
                                        "reason": "monitor",
                                        "error": f"{type(exc).__name__}: {exc}",
                                    }
                                )
                except BaseException as exc:
                    if self.fatal is None:
                        self.fatal = ("monitor_failed", exc)
                    # Best effort only; the failure latch does not rely on logging.
                    try:
                        self.emit({"kind": "monitor_failed", "error": str(exc)})
                    except BaseException:
                        pass
                finally:
                    if not self.stop.is_set() and self.fatal is None:
                        self.fatal = ("monitor_exited", RuntimeError("unexpected monitor exit"))

            self.thread = threading.Thread(target=monitor, daemon=True, name="scheduled-clock")
            try:
                self.thread.start()
                self.monitor_required = True
                self.check_health()
            except Exception as exc:
                self.fail("monitor_start_failed", exc)
            return self

    def close(self):
        # Do not acquire the sampling mutex before the bounded join.
        unexpected = self.monitor_required and not self.thread.is_alive() and not self.stop.is_set()
        self.stop.set()
        if self.thread:
            self.thread.join(self.stop_timeout)
            if self.thread.is_alive():
                self.fail("monitor_stop_timeout", TimeoutError("monitor still alive"))
        self.closed = True
        if unexpected and self.fatal is None:
            self.fatal = ("monitor_exited", RuntimeError("unexpected monitor exit"))
        self.check_health()
        return {
            "stopped": True,
            "required": self.monitor_required,
            "last_time_fault": str(self.fault) if self.fault else None,
        }


def freshness(success, started_at, hours, *, context=None):
    try:
        point = (context or active()).point("status_freshness")
        earliest = (point.lower - started_at).total_seconds()
        latest = (point.upper - started_at).total_seconds()
        fresh = (
            True
            if 0 <= earliest <= latest <= hours * 3600
            else False
            if latest < 0 or earliest > hours * 3600
            else None
        )
        return {
            "execution_succeeded": success,
            "fresh": fresh,
            "freshness_known": fresh is not None,
            "age_interval_seconds": [earliest, latest],
            "clock_observation": point.evidence,
        }
    except ClockDeferred as exc:
        return {
            "execution_succeeded": success,
            "fresh": None,
            "freshness_known": False,
            "reason": str(exc),
        }
