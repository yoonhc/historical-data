import json
from pathlib import Path

import pytest
from marketdata import cli, job_clock as jc, maintenance
from marketdata.domain import utc
from test_wrapper_recovery import wrapper


@pytest.mark.parametrize("low_after", [False, True])
def test_real_pack_final_report_controls_terminal_warning(wrapper, monkeypatch, low_after):
    original = maintenance.capacity
    calls = []

    def capacity(*a, **k):
        calls.append(1)
        result = original(*a, **k)
        if low_after and len(calls) >= 3:
            result["healthy"] = False
        return result

    monkeypatch.setattr(maintenance, "capacity", capacity)
    code = wrapper.run(
        "pack",
        [
            "--root",
            str(wrapper.store.root),
            "maintenance",
            "--mode",
            "hourly",
            "--min-free-gib",
            "0",
        ],
    )
    assert code == (2 if low_after else 0)
    r = wrapper.request("pack")
    assert r["state"] == "complete" and r["work_result"]["result"]["phase"] == "finished"
    assert r["disposition"]["reason"] == ("finished_capacity_warning" if low_after else "finished")


@pytest.mark.parametrize("upstream_failure", [False, True])
def test_real_oi_update_commit_and_failure_keep_distinct_request_status(
    wrapper, monkeypatch, upstream_failure
):
    from marketdata.domain import Key
    from test_operations import Source

    key = Key("open_interest", interval="5m", series="rest")
    source = Source(wrapper.store, fail_keys=[key.id] if upstream_failure else [])
    monkeypatch.setattr(cli, "Collector", lambda *a, **k: source)
    wrapper.samples.advance(45)
    config = wrapper.root / "oi.toml"
    config.write_text(
        '[operations]\nstart="2024-01-15"\n[[datasets]]\ndataset="open_interest"\ninterval="5m"\nseries="rest"\n'
    )
    code = wrapper.run("oi", ["--root", str(wrapper.store.root), "update", "--config", str(config)])
    assert code == (2 if upstream_failure else 0)
    r = wrapper.request("oi")
    assert r["state"] == ("failed" if upstream_failure else "complete")
    assert r["disposition"]["data_complete"] is not upstream_failure
    assert source.calls


def test_real_probe_response_then_cooldown_is_not_false_completion(wrapper, monkeypatch):
    from marketdata.binance import SourceError
    from marketdata.oi_probe import probe_once
    from test_oi_probe import Fetch, START

    wrapper.samples.wall = START.timestamp() + 2
    source = Fetch(
        wrapper.store, error=SourceError("limited", 429, b"limited", headers={"retry-after": "600"})
    )
    monkeypatch.setattr(
        cli, "probe_once", lambda store: probe_once(store, source, clock=lambda: START)
    )
    args = ["--root", str(wrapper.store.root), "oi-probe"]
    assert wrapper.run("oi-probe", args) == 2
    first = wrapper.request("oi-probe")
    assert first["state"] == "complete" and first["disposition"]["data_complete"] is False
    assert wrapper.run("oi-probe", args) == 2
    second = wrapper.request("oi-probe")
    assert second["state"] == "deferred" and second["disposition"]["reason"] == "cooldown"
    assert source.calls == 1


def test_probe_retains_received_limit_then_propagates_monitoring_failure(wrapper, monkeypatch):
    from marketdata.binance import SourceError
    from marketdata.oi_probe import probe_once
    from test_oi_probe import Fetch, START

    wrapper.samples.wall = START.timestamp() + 2
    source = Fetch(
        wrapper.store, error=SourceError("limited", 429, b"limited", headers={"retry-after": "600"})
    )
    original = wrapper.context_class.point

    def point(c, reason):
        if reason == "oi_probe_receive":
            c.fail("evidence_write_failed", OSError("receive journal unavailable"))
        return original(c, reason)

    monkeypatch.setattr(wrapper.context_class, "point", point)
    monkeypatch.setattr(
        cli, "probe_once", lambda store: probe_once(store, source, clock=lambda: START)
    )
    args = ["--root", str(wrapper.store.root), "oi-probe"]
    with pytest.raises(BaseExceptionGroup):
        wrapper.run("oi-probe", args)
    r = wrapper.request("oi-probe")
    assert r["state"] == "failed" and r["exit_code"] == 1
    receipt = json.loads((wrapper.store.root / "state/oi-probe-latest.json").read_bytes())
    assert receipt["source_id"] and receipt["clock_failure"] and receipt["pending_clock_cooldown"]
    monkeypatch.setattr(wrapper.context_class, "point", original)
    assert wrapper.run("oi-probe", args) == 2
    assert wrapper.request("oi-probe")["state"] == "deferred" and source.calls == 1


def test_actual_network_receipt_is_bound_without_real_network(wrapper, monkeypatch):
    from scripts import network_observer as observer

    path = wrapper.root / "network.toml"
    path.write_text('test="network"\n')
    output = wrapper.root / "network-output"
    monkeypatch.setattr(observer, "load_profile", lambda _: {"output_root": output})
    monkeypatch.setattr(
        observer,
        "observe",
        lambda _: {
            "started_at": "2024-01-15T09:00:02Z",
            "finished_at": "2024-01-15T09:00:03Z",
            "status": "degraded",
            "failed_probes": ["dns"],
            "probes": {"dns": {"ok": False}},
        },
    )
    # Same actual entrypoint, with only the external probes/config parser replaced.
    monkeypatch.setattr(wrapper.runner, "invoke", lambda *a: observer.main())
    import sys

    def invoke(job, args):
        monkeypatch.setattr(sys, "argv", [job, *args])
        return observer.main()

    monkeypatch.setattr(wrapper.runner, "invoke", invoke)
    assert wrapper.run("network-observer", ["--profile", str(path)]) == 0
    r = wrapper.request("network-observer")
    assert r["state"] == "complete" and r["disposition"]["data_complete"] is None
    assert r["work_result"]["result"]["status"] == "degraded"


def test_indirect_backup_profile_is_in_input_identity(wrapper):
    contract = wrapper.runner.load_contract()
    settings, policy, profile, binding = [
        wrapper.root / n for n in ("settings.toml", "policy.toml", "archive.toml", "clock.json")
    ]
    binding.write_text("{}")
    settings.write_text(f'retention_policy="{policy}"\nclock_binding_file="{binding}"\n')
    policy.write_text(f'archive_profile="{profile}"\n')
    profile.write_text('archive_scope="one"\n')
    first = contract.inputs(
        "backup", ["--settings", str(settings), "backup"], binding, wrapper.runner.ROOT
    )
    profile.write_text('archive_scope="two"\n')
    second = contract.inputs(
        "backup", ["--settings", str(settings), "backup"], binding, wrapper.runner.ROOT
    )
    assert first != second and str(profile) in first["files"]


def test_saved_result_tampering_blocks_recovery(wrapper, monkeypatch):
    original = wrapper.context_class.close

    def close(c):
        original(c)
        raise jc.ClockFailure("stop_failure")

    monkeypatch.setattr(wrapper.context_class, "close", close)
    with pytest.raises(BaseExceptionGroup):
        wrapper.run()
    before = wrapper.request()
    report = Path(before["work_result"]["files"][0]["path"])
    report.write_text("{}")
    monkeypatch.setattr(wrapper.context_class, "close", original)
    monkeypatch.setattr(
        wrapper.runner, "invoke", lambda *a: pytest.fail("must not redo uncertain work")
    )
    with pytest.raises(BaseExceptionGroup):
        wrapper.run()
    assert wrapper.request()["state"] == "finalization_failed"
    assert wrapper.request()["pins"] == before["pins"]
