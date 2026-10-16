import json
from datetime import timedelta

import pytest
from marketdata import job_clock as jc, maintenance, operations, packing
from marketdata.domain import Key, utc
from test_clock_decisions import Samples, context
from test_packing import populate_events


def test_pack_cutoff_uses_lower_bound_and_keeps_content_guards(store):
    populate_events(store)
    s = Samples()
    c, _ = context(s, root_dispersion=2)
    with store.db:
        store.db.execute(
            "UPDATE event_collections SET updated_at=?", (utc("2024-01-15T08:00:01Z").isoformat(),)
        )
    with jc.using(c):
        held = packing.pack_events(store, apply=True, min_free_gib=0)
        assert held["files"] == 0
        s.advance(3)
        result = packing.pack_events(store, apply=True, min_free_gib=0)
    assert result["files"] == 8
    assert store.validate()["ok"]
    # Tampering with a packed copy still blocks removal of loose source bytes.
    pack = result["packs"][0]
    row = store.db.execute("SELECT path FROM file_packs WHERE id=?", (pack["id"],)).fetchone()
    (store.root / row["path"]).write_bytes(b"corrupt")
    with pytest.raises(Exception):
        packing.prune_loose(store, pack_ids=[pack["id"]], apply=True)


def test_backup_logical_day_survives_midnight_validation_and_resume(store, monkeypatch):
    populate_events(store)
    s = Samples("2024-01-15T23:59:59Z")
    c, _ = context(s)
    saved = []
    c.save_pins = lambda p: saved.append(json.loads(json.dumps(p)))
    with store.db:
        store.db.execute(
            "UPDATE event_collections SET updated_at=?", ("2024-01-14T00:00:00+00:00",)
        )
    validate = store.validate

    def slow():
        result = validate()
        s.advance(2)
        return result

    monkeypatch.setattr(store, "validate", slow)
    with jc.using(c):
        r = maintenance.run(store, mode="daily", min_free_gib=0)
    assert r["ok"] and r["work_day"] == "2024-01-15"
    assert "/2024-01-15/" in r["backup"]["path"]
    assert utc("2024-01-16").timestamp() * 1e9 < r["backup_decision_observation"]["lower_utc_ns"]
    other, _ = context(s)
    other.pins = saved[0]
    with jc.using(other):
        again = maintenance.run(store, mode="daily", min_free_gib=0)
    assert again["backup"]["reused"] and again["backup"]["backup_id"] == r["backup"]["backup_id"]


def test_oi_grid_ambiguity_prevents_work_and_later_scope_is_fixed(store):
    s = Samples("2024-01-15T09:00:45Z")
    c, _ = context(s)

    class Collector:
        def __init__(self):
            self.store = store

        def rest(self, *a):
            pytest.fail("must not request before range is unambiguous")

    with jc.using(c), store.writer(), pytest.raises(jc.ClockDeferred, match="grid"):
        operations.update(Collector(), [Key("open_interest", interval="5m", series="rest")])
    assert store.db.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] == 0


def test_network_retains_actual_responses_when_end_time_fails(monkeypatch):
    from scripts import network_observer as observer

    s = Samples()
    c, _ = context(s)
    query = c.query
    count = [0]

    def fail_end():
        count[0] += 1
        if count[0] > 1:
            raise OSError("provider query lost")
        return query()

    c.query = fail_end
    monkeypatch.setattr(observer, "command", lambda *a, **k: {"returncode": 0, "stdout": "[]"})
    monkeypatch.setattr(observer, "link", lambda i: {"ok": True})
    monkeypatch.setattr(observer, "numeric_tcp", lambda a: {"ok": True})
    with jc.using(c):
        r = observer.observe({"external_ip": "192.0.2.1", "expected_interface": "eth0"})
    assert r["probes"] and not r["local_time_quality"]["current_time_known"]


def test_oi_probe_preserves_response_and_cooldown_across_clock_recovery(store):
    from marketdata.oi_probe import probe_once
    from marketdata.binance import SourceError
    from test_oi_probe import Fetch, START

    s = Samples((START + timedelta(seconds=2)).isoformat())
    c, _ = context(s)
    source = Fetch(
        store, error=SourceError("limited", 429, b"limited", headers={"retry-after": "3600"})
    )
    with jc.using(c):
        r = probe_once(store, source, clock=lambda: START + timedelta(seconds=2))
    assert r["pending_clock_cooldown"] and source.calls == 1
    assert store.raw(r["source_id"])[0] == b"limited"
    with jc.using(c):
        held = probe_once(store, source, clock=lambda: START + timedelta(seconds=2))
    assert held["state"] == "cooldown" and source.calls == 1
    assert utc(held["cooldown_until"]).timestamp() >= s.wall + 3600


def test_OI_update_stores_exact_range_and_unknown_status_preserves_success(store):
    from test_operations import Source, SyncPolicy

    key = Key("open_interest", interval="5m", series="rest")
    source = Source(store)
    s = Samples("2024-01-15T09:00:47Z")
    c, _ = context(s)
    with jc.using(c), store.writer():
        r = operations.update(source, [key], SyncPolicy(start="2024-01-15"))
    assert source.calls and source.calls[-1][2] == c.point("expected lower").lower
    assert operations.window_end(key, source.calls[-1][2]) == utc("2024-01-15T09:05:00Z")
    before = [tuple(r) for r in store.db.execute("SELECT id,state,report FROM sync_runs")]
    c.query = lambda: (_ for _ in ()).throw(OSError("not available"))
    with jc.using(c):
        view = operations.status(store, [key])
    assert not view["freshness_known"] and not view["data_reclassified"]
    assert before == [tuple(r) for r in store.db.execute("SELECT id,state,report FROM sync_runs")]


def test_quality_scope_is_pinned_and_reports_retained_warning(store, tmp_path):
    from marketdata.cli import main

    s = Samples("2024-01-15T09:00:02Z")
    c, _ = context(s)
    with jc.using(c):
        code = main(
            [
                "--root",
                str(store.root),
                "oi-quality",
                "--days",
                "7",
                "--output",
                str(tmp_path / "quality"),
            ]
        )
    assert code == 2  # no fabricated source quality on the empty store
    report = json.loads(next((tmp_path / "quality").glob("*/report.json")).read_bytes())
    assert report["comparison_start"].startswith("2024-01-08") and report[
        "comparison_end"
    ].startswith("2024-01-15")
    assert report["clock_observation"]["reference_age_seconds"] == 7200
    assert c.pins["oi_quality_day"]["day"] == "2024-01-15"


def test_probe_query_bound_does_not_fake_wall_clock_jump(store):
    from marketdata.binance import Response
    from marketdata.oi_probe import probe_once

    s = Samples("2024-01-15T09:01:00Z")
    c, _ = context(s, root_dispersion=6)

    class Fetch:
        def fetch(self, *a, **k):
            s.advance(0.2)
            return Response(b"[]", "test:url", utc("2024-01-15T09:01:00.2Z"), {})

    ticks = iter([0.0, 0.2])
    with jc.using(c):
        r = probe_once(
            store, Fetch(), clock=lambda: utc("2024-01-15T09:01:00Z"), monotonic=lambda: next(ticks)
        )
    assert "local_clock_discontinuity" not in r["clock_flags"]
    assert r["requested_at"] > r["query_end"]
