import gzip
import json
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import marketdata.aggtrade_stream as live
import marketdata.aggtrades as agg
import pytest
from marketdata.backup import create_backup, restore_backup, verify_backup
from marketdata.binance import Fetcher, SourceError
from marketdata.cli import main
from marketdata.domain import DataError, Key, json_bytes, micros, utc
from marketdata.normalize import normalize_rest
from marketdata.store import Store

BASE = utc("2024-01-01T12:00")


def trade(identity, *, timestamp=BASE, quantity="1", maker=False, normal=None, ws=False):
    item = {
        "a": identity,
        "p": "100",
        "q": quantity,
        "f": identity * 2,
        "l": identity * 2 + 1,
        "T": micros(timestamp) // 1000,
        "m": maker,
    }
    if normal is not None:
        item["nq"] = normal
    if ws:
        item.update(e="aggTrade", s="BTCUSDT", E=item["T"] + 5, st=1)
    return item


@pytest.fixture(autouse=True)
def current_time(monkeypatch):
    monkeypatch.setattr(agg, "now", lambda: BASE + timedelta(hours=1))


def transport(rows):
    calls = []

    def handler(request):
        params = request.url.params
        calls.append(dict(params))
        if "fromId" in params:
            assert "startTime" not in params and "endTime" not in params
            selected = [r for r in rows if r["a"] >= int(params["fromId"])]
        else:
            selected = [
                r for r in rows if int(params["startTime"]) <= r["T"] <= int(params["endTime"])
            ]
        return httpx.Response(200, json=selected[:1000])

    return Fetcher(
        httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    ), calls


def insert(store, raw, kind="rest", *, received=BASE):
    report = agg.start_collection(store, kind)
    with store.writer():
        source_id = store.put_raw(json_bytes(raw), "test", "test")
    rows = [
        agg.parse_trade(r, source_id, i, received, websocket=kind == "websocket")
        for i, r in enumerate(raw)
    ]
    agg.publish_segment(store, report["id"], kind, rows, source_id)
    return report, source_id, rows


def test_more_than_one_page_at_same_millisecond_uses_ids_and_keeps_exclusive_end(store):
    raw = [trade(i) for i in range(1500)] + [
        trade(1500, timestamp=BASE + timedelta(milliseconds=1))
    ]
    fetcher, calls = transport(raw)
    report = agg.collect_rest(store, BASE, BASE + timedelta(milliseconds=1), fetcher=fetcher)
    assert report["complete"] and report["rows"] == 1500 and report["pages"] == 2
    assert calls[1]["fromId"] == "1000"
    rows, audit = agg.read_trades(store, BASE, BASE + timedelta(milliseconds=1))
    assert audit["valid"] and len(rows) == 1500
    assert rows[-1]["agg_trade_id"] == 1499
    repeated = agg.collect_rest(store, BASE, BASE + timedelta(milliseconds=1), fetcher=fetcher)
    assert repeated["complete"]
    rows, audit = agg.read_trades(store, BASE, BASE + timedelta(milliseconds=1))
    assert len(rows) == 1500 and audit["duplicate_rows"] == 1500 and audit["valid"]


def test_page_budget_does_not_claim_complete_query(store):
    fetcher, _ = transport([trade(i) for i in range(1500)])
    report = agg.collect_rest(
        store, BASE, BASE + timedelta(seconds=1), fetcher=fetcher, max_pages=1
    )
    assert not report["complete"] and not report["query_complete"]
    assert report["stop_reason"] == "page_budget_exhausted"
    assert report["state"] == "partial"


def test_rest_missing_ids_and_requested_edges_are_explicit(store):
    fetcher, _ = transport([trade(11), trade(13)])
    report = agg.collect_rest(store, first_id=10, last_id=14, fetcher=fetcher)
    assert report["gaps"] == [[10, 10], [12, 12], [14, 14]]
    assert report["query_complete"] and not report["complete"]


def test_nonadvancing_or_duplicate_api_page_retains_raw_and_fails(store):
    fetcher, _ = transport([trade(1), trade(1)])
    with pytest.raises(DataError, match="strictly ordered"):
        agg.collect_rest(store, BASE, BASE + timedelta(seconds=1), fetcher=fetcher)
    assert store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    assert store.db.execute("SELECT state FROM event_collections").fetchone()[0] == "failed"
    assert store.db.execute("SELECT count(*) FROM event_segments").fetchone()[0] == 0


