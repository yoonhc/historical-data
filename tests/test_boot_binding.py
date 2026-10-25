import hashlib
import json
import os
from pathlib import Path

import pytest
from marketdata import checkpoint_clock as hostclock
from marketdata import job_clock as jc
from test_clock_decisions import Samples


def host_fixture(tmp_path, monkeypatch, change=None):
    def h(raw):
        return hashlib.sha256(raw).hexdigest()

    conf = tmp_path / "chrony.conf"
    conf.write_text("server 192.0.2.1\n")
    runtime = tmp_path / "runtime.json"
    runtime.write_text(
        json.dumps(
            {
                "uid": os.getuid(),
                "chrony_config": str(conf),
                "chrony_config_sha256": h(conf.read_bytes()),
                "chronyc": "/test/chronyc",
            }
        )
    )
    p = {
        "runtime_profile": str(runtime),
        "runtime_profile_sha256": h(runtime.read_bytes()),
        "state_dir": str(tmp_path),
    }
    profile = tmp_path / "bootstrap.json"
    profile.write_text(json.dumps(p))
    state = {
        "schema": 1,
        "phase": "closed",
        "boot_id": "boot",
        "profile_sha256": h(json.dumps(p, sort_keys=True).encode()),
        "closed_observation": {"ready": True},
        "step_attempts": 1,
    }
    if change:
        state.update(change)
    ledger = tmp_path / "boot-state.json"
    ledger.write_text(json.dumps(state))
    (tmp_path / "coordinator.lock").touch()
    monkeypatch.setattr(hostclock, "trusted", lambda p, **k: Path(p))
    monkeypatch.setattr(hostclock, "boot_id", lambda: "boot")
    monkeypatch.setattr(
        hostclock,
        "provider_identity",
        lambda: {"pid": 12, "argv": ["chronyd", "-f", str(conf)], "invocation_id": "pid12"},
    )
    s = Samples()
    csv = f"C0000201,192.0.2.1,3,{s.wall - 7200},0.001,0,0,0,0,1,.02,.01,256,Normal"
    monkeypatch.setattr(hostclock, "command", lambda argv: csv)
    binding = {
        "bootstrap_profile": str(profile),
        "bootstrap_profile_sha256": h(profile.read_bytes()),
    }
    return hostclock.Host(binding), s, ledger


@pytest.mark.parametrize(
    "change",
    [
        {"phase": "pending"},
        {"schema": 2},
        {"boot_id": "old"},
        {"closed_observation": {"ready": False}},
        {"step_attempts": 2},
        {"profile_sha256": "different"},
    ],
)
def test_no_job_policy_can_skip_boot_preparation_or_pending_binding(tmp_path, monkeypatch, change):
    host, s, ledger = host_fixture(tmp_path, monkeypatch, change)
    before = ledger.read_bytes()
    with pytest.raises(hostclock.ClockHold):
        with host.lock():
            host.query()
    assert ledger.read_bytes() == before


def test_fresh_query_with_old_reference_uses_unchanged_closed_ledger(tmp_path, monkeypatch):
    host, s, ledger = host_fixture(tmp_path, monkeypatch)
    before = ledger.read_bytes()

    def query():
        with host.lock():
            return host.query()

    c = jc.Context(query, lambda e: None, sampler=s)
    assert c.point("business").evidence["reference_age_seconds"] == 7200
    assert ledger.read_bytes() == before


def test_coordinator_exclusive_transition_is_a_hold_not_a_second_step(tmp_path, monkeypatch):
    import fcntl

    host, s, ledger = host_fixture(tmp_path, monkeypatch)
    before = ledger.read_bytes()
    with (tmp_path / "coordinator.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(hostclock.ClockHold, match="transition"):
            with host.lock():
                host.query()
    assert ledger.read_bytes() == before
