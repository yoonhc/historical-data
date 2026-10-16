import copy
import importlib.util
import json
from datetime import timedelta
from pathlib import Path

import pytest
from marketdata import job_clock as jc
from marketdata.domain import utc


class Samples:
    def __init__(self, at="2024-01-15T09:00:02Z"):
        self.wall = utc(at).timestamp()
        self.mono = 100.0
        self.boot = 100.0

    def __call__(self):
        return dict(
            wall_ns=round(self.wall * 1e9),
            monotonic_ns=round(self.mono * 1e9),
            boottime_ns=round(self.boot * 1e9),
            span_ns=1000,
        )

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds
        self.boot += seconds


def provider(s, age=7200, **tracking):
    t = dict(
        reference_id="C0000201",
        reference_address="192.0.2.1",
        stratum=3,
        reference_time=s.wall - age,
        correction=0.001,
        root_delay=0.02,
        root_dispersion=0.01,
        skew_ppm=1.0,
        leap="Normal",
    )
    t.update(tracking)
    return dict(
        tracking=t,
        boot_id="boot1",
        provider={"pid": 12, "invocation_id": "p1"},
        config_sha256="config",
        bootstrap_sha256="bootstrap",
        runtime_profile_sha256="runtime",
        ledger_sha256="closed",
    )


def context(s, **kw):
    events = []
    return jc.Context(lambda: provider(s, **kw), events.append, sampler=s), events


@pytest.mark.parametrize("age", [30, 601, 3600, 86400])
def test_age_alone_does_not_change_decisions(age):
    s = Samples()
    c, events = context(s, age=age)
    p = c.point("date")
    assert p.day().isoformat() == "2024-01-15"
    assert p.reached(utc("2024-01-15T09:00:00Z"))
    assert p.evidence["reference_age_seconds"] == age
    assert "reference_stale" not in p.evidence
    assert events[0]["observation"]["tracking"]["reference_time"] == s.wall - age


@pytest.mark.parametrize(
    "tracking,reason",
    [
        ({"leap": "Not synchronised"}, "synchronized"),
        ({"reference_time": 0}, "reference"),
        ({"reference_time": utc("2024-01-16").timestamp()}, "reference"),
        ({"root_dispersion": -1}, "invalid"),
        ({"correction": float("nan")}, "nonfinite"),
        ({"reference_address": "127.0.0.1"}, "external"),
    ],
)
def test_invalid_observations_are_rejected(tracking, reason):
    s = Samples()
    c, _ = context(s, **tracking)
    with pytest.raises(jc.ClockDeferred, match=reason):
        c.point("decision")


def test_accuracy_numbers_are_not_copied_to_unrelated_date_decisions():
    s = Samples()
    c, _ = context(s, correction=6.0, skew_ppm=150.0)
    p = c.point("day")
    assert p.day().isoformat() == "2024-01-15"
    assert p.evidence["total_uncertainty"] > 5


def test_boundaries_use_interval_not_center_and_future_reference_inside_interval():
    s = Samples("2024-01-15T00:00:00Z")
    c, _ = context(s, age=-0.01)
    p = c.point("calendar")
    with pytest.raises(jc.ClockDeferred, match="calendar"):
        p.day()
    assert not p.reached(utc("2024-01-15"))
    s.advance(1)
    p = c.point("calendar")
    assert p.day().isoformat() == "2024-01-15"
    s = Samples("2024-01-15T09:00:45Z")
    c, _ = context(s)
    with pytest.raises(jc.ClockDeferred, match="grid"):
        c.point("oi").grid(300, slack=45)


@pytest.mark.parametrize("fault", ["gap", "forward", "backward", "suspend", "provider", "query"])
def test_fault_requires_new_observation_and_preserves_history(fault):
    s = Samples()
    q = [provider(s)]
    events = []
    c = jc.Context(lambda: copy.deepcopy(q[0]), events.append, sampler=s)
    c.point("first")
    s.advance(1)
    if fault == "gap":
        s.advance(6)
    if fault == "forward":
        s.wall += 1
    if fault == "backward":
        s.wall -= 2
    if fault == "suspend":
        s.boot += 1
    if fault == "provider":
        q[0]["provider"]["pid"] = 20
    if fault == "query":

        def slow():
            s.advance(3.1)
            return provider(s)

        c.query = slow
    with pytest.raises(jc.ClockDeferred):
        c.point("unsafe")
    with pytest.raises(jc.ClockDeferred):
        c.point("still_latched")
    c.query = lambda: provider(s)
    c.restart_observation()
    c.point("resumed")
    assert any(e["kind"] == "clock_deferred" for e in events)
    assert events[-1]["reason"] == "resumed"


def test_no_cached_bound_is_moved_to_a_new_wall_sample():
    s = Samples()
    calls = []

    def query():
        calls.append(1)
        return provider(s, root_dispersion=len(calls))

    c = jc.Context(query, lambda e: None, sampler=s)
    a = c.point("first")
    s.advance(1)
    b = c.point("later")
    assert len(calls) == 2 and b.evidence["total_uncertainty"] > a.evidence["total_uncertainty"]


def test_status_preserves_success_when_freshness_cannot_be_decided():
    s = Samples()
    c, _ = context(s)
    p = jc.freshness(True, utc("2024-01-15T08:00:02Z"), 1, context=c)
    assert p["execution_succeeded"] and p["fresh"] is None and not p["freshness_known"]
    c.query = lambda: (_ for _ in ()).throw(jc.ClockDeferred("query failed"))
    p = jc.freshness(True, utc("2024-01-15T08:00:02Z"), 1, context=c)
    assert p["execution_succeeded"] and p["fresh"] is None


def load_runner():
    p = Path(__file__).resolve().parents[1] / "candidate/policy/run_job.py"
    spec = importlib.util.spec_from_file_location("job_runner", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_resume_retains_request_pin_without_retrying_other_errors(tmp_path):
    runner = load_runner()
    s = Samples()
    c, events = context(s)
    saves = []
    c.save_pins = lambda x: saves.append(json.loads(json.dumps(x)))
    count = [0]

    def work():
        count[0] += 1
        c.pin_day("backup_day", c.point("day"))
        if count[0] == 1:
            raise jc.ClockDeferred("wait")
        return 2

    assert runner.run_with_resume(c, work, sleep=lambda n: s.advance(1), emit=events.append) == 2
    assert count[0] == 2 and len(saves) == 1

    def failed():
        raise OSError("disk")

    with pytest.raises(OSError):
        runner.run_with_resume(c, failed, sleep=lambda n: pytest.fail("retry"))
