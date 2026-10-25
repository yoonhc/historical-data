import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import clock_retirement as safe
from scripts import render_retirement_units
from scripts import retirement_clock as clock


class Samples:
    def __init__(self, wall=1791676800, mono=100):
        self.wall, self.mono, self.boot = wall, mono, mono

    def __call__(self):
        return {
            "wall_ns": round(self.wall * 1e9),
            "monotonic_ns": round(self.mono * 1e9),
            "boottime_ns": round(self.boot * 1e9),
            "span_ns": 1000,
        }

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds
        self.boot += seconds


def provider(
    samples, *, age=900, correction=0.001, delay=0.1, dispersion=0.01, skew=0.2, leap="Normal"
):
    csv = f"5BBD5B9D,91.189.91.157,3,{samples.wall - age},{correction},0.001,0.001,20,0.01,{skew},{delay},{dispersion},256,{leap}"
    return {
        "tracking": clock.tracking(csv),
        "provider": {
            "pid": 123,
            "invocation_id": "a" * 32,
            "argv": ["/usr/sbin/chronyd", "-f", "/etc/chrony/test.conf"],
        },
        "boot_id": "12345678-1234-1234-1234-123456789abc",
        "config_sha256": "b" * 64,
        "bootstrap_sha256": "c" * 64,
        "runtime_profile_sha256": "e" * 64,
        "ledger_sha256": "d" * 64,
    }


def test_stale_reference_is_not_stale_observation(tmp_path):
    s = Samples()
    g = clock.Guard(lambda: provider(s, age=3600), tmp_path / "clock.jsonl", sampler=s)
    first = g.check(fresh=True)
    assert first["ready"] and first["reference_age_seconds"] == 3600
    assert "reference_stale" not in first
    s.advance(1)
    value = g.check()
    assert value["observation_age_seconds"] == 1
    assert value["lower_utc_ns"] == first["lower_utc_ns"]
    for _ in range(15):
        s.advance(1)
        if s.mono < 116:
            g.check()
    with pytest.raises(clock.ClockHold, match="expired"):
        g.check()
    with pytest.raises(clock.ClockHold):
        g.check(fresh=True)  # fault remains latched, even if query would recover


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"age": -1}, "future"),
        ({"correction": 5.01}, "total_uncertainty"),
        ({"dispersion": 5.01}, "total_uncertainty"),
        ({"dispersion": -0.1}, "invalid_dispersion"),
        ({"skew": -1}, "invalid_dispersion_or_skew"),
        ({"leap": "Not synchronised"}, "synchronized"),
        ({"leap": "Insert second"}, "leap"),
    ],
)
def test_other_clock_faults_remain_rejected(tmp_path, change, reason):
    s = Samples()
    g = clock.Guard(lambda: provider(s, **change), tmp_path / "clock.jsonl", sampler=s)
    with pytest.raises(clock.ClockHold, match=reason):
        g.check(fresh=True)


@pytest.mark.parametrize("raw", ["", "x,y", "a,b,3,nan,1,2,3,4,5,6,7,8,9,Normal", "x\nx"])
def test_bad_csv_rejected(raw):
    with pytest.raises(clock.ClockHold):
        clock.tracking(raw)


@pytest.mark.parametrize(
    "kind", ["forward", "backward", "suspend", "gap", "provider", "boot", "config", "query", "log"]
)
def test_guard_faults_before_mutation(tmp_path, kind):
    s = Samples()
    q = [provider(s)]
    g = clock.Guard(lambda: q[0], tmp_path / "clock.jsonl", sampler=s)
    g.check(fresh=True)
    s.advance(1)
    if kind == "forward":
        s.wall += 41
    elif kind == "backward":
        s.wall -= 41
    elif kind == "suspend":
        s.boot += 20
    elif kind == "gap":
        s.advance(10)
    elif kind == "provider":
        q[0]["provider"]["invocation_id"] = "f" * 32
        # Query fixtures must not alias the saved observation.
        g.observation["provider"] = {**q[0]["provider"], "invocation_id": "a" * 32}
    elif kind == "boot":
        q[0]["boot_id"] = "f" * 36
    elif kind == "config":
        q[0]["config_sha256"] = "f" * 64
    elif kind == "query":
        g.query = lambda: (_ for _ in ()).throw(OSError("query unavailable"))
    elif kind == "log":
        g.log = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    with pytest.raises(clock.ClockHold):
        g.check(fresh=True)


