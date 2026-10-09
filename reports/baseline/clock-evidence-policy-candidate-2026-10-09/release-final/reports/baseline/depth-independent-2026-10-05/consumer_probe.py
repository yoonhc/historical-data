"""Exercise real collector/status/CLI/replay with isolated storage and fake I/O.

The two-second persistence delay is synthetic, not an estimate for either real
incident. Observe before and after the actual failure-report commit separately.
"""

import io
import json
import tempfile
from contextlib import contextmanager, redirect_stdout
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from marketdata import depth
from marketdata import depth_stream as live
from marketdata.binance import Fetcher
from marketdata.cli import main as cli
from marketdata.domain import json_bytes, micros, utc
from marketdata.store import Store

OUT = Path(__file__).resolve().parent
BASE = utc("2024-01-01")


def probe(root, remaining, persistence_delay=2):
    observations = []
    clock = SimpleNamespace(seconds=0, identity=100, snapshots=0, receipts=[])
    exchange = {
        "bids": {Decimal(p): Decimal(1) for p in range(800, 1000)},
        "asks": {Decimal(p): Decimal(1) for p in range(1001, 1501)},
    }
    store = Store(root)

    def observe(phase, in_memory=None):
        status = depth.status(store)
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli(["--root", str(root), "depth-status"])
        cli_status = json.loads(output.getvalue())
        assert cli_status == json.loads(json_bytes(status))
        latest = json.loads((root / "state/depth-latest.json").read_bytes())
        report = status["report"]
        assert latest == report
        observations.append(
            {
                "phase": phase,
                "synthetic_seconds": clock.seconds,
                "healthy": status["healthy"],
                "cli_exit": code,
                "state": status["state"],
                "synchronized": report["synchronized"],
                "sync_losses": report["sync_losses"],
                "last_update_id": report.get("last_update_id"),
                "last_receive_age_seconds": status["last_receive_age_seconds"],
                "in_memory_sync_losses": None if in_memory is None else in_memory["sync_losses"],
                "in_memory_synchronized": None if in_memory is None else in_memory["synchronized"],
                "epochs": [
                    {
                        "epoch": e["epoch"],
                        "end_reason": e.get("end_reason"),
                        "error": e.get("final", {}).get("error"),
                        "book": e.get("final", {}).get("book"),
                    }
                    for e in report["epochs"]
                ],
            }
        )

    def fetch(request):
        clock.snapshots += 1
        if clock.snapshots == 2:
            observe("before_recovery_snapshot")
        return httpx.Response(
            200,
            json={
                "lastUpdateId": clock.identity,
                "T": micros(BASE) // 1000 + clock.identity,
                "E": micros(BASE) // 1000 + clock.identity + 1,
                **{
                    side: [
                        [str(p), str(values[p])]
                        for p in sorted(values, reverse=side == "bids")[:100]
                    ]
                    for side, values in exchange.items()
                },
            },
        )

    @contextmanager
    def connector(*args, **kwargs):
        def recv(timeout):
            clock.seconds += 1
            previous = clock.identity
            clock.identity += 1
            raw = {
                "e": "depthUpdate",
                "s": "BTCUSDT",
                "st": 1,
                "U": 100 if clock.identity == 101 else clock.identity,
                "u": clock.identity,
                "pu": previous,
                "T": micros(BASE) // 1000 + clock.identity,
                "E": micros(BASE) // 1000 + clock.identity + 1,
                "b": [],
                "a": [],
            }
            if clock.identity == 103:
                side, field = ("bids", "b") if remaining == 0 else ("asks", "a")
                removed = sorted(exchange[side], reverse=side == "bids")[: 100 - remaining]
                for price in removed:
                    del exchange[side][price]
                raw[field] = [[str(p), "0"] for p in removed]
            clock.receipts.append(raw)
            return json.dumps(raw)

        yield SimpleNamespace(recv=recv)

    original_persist, original_save = live.persist_messages, depth.save_collection

    def persist(target, cid, messages):
        failing = any(json.loads(m["message"])["u"] == 103 for m in messages)
        if failing:
            observe("failure_before_event_persistence")
            clock.seconds += persistence_delay
            observe("failure_after_synthetic_persistence_delay")
        return original_persist(target, cid, messages)

    def save(target, report, state):
        first_failure = report["sync_losses"] == 1 and report["persisted_messages"] == 3
        if first_failure:
            observe("failure_before_report_commit", report)
        original_save(target, report, state)
        if first_failure:
            observe("failure_after_report_commit", report)

    client = httpx.Client(transport=httpx.MockTransport(fetch))
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(live.time, "monotonic", lambda: clock.seconds)
            patch.setattr(
                live.time, "sleep", lambda delay: setattr(clock, "seconds", clock.seconds + delay)
            )
            patch.setattr(live, "now", lambda: BASE + timedelta(seconds=clock.seconds))
            patch.setattr(depth, "now", lambda: BASE + timedelta(seconds=clock.seconds))
            patch.setattr(live, "persist_messages", persist)
            patch.setattr(depth, "save_collection", save)
            report = live.capture(
                store,
                seconds=7,
                top=20,
                flush_seconds=1,
                verify_seconds=3600,
                min_free_gib=0,
                connector=connector,
                fetcher=Fetcher(client, retries=0, sleep=lambda _: None),
                progress=lambda _: observe("after_flush"),
            )
            replay = depth.replay(store)
            assert any(o["healthy"] and o["phase"] == "after_flush" for o in observations), (
                "A healthy persisted baseline is required before injecting the failure"
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = cli(["--root", str(root), "depth-replay"])
            assert json.loads(output.getvalue()) == json.loads(json_bytes(replay))
            result = {
                "remaining": remaining,
                "synthetic_persistence_delay_seconds": persistence_delay,
                "observations": observations,
                "final_state": report["state"],
                "complete": report["complete"],
                "sync_losses": report["sync_losses"],
                "replay_valid": replay["valid"],
                "replay_matches_live": replay["replay_matches_live"],
                "replay_cli_exit": exit_code,
                "messages": report["messages"],
                "persisted_messages": report["persisted_messages"],
                "snapshots": clock.snapshots,
                "final_book": report["epochs"][-1]["final"]["book"],
            }
            return json.loads(json_bytes(result))
    finally:
        client.close()
        store.close()


def main():
    with tempfile.TemporaryDirectory(prefix="depth-consumer-probe-") as directory:
        results = [probe(Path(directory) / str(n), n) for n in (0, 8)]
    with (OUT / "consumer-results.json").open("x") as handle:
        json.dump(results, handle, indent=2)
        handle.write("\n")
    for result in results:
        print(
            json.dumps(
                {
                    "remaining": result["remaining"],
                    "final_state": result["final_state"],
                    "replay_valid": result["replay_valid"],
                    "observations": [
                        {
                            k: r[k]
                            for k in (
                                "phase",
                                "synthetic_seconds",
                                "healthy",
                                "sync_losses",
                                "in_memory_sync_losses",
                            )
                        }
                        for r in result["observations"]
                    ],
                }
            )
        )


if __name__ == "__main__":
    main()
