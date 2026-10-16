import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from marketdata import job_clock as jc
from test_wrapper_recovery import wrapper
from test_checkpoint_evidence import lab


def test_backup_saved_before_close_failure_recovers_original_day_and_T05_graph(
    wrapper, lab, monkeypatch
):
    from scripts import checkpoint_evidence as entry

    store, reg, samples, observe = lab
    directory = wrapper.root / "copies"
    directory.mkdir()
    p = SimpleNamespace(
        store_root=store.root,
        backup_dir=directory,
        backup_mount=None,
        identity=reg.scope,
        guard=lambda: None,
    )
    settings, policy, profile, binding = [
        wrapper.root / n
        for n in ("settings.toml", "policy.toml", "archive.toml", "t05-binding.json")
    ]
    binding.write_text("{}")
    profile.write_text('archive_scope="isolated"\n')
    policy.write_text(f'archive_profile="{profile}"\n')
    settings.write_text(f'retention_policy="{policy}"\nclock_binding_file="{binding}"\n')
    monkeypatch.setattr(
        entry.Settings, "load", lambda _: SimpleNamespace(clock_binding_file=binding)
    )
    monkeypatch.setattr(entry.clock, "trusted", lambda p, **k: p)
    monkeypatch.setattr(entry, "profile", lambda _: p)
    monkeypatch.setattr(entry, "registry", lambda *a: reg)
    monkeypatch.setattr(entry, "fresh_observer", lambda *a: observe)
    original = entry.run_backup
    calls = []

    def backup(*a, **k):
        calls.append(1)
        return original(*a, **k)

    monkeypatch.setattr(entry, "run_backup", backup)
    close = wrapper.context_class.close

    def failed_close(c):
        close(c)
        raise jc.ClockFailure("injected_close")

    monkeypatch.setattr(wrapper.context_class, "close", failed_close)
    args = ["--settings", str(settings), "backup", "--min-free-gib", "0"]
    with pytest.raises(BaseExceptionGroup):
        wrapper.run("backup", args)
    request = wrapper.request("backup")
    assert request["state"] == "finalization_failed" and request["work_exit_code"] == 0
    result = request["work_result"]["result"]
    assert result["backup"]["clock_evidence_state"] == "available"
    sid = Path(result["backup"]["path"]) / "checkpoint-evidence.json"
    before = sid.read_bytes()
    objects = {p.name: p.read_bytes() for p in (reg.root / "objects").glob("*.json")}
    wrapper.samples.advance(86400)
    monkeypatch.setattr(wrapper.context_class, "close", close)
    assert wrapper.run("backup", args) == 0
    after = wrapper.request("backup")
    assert after["state"] == "complete" and after["pins"] == request["pins"]
    assert len(calls) == 1 and sid.read_bytes() == before
    assert {p.name: p.read_bytes() for p in (reg.root / "objects").glob("*.json")} == objects


def test_restic_real_schedule_entrypoint_binds_committed_receipt(wrapper, monkeypatch):
    from scripts import restic_schedule as archive
    from test_restic_schedule import evidence
    from contextlib import nullcontext

    o, source, receipt, state = evidence()
    o["restore"] = {"status": "passed"}
    reports = wrapper.root / "archive-reports"
    (reports / "scheduled").mkdir(parents=True)
    p = SimpleNamespace(reports_root=reports)
    config = wrapper.root / "archive.toml"
    config.write_text('archive_scope="isolated"\n')
    monkeypatch.setattr(archive.Profile, "load", lambda _: p)
    monkeypatch.setattr(archive, "observation_lock", lambda p: nullcontext())
    monkeypatch.setattr(archive, "load", lambda p: o)
    monkeypatch.setattr(archive, "load_state", lambda p: state)
    monkeypatch.setattr(archive, "reconcile_restore", lambda *a: None)
    monkeypatch.setattr(archive, "service_context", lambda *a, **k: source)
    monkeypatch.setattr(
        archive,
        "save",
        lambda p, value: (reports / "observation.json").write_text(json.dumps(value)),
    )
    monkeypatch.setattr(archive, "execute", lambda *a: receipt)
    monkeypatch.setattr(archive.os, "nice", lambda *_: None)
    monkeypatch.setattr(archive.subprocess, "run", lambda *a, **k: None)
    assert wrapper.run("restic-archive", ["--profile", str(config), "run"]) == 0
    r = wrapper.request("restic-archive")
    assert r["state"] == "complete" and r["disposition"]["reason"] == "archive_preserved"
    assert r["work_result"]["result"]["archive"] == receipt