@pytest.mark.parametrize(
    "field,value",
    [("m", "false"), ("a", True), ("q", "0"), ("p", "NaN"), ("nq", "2"), ("st", 2), ("f", 100)],
)
def test_schema_rejects_invalid_values(field, value):
    raw = trade(1, ws=True)
    raw[field] = value
    with pytest.raises(DataError):
        agg.parse_trade(raw, "source", 0, BASE, websocket=True)


def test_rate_limit_body_is_saved_and_retry_after_survives_another_command(store):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(429, text="rate limited", headers={"Retry-After": "900"})

    fetcher = Fetcher(
        httpx.Client(transport=httpx.MockTransport(handler)), retries=0, sleep=lambda _: None
    )
    with pytest.raises(SourceError):
        agg.collect_rest(store, BASE, BASE + timedelta(seconds=1), fetcher=fetcher)
    with pytest.raises(DataError, match="cooldown"):
        agg.collect_rest(store, BASE, BASE + timedelta(seconds=1), fetcher=fetcher)
    assert count == 1
    cooldown = json.loads((store.root / "state/aggtrade-rest-cooldown.json").read_bytes())
    assert utc(cooldown["until"]) == BASE + timedelta(hours=1, seconds=900)
    source_id = store.db.execute("SELECT id FROM artifacts").fetchone()[0]
    assert store.raw(source_id)[0] == b"rate limited"


def test_source_conflicts_are_not_hidden_by_deduplication_or_missing_optional_field(store):
    insert(store, [trade(1)])
    insert(store, [trade(1, normal="0.5")], received=BASE + timedelta(seconds=1))
    insert(store, [trade(1, normal="0.6")], received=BASE + timedelta(seconds=2))
    _, audit = agg.read_trades(store, BASE, BASE + timedelta(seconds=1))
    assert not audit["valid"] and audit["conflicts"]
    insert(store, [trade(1, quantity="2")])
    _, audit = agg.read_trades(store, BASE, BASE + timedelta(seconds=1))
    assert any("field" not in r for r in audit["conflicts"])


def test_rest_and_websocket_parity_preserves_distinct_clocks(store):
    insert(store, [trade(1)])
    insert(store, [trade(1, ws=True)], "websocket", received=BASE + timedelta(seconds=1))
    result = agg.compare_sources(store, BASE, BASE + timedelta(seconds=1))
    assert result["equal"] and result["compared"] == 1
    rows, _ = agg.read_trades(store, BASE, BASE + timedelta(seconds=1), kind="websocket")
    assert rows[0]["event_time"] == BASE + timedelta(milliseconds=5)
    assert rows[0]["received_at"] == BASE + timedelta(seconds=1)
    assert rows[0]["timestamp"] == BASE


def test_cross_source_normal_quantity_conflict_is_not_hidden_by_old_missing_field(store):
    insert(store, [trade(1)])
    insert(store, [trade(1, normal="0.5")], received=BASE + timedelta(seconds=1))
    insert(store, [trade(1, ws=True, normal="0.6")], "websocket")
    result = agg.compare_sources(store, BASE, BASE + timedelta(seconds=1))
    assert not result["equal"] and result["mismatched_ids"] == [1]


def test_candle_comparison_uses_exact_volumes_and_does_not_equate_trade_counts(store):
    insert(store, [trade(1), trade(2, maker=True)])
    key = Key("trade_bars")
    raw = [
        [
            micros(BASE) // 1000,
            "100",
            "100",
            "100",
            "100",
            "2",
            micros(BASE + timedelta(minutes=1)) // 1000 - 1,
            "200",
            9,
            "1",
            "100",
            "0",
        ]
    ]
    blob = json_bytes(raw)
    source_id = store.put_raw(blob, "test:candle", "rest")
    store.publish(
        key,
        normalize_rest(blob, key, source_id, BASE),
        BASE,
        BASE + timedelta(minutes=1),
        source_id,
    )
    result = agg.compare_candles(store, BASE, BASE + timedelta(minutes=1))
    assert result["equal"]
    assert result["checks"][0]["aggregate_event_count"] == 2
    assert result["checks"][0]["candle_trade_count"] == 9


