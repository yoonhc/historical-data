import httpx
from marketdata import daily_schedule as daily
from marketdata.domain import Key, WriterBusy
from test_daily_schedule import Upstream
from test_wrapper_recovery import wrapper


def test_real_daily_cli_ledger_terminal_and_no_duplicate_http(wrapper, monkeypatch):
    upstream = Upstream()
    key = Key("trade_bars", interval="1h")
    url = upstream.add(key)
    original = daily.Scheduler
    client = httpx.Client(transport=httpx.MockTransport(upstream.handle))
    from datetime import datetime, timezone

    clock = lambda: datetime.fromtimestamp(wrapper.samples.wall, timezone.utc)
    monkeypatch.setattr("marketdata.pipeline.now", clock)
    monkeypatch.setattr("marketdata.binance.now", clock)
    monkeypatch.setattr(
        daily,
        "Scheduler",
        lambda store, keys, policy, schedule: original(
            store, keys, policy, schedule, client=client, clock=clock, sleep=lambda _: None
        ),
    )
    config = wrapper.root / "daily.toml"
    config.write_text(
        '[operations]\nstart="2024-01-14"\nrefresh_days=1\n[[datasets]]\ndataset="trade_bars"\ninterval="1h"\n'
    )
    args = ["run", "--root", str(wrapper.store.root), "--config", str(config)]
    try:
        assert wrapper.run("daily", args) == 0
        r = wrapper.request("daily")
        assert r["state"] == "complete" and r["disposition"]["data_complete"] is True
        assert wrapper.run("daily", args) == 0
        assert upstream.calls == [url, url + ".CHECKSUM"]
        assert wrapper.store.db.execute("SELECT COUNT(*) FROM daily_runs").fetchone()[0] == 2
    finally:
        client.close()


def test_daily_lock_wait_is_nonterminal_and_does_not_spend_attempts(wrapper, monkeypatch):
    config = wrapper.root / "daily.toml"
    config.write_text(
        '[operations]\nstart="2024-01-14"\n[[datasets]]\ndataset="trade_bars"\ninterval="1h"\n'
    )
    monkeypatch.setattr(daily.Scheduler, "run", lambda _: (_ for _ in ()).throw(WriterBusy("busy")))
    assert (
        wrapper.run("daily", ["run", "--root", str(wrapper.store.root), "--config", str(config)])
        == 0
    )
    assert wrapper.request("daily")["state"] == "deferred"
    assert (
        wrapper.store.db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='daily_attempts'"
        ).fetchone()[0]
        == 0
    )
