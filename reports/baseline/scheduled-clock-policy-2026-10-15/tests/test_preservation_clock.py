import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from marketdata import job_clock as jc
from test_clock_decisions import Samples, context
from test_restic_schedule import evidence


def test_archive_preserves_success_when_qualification_clock_is_lost(tmp_path, monkeypatch):
    from scripts import restic_schedule as archive

    o, source, receipt, state = evidence()
    o["restore"] = {"status": "passed"}
    p = SimpleNamespace(reports_root=tmp_path)
    (tmp_path / "scheduled").mkdir()
    monkeypatch.setattr(archive, "observation_lock", lambda p: nullcontext())
    monkeypatch.setattr(archive, "load", lambda p: o)
    monkeypatch.setattr(archive, "load_state", lambda p: state)
    monkeypatch.setattr(archive, "reconcile_restore", lambda *a: None)
    monkeypatch.setattr(archive, "service_context", lambda: source)
    monkeypatch.setattr(archive, "save", lambda *a: None)
    monkeypatch.setattr(archive, "execute", lambda *a: receipt)
    s = Samples()
    c, _ = context(s)
    query = c.query
    calls = [0]

    def lost_after_storage():
        calls[0] += 1
        if calls[0] > 1:
            raise OSError("clock lost after successful archive")
        return query()

    c.query = lost_after_storage
    with jc.using(c):
        result = archive.run(p)
    assert result["ok"] and result["archive"]["ok"]
    assert not result["freshness_known"] and result["counted_dates"] == []
    saved = json.loads(next((tmp_path / "scheduled").glob("*.json")).read_bytes())
    assert saved["archive"] == receipt and saved["clock_qualification_reason"]


def test_checkpoint_entrypoint_propagates_time_hold_instead_of_failure(monkeypatch):
    from scripts import checkpoint_evidence as checkpoint

    monkeypatch.setattr("sys.argv", ["checkpoint", "--settings", "/test/settings", "backup"])
    settings = SimpleNamespace(clock_binding_file="/test/binding")
    monkeypatch.setattr(checkpoint.Settings, "load", lambda p: settings)
    monkeypatch.setattr(checkpoint.clock, "trusted", lambda p: p)
    monkeypatch.setattr(
        checkpoint, "run_backup", lambda *a, **k: (_ for _ in ()).throw(jc.ClockDeferred("wait"))
    )
    with pytest.raises(jc.ClockDeferred):
        checkpoint.main()


def test_T05_producer_retains_same_baseline_and_bundle_with_new_job_policy(lab, monkeypatch):
    from marketdata import maintenance, checkpoint_evidence

    store, reg, old_samples, observer = lab
    s = Samples()
    c, _ = context(s)
    with jc.using(c):
        r = maintenance.run(
            store,
            mode="daily",
            min_free_gib=0,
            evidence=checkpoint_evidence.Producer(reg, observer),
        )
    assert r["ok"] and r["backup"]["clock_evidence_state"] == "available"
    bundle = (Path(r["backup"]["path"]) / "checkpoint-evidence.json").read_bytes()
    graph = checkpoint_evidence.decode_bundle(bundle)
    assert graph
    assert len(list((reg.root / "objects").glob("*.json"))) == 2


from test_checkpoint_evidence import lab