def test_event_segments_are_validated_backed_up_and_restored(store, tmp_path):
    insert(store, [trade(1)])
    archived, restored = tmp_path / "backup", tmp_path / "restored"
    result = create_backup(store, archived, min_free_gib=0)
    assert result["catalog_counts"]["event_segments"] == 1
    assert verify_backup(archived)["ok"]
    restore_backup(archived, restored, min_free_gib=0)
    other = Store(restored)
    try:
        rows, audit = agg.read_trades(other, BASE, BASE + timedelta(seconds=1))
        assert rows[0]["price"] == Decimal("100") and audit["valid"]
        assert other.validate()["checked"] == 2
        path = other.db.execute("SELECT path FROM event_segments").fetchone()[0]
        (restored / path).write_bytes(b"broken")
        assert not other.validate()["ok"]
    finally:
        other.close()


def test_candle_value_differences_are_reported_without_modifying_canonical(store):
    insert(store, [trade(1), trade(2, quantity="2", maker=True)])
    key = Key("trade_bars")
    raw = [
        [
            micros(BASE) // 1000,
            "100",
            "100",
            "100",
            "100",
            "2",
            micros(BASE + timedelta(minutes=1)) // 1000 - 1,
            "200",
            9,
            "1",
            "100",
            "0",
        ]
    ]
    blob = json_bytes(raw)
    source_id = store.put_raw(blob, "test:candle", "rest")
    store.publish(
        key,
        normalize_rest(blob, key, source_id, BASE),
        BASE,
        BASE + timedelta(minutes=1),
        source_id,
    )
    result = agg.compare_candles(store, BASE, BASE + timedelta(minutes=1))
    assert not result["equal"]
    assert result["mismatches_by_field"] == {"base_volume": 1, "quote_volume": 1}
    assert result["checks"][0]["differences"]["base_volume"]["delta"] == 1
    assert store.read(key, BASE, BASE + timedelta(minutes=1))["base_volume"][0].as_py() == 2


def test_segment_write_interruption_can_be_replayed(store, monkeypatch):
    report = agg.start_collection(store, "rest")
    source_id = store.put_raw(json_bytes([trade(1)]), "test", "test")
    rows = [agg.parse_trade(trade(1), source_id, 0, BASE)]
    original = agg.atomic_write

    def fail_after_write(path, blob):
        original(path, blob)
        raise OSError("interrupted before catalog publish")

    with monkeypatch.context() as patch:
        patch.setattr(agg, "atomic_write", fail_after_write)
        with pytest.raises(OSError):
            agg.publish_segment(store, report["id"], "rest", rows, source_id)
    assert store.db.execute("SELECT count(*) FROM event_segments").fetchone()[0] == 0
    agg.publish_segment(store, report["id"], "rest", rows, source_id)
    agg.publish_segment(store, report["id"], "rest", rows, source_id)
    assert store.db.execute("SELECT count(*) FROM event_segments").fetchone()[0] == 1


