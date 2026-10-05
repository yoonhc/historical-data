"""Isolated real collector with synthetic exchange I/O and observable write faults."""

import argparse
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from marketdata import depth
from marketdata import depth_stream as live
from marketdata.binance import Fetcher
from marketdata.domain import json_bytes, micros, utc
from marketdata.store import Store

BASE = micros(utc("2024-01-01")) // 1000


def saved(root):
    with sqlite3.connect((root / "state/catalog.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        result = depth.status(SimpleNamespace(db=db))
    return result


class SQLFault:
    """Preserve actual SQLite transactions; inject only selected UPDATE failures."""

    def __init__(self, connection, rig):
        self.connection, self.rig = connection, rig

    def __getattr__(self, key):
        return getattr(self.connection, key)

    def __enter__(self):
        self.writing_failure = False
        self.connection.__enter__()
        return self

    def __exit__(self, *args):
        if args[0] is None and self.writing_failure:
            self.rig.counts["commit"] += 1
            if self.rig.should_fail("commit"):
                self.connection.rollback()
                self.rig.note("db_commit_rejected")
                raise sqlite3.OperationalError("injected failure status commit error")
        return self.connection.__exit__(*args)

    def execute(self, sql, parameters=()):
        if sql.startswith("UPDATE event_collections SET state=?,updated_at=?,report=?"):
            report = json.loads(parameters[2])
            if "failure_notice" in report:
                self.writing_failure = True
                self.rig.counts["db"] += 1
                if self.rig.should_fail("db"):
                    self.rig.note("db_write_rejected")
                    raise sqlite3.OperationalError("injected failure status DB error")
        return self.connection.execute(sql, parameters)


class Rig:
    def __init__(self, root, remaining, mode="normal", persist_hook=None):
        self.root, self.remaining, self.mode = root, remaining, mode
        self.persist_hook = persist_hook
        self.clock, self.identity, self.snapshots = 0, 100, 0
        self.counts = {"db": 0, "json": 0, "raw": 0, "commit": 0}
        self.events, self.baseline, self.error, self.report = [], None, None, None
        self.bids = {p: "1" for p in range(600, 1000)}
        self.asks = {p: "1" for p in range(1001, 1501)}
        self.store = Store(root)
        self.store.db = SQLFault(self.store.db, self)

    def note(self, phase):
        value = saved(self.root)
        self.events.append({"phase": phase, **value})
        return value

    def should_fail(self, operation):
        if self.mode == "json_after_flush_once" and operation == "json":
            return self.counts[operation] == 2
        mode = self.mode.removeprefix("healthy_")
        return mode == operation + "_always" or (
            mode == operation + "_once" and self.counts[operation] == 1
        )

    def fetch(self, request):
        self.snapshots += 1
        if self.snapshots > 1:
            self.note("before_recovery_snapshot")
        return httpx.Response(
            200,
            json={
                "lastUpdateId": self.identity,
                "T": BASE + self.identity,
                "E": BASE + self.identity + 1,
                "bids": [[str(p), self.bids[p]] for p in sorted(self.bids, reverse=True)[:100]],
                "asks": [[str(p), self.asks[p]] for p in sorted(self.asks)[:100]],
            },
        )

    @contextmanager
    def connect(self, *args, **kwargs):
        def recv(timeout):
            self.clock += 1
            previous = self.identity
            self.identity += 1
            raw = {
                "e": "depthUpdate",
                "s": "BTCUSDT",
                "st": 1,
                "U": 100 if self.identity == 101 else self.identity,
                "u": self.identity,
                "pu": previous,
                "T": BASE + self.identity,
                "E": BASE + self.identity + 1,
                "b": [],
                "a": [],
            }
            failure_ids = (103, 105) if self.mode == "repeat_shock" else (103,)
            if self.identity in failure_ids and self.mode != "no_shock":
                self.baseline = self.note("healthy_before_failure")
                assert self.baseline["healthy"] is (self.identity == 103)
                if not self.mode.startswith("healthy_"):
                    book, field = (self.bids, "b") if self.remaining == 0 else (self.asks, "a")
                    removed = sorted(book, reverse=field == "b")[: 100 - self.remaining]
                    for price in removed:
                        del book[price]
                    raw[field] = [[str(p), "0"] for p in removed]
            return json.dumps(raw)

        yield SimpleNamespace(recv=recv)

    def run(self):
        original_persist, original_write = live.persist_messages, depth.atomic_write

        def persist(store, cid, messages):
            if (
                any(json.loads(m["message"])["u"] == 103 for m in messages)
                and self.mode != "no_shock"
            ):
                self.counts["raw"] += 1
                self.note("before_failed_event_persistence")
                if self.persist_hook is not None:
                    self.persist_hook(self)
                if self.should_fail("raw"):
                    raise OSError("injected raw persistence error")
            return original_persist(store, cid, messages)

        def write(path, blob):
            report = json.loads(blob) if path.name == "depth-latest.json" else {}
            if "failure_notice" in report or (
                self.mode.startswith("healthy_") and report.get("messages", 0) >= 3
            ):
                self.counts["json"] += 1
                self.note("db_committed_before_json")
                if self.should_fail("json"):
                    raise OSError("injected failure status JSON error")
            return original_write(path, blob)

        client = httpx.Client(transport=httpx.MockTransport(self.fetch))
        try:
            with pytest.MonkeyPatch.context() as patch:
                # Only the collector's duration clock is synthetic. Wall-clock
                # receipts remain real so an unmodified external CLI can query.
                patch.setattr(
                    live,
                    "time",
                    SimpleNamespace(
                        monotonic=lambda: self.clock,
                        sleep=lambda seconds: setattr(self, "clock", self.clock + seconds),
                    ),
                )
                patch.setattr(live, "persist_messages", persist)
                patch.setattr(depth, "atomic_write", write)
                try:
                    self.report = live.capture(
                        self.store,
                        seconds=7,
                        flush_seconds=1,
                        top=20,
                        verify_seconds=3600,
                        min_free_gib=0,
                        connector=self.connect,
                        fetcher=Fetcher(client, retries=0, sleep=lambda _: None),
                    )
                except Exception as exc:
                    self.error = f"{type(exc).__name__}: {exc}"
            final = saved(self.root)
            persisted = sum(
                row["kind"] == "update"
                for segment in self.store.db.execute(
                    "SELECT * FROM event_segments WHERE collection_id=?", (final["report"]["id"],)
                )
                for row in self.store._revision_table(segment, columns=["kind"]).to_pylist()
            )
            replay = depth.replay(self.store)
            latest = json.loads((self.root / "state/depth-latest.json").read_bytes())
            return json.loads(
                json_bytes(
                    {
                        "remaining": self.remaining,
                        "mode": self.mode,
                        "error": self.error,
                        "events": self.events,
                        "final": final,
                        "latest_json": latest,
                        "counts": self.counts,
                        "update_rows": persisted,
                        "replay": replay,
                    }
                )
            )
        finally:
            client.close()
            self.store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--remaining", type=int, choices=(0, 8), required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()

    def pause(rig):
        print(json.dumps({"blocked_before_persistence": True, "root": str(rig.root)}), flush=True)
        assert sys.stdin.readline().strip() == "resume"

    result = Rig(args.root, args.remaining, persist_hook=pause).run()
    args.result.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