def test_provider_query_order_and_duration(tmp_path):
    s = Samples()

    def delayed():
        s.advance(0.01)
        return provider(s, age=0)

    g = clock.Guard(delayed, tmp_path / "clock.jsonl", sampler=s)
    result = g.check(fresh=True)
    assert result["ready"] and result["reference_age_seconds"] == 0
    assert result["uncertainty_seconds"] >= result["conditional_error_bound_seconds"] + 0.01
    clock.validate_observation(result)
    g.query = lambda: (s.advance(3.1), provider(s))[1]
    with pytest.raises(clock.ClockHold, match="expired"):
        g.check(fresh=True)


def test_plan_anchor_rejects_boot_and_jump_without_erasing_history(tmp_path):
    s = Samples()
    g = clock.Guard(lambda: provider(s), tmp_path / "clock.jsonl", sampler=s)
    anchor = g.check(fresh=True)
    bad = copy.deepcopy(anchor)
    bad["boot_id"] = "f" * 36
    with pytest.raises(clock.ClockHold, match="Plan boot"):
        g.check(anchor=bad)
    rows = [json.loads(line) for line in g.log_path.read_text().splitlines()]
    assert rows[0]["kind"] == "provider" and rows[-1]["kind"] == "hold"


def test_fabricated_tighter_witness_rejected(tmp_path):
    s = Samples()
    g = clock.Guard(lambda: provider(s), tmp_path / "clock.jsonl", sampler=s)
    o = g.check(fresh=True)
    for field, value in (
        ("uncertainty_seconds", 0),
        ("upper_utc_ns", o["upper_utc_ns"] - 1),
        ("policy", {}),
    ):
        forged = {**o, field: value}
        with pytest.raises(ValueError):
            clock.validate_observation(forged)


def test_new_system_units_keep_preview_readonly_and_no_catchup():
    p = {
        "store_root": "/srv/store",
        "backup_mount": "/mnt/backup",
        "backup_dir": "/mnt/backup/checkpoints",
        "state_root": "/srv/archive-state",
        "expected_repository_id": "a" * 64,
        "cache_root": "/srv/cache",
        "restic_repository": "/mnt/backup/restic",
    }
    units = render_retirement_units.render(
        "/opt/test", "/etc/test/settings.toml", "/var/lib/test", p
    )
    preview = units["marketdata-checkpoint-retirement-preview.service"]
    actual = units["marketdata-checkpoint-retirement.service"]
    for text in (preview, actual):
        for required in (
            "User=yoonhc",
            "ProtectClock=yes",
            "CapabilityBoundingSet=\n",
            "ProtectSystem=strict",
            "Restart=no",
        ):
            assert required in text
        assert "--user" not in text
    assert "ReadWritePaths=/mnt/backup/checkpoints" not in preview
    assert "ReadWritePaths=/mnt/backup\n" in actual
    assert "ReadWritePaths=/mnt/backup/checkpoints" not in actual
    assert "ReadWritePaths=/mnt/backup/.checkpoints.retiring" not in actual
    timer = units["marketdata-checkpoint-retirement.timer"]
    assert "Persistent=false" in timer and "OnActiveSec" not in timer


def test_service_user_cannot_self_approve_root_files(tmp_path):
    p = tmp_path / "approval.json"
    p.write_text("{}")
    with pytest.raises(clock.ClockHold, match="Untrusted"):
        clock.trusted(p)


@pytest.mark.parametrize("delta,protected", [(-1, True), (0, False), (1, False)])
def test_72h_boundary_uses_conservative_bounds_at_nanosecond_precision(delta, protected):
    # Epoch-sized integers would lose the one-nanosecond boundary as floats.
    upper = 1791676800000000000
    lower = upper + (72 * 3600 + 60) * 10**9 + delta
    assert safe.protected_by_time(lower, upper, 72) is protected


@pytest.mark.parametrize("change", ["none", "dropin", "reload", "bytes", "duplicate"])
def test_actual_system_fragment_must_match_reviewed_unit(tmp_path, monkeypatch, change):
    units = tmp_path / "units"
    units.mkdir()
    (units / safe.UNITS[3]).write_text("reviewed unit")
    installed = tmp_path / "installed.service"
    installed.write_text("changed" if change == "bytes" else "reviewed unit")
    raw = f"FragmentPath={installed}\nDropInPaths={'/etc/unknown.conf' if change == 'dropin' else ''}\nNeedDaemonReload={'yes' if change == 'reload' else 'no'}\n"
    if change == "duplicate":
        raw += "DropInPaths=\n"
    monkeypatch.setattr(clock, "command", lambda args: raw)
    monkeypatch.setattr(clock, "trusted", lambda path: Path(path))
    if change == "none":
        safe.installed_service(SimpleNamespace(unit_directory=units), "unit", "apply-plan")
    else:
        with pytest.raises(ValueError):
            safe.installed_service(SimpleNamespace(unit_directory=units), "unit", "apply-plan")