class Clock:
    value = 0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def connector_factory(monkeypatch, sessions, *, step=6):
    clock = Clock()
    monkeypatch.setattr(live.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(live.time, "sleep", clock.sleep)
    sessions = iter(sessions)

    @contextmanager
    def connector(url, **kwargs):
        assert url == agg.WS and "/market/ws/" in url
        messages = iter(next(sessions))

        def recv(timeout):
            clock.value += step
            item = next(messages)
            if isinstance(item, BaseException):
                raise item
            return item if isinstance(item, str) else json.dumps(item)

        yield SimpleNamespace(recv=recv)

    return connector


def test_planned_reconnect_recovers_missing_ids_and_retains_original_gap(store, monkeypatch):
    connector = connector_factory(
        monkeypatch,
        [[trade(10, ws=True), trade(11, ws=True)], [trade(14, ws=True), trade(15, ws=True)]],
    )
    recovered = []

    def recover(first, last):
        recovered.append([first, last])
        result, _, _ = insert(store, [trade(i) for i in range(first, last + 1)])
        return {"id": result["id"], "complete": True}

    report = live.capture(
        store, seconds=23, rotate_seconds=10, connector=connector, recover=recover, min_free_gib=0
    )
    assert report["complete"] and report["connections"] == 2 and report["rotations"] == 1
    assert recovered == [[12, 13]]
    assert report["detected_gaps"] == report["repaired_gaps"] == 1
    _, combined = agg.read_trades(store, BASE, BASE + timedelta(seconds=1))
    _, websocket = agg.read_trades(store, BASE, BASE + timedelta(seconds=1), kind="websocket")
    assert combined["valid"] and not websocket["valid"]


def test_abrupt_disconnect_reconnects_and_failed_repair_stays_partial(store, monkeypatch):
    connector = connector_factory(
        monkeypatch,
        [
            [trade(10, ws=True), OSError("connection lost")],
            [trade(13, ws=True), trade(14, ws=True)],
        ],
    )
    report = live.capture(
        store,
        seconds=24,
        connector=connector,
        min_free_gib=0,
        recover=lambda a, b: {"id": "failed", "complete": False},
    )
    assert report["connections"] == 2 and report["connection_errors"] == 1
    assert report["state"] == "partial" and report["unrepaired_gaps"] == 1
    assert report["persisted_messages"] == 3


def test_duplicate_messages_are_retained_but_reader_counts_once(store, monkeypatch):
    connector = connector_factory(monkeypatch, [[trade(1, ws=True), trade(1, ws=True)]])
    report = live.capture(store, seconds=11, connector=connector, min_free_gib=0)
    assert report["duplicate_messages"] == 1 and report["persisted_messages"] == 2
    rows, audit = agg.read_trades(store, BASE, BASE + timedelta(seconds=1))
    assert len(rows) == 1 and audit["duplicate_rows"] == 1


def test_interrupt_flushes_and_restart_uses_last_persisted_id(store, monkeypatch):
    connector = connector_factory(monkeypatch, [[trade(1, ws=True), KeyboardInterrupt()]])
    with pytest.raises(KeyboardInterrupt):
        live.capture(store, seconds=30, connector=connector, min_free_gib=0)
    assert live.latest_trade(store)["agg_trade_id"] == 1
    connector = connector_factory(monkeypatch, [[trade(3, ws=True)]])
    repaired = []

    def recover(first, last):
        repaired.append([first, last])
        return {"id": "recovery", "complete": True}

    report = live.capture(store, seconds=5, connector=connector, recover=recover, min_free_gib=0)
    assert report["resume_after_id"] == 1 and repaired == [[2, 2]]
    assert (
        store.db.execute(
            "SELECT count(*) FROM event_collections WHERE state='interrupted'"
        ).fetchone()[0]
        == 1
    )


def test_invalid_stream_message_is_preserved_and_cannot_pass_as_healthy(store, monkeypatch):
    connector = connector_factory(monkeypatch, [["not json"]])
    with pytest.raises(json.JSONDecodeError):
        live.capture(store, seconds=5, connector=connector, min_free_gib=0)
    raw = store.raw(store.db.execute("SELECT id FROM artifacts").fetchone()[0])[0]
    assert b"not json" in gzip.decompress(raw)
    assert live.stream_status(store)["state"] == "failed"


def test_unclean_exit_is_recorded_when_the_next_stream_gets_exclusive_lock(store, monkeypatch):
    previous = agg.start_collection(store, "websocket")
    connector = connector_factory(monkeypatch, [[trade(1, ws=True)]])
    live.capture(store, seconds=5, connector=connector, min_free_gib=0)
    record = store.db.execute(
        "SELECT * FROM event_collections WHERE id=?", (previous["id"],)
    ).fetchone()
    assert record["state"] == "interrupted"
    assert json.loads(record["report"])["state"] == "interrupted"
    assert "without a final report" in json.loads(record["report"])["error"]


def test_reader_budget_and_missing_cli_arguments_fail_cleanly(store, capsys):
    insert(store, [trade(1), trade(2)])
    with pytest.raises(DataError, match="row budget"):
        agg.read_trades(store, BASE, BASE + timedelta(seconds=1), max_rows=1)
    assert main(["--root", str(store.root), "aggtrade-rest"]) == 1
    assert "Supply both" in capsys.readouterr().err
