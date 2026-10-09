"""Versioned sampled clock evidence, using the existing immutable raw catalog.

Only committed chunks are backup inputs. Missing chunks, open tails and absent
collection links are unknown, never evidence of good local receive timestamps.
"""

import gzip
import json
import os
import re
import shutil
import sqlite3
import time
from contextlib import closing, nullcontext
from pathlib import Path

from .domain import DataError, json_bytes, sha256

KIND = "runtime_clock_v2"
POLICY = "reference-freshness-v2"
MAX_CHUNK = 1024 * 1024
MAX_RECORD = 64 * 1024
CHUNK_SECONDS = 300


def sample():
    before = time.monotonic_ns()
    wall = time.time_ns()
    boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    after = time.monotonic_ns()
    return {
        "wall_ns": wall,
        "monotonic_ns": (before + after) // 2,
        "boottime_ns": boot,
        "sampling_span_ns": after - before,
    }


def collection_context(kind):
    value = os.environ.get("MARKETDATA_CLOCK_CONTEXT")
    if kind != "websocket" or value is None:
        return {}
    try:
        context = json.loads(value)
        if (
            set(context) != {"schema", "run_id", "child_id", "boot_id", "stream", "policy_version"}
            or context["schema"] != 2
            or context["policy_version"] != POLICY
            or not re.fullmatch(r"[0-9a-f]{32}", context["run_id"])
            or not re.fullmatch(r"[0-9a-f]{32}", context["child_id"])
            or not re.fullmatch(r"[0-9a-f-]{36}", context["boot_id"])
            or context["stream"] not in {"depth", "aggtrade"}
        ):
            raise ValueError("invalid runtime context")
    except (ValueError, TypeError, KeyError) as exc:
        raise DataError("Invalid clock evidence context") from exc
    return {"runtime_clock": {**context, "collection_start": sample()}}


def finish_context(report, state):
    if "runtime_clock" in report and state != "running":
        report["runtime_clock"] = {
            **report["runtime_clock"],
            "collection_end": sample(),
            "terminal_state": state,
        }


def decode_chunk(blob):
    try:
        raw = gzip.decompress(blob)
        if len(raw) > MAX_CHUNK + MAX_RECORD:
            raise ValueError("oversized chunk")
        boundary = raw.rfind(b"\n") + 1
        rows = [json.loads(line) for line in raw[:boundary].splitlines()]
        header, records = rows[0], rows[1:]
        if header["schema"] != 2 or header["kind"] != KIND or not records:
            raise ValueError("invalid chunk header/empty chunk")
        if not re.fullmatch(r"[0-9a-f]{32}", header["run_id"]):
            raise ValueError("invalid run ID")
        if type(header["chunk"]) is not int or header["chunk"] < 0:
            raise ValueError("invalid chunk index")
        for row in records:
            if (
                row["run_id"] != header["run_id"]
                or row["schema"] != 2
                or row["policy_version"] != POLICY
            ):
                raise ValueError("record/chunk identity mismatch")
        return header, records, raw[boundary:]
    except (OSError, ValueError, IndexError, KeyError, TypeError) as exc:
        raise DataError("Invalid clock evidence chunk") from exc


def publish_chunk(store, path):
    """Idempotent commit; leave spool bytes intact on any failure."""
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > MAX_CHUNK + MAX_RECORD:
        raise DataError("Unsafe/oversized clock spool")
    raw = path.read_bytes()
    blob = gzip.compress(raw, mtime=0)
    header, records, tail = decode_chunk(blob)
    url = f"runtime-clock://{header['run_id']}/{header['chunk']}"
    identity = sha256(blob)
    with store.writer(timeout=60):
        previous = store.db.execute(
            "SELECT artifact_id FROM fetches WHERE url=?", (url,)
        ).fetchall()
        if previous:
            if len(previous) != 1 or previous[0][0] != identity or store.raw(identity)[0] != blob:
                raise DataError("Conflicting clock evidence retry")
        else:
            store.put_raw(
                blob,
                url,
                KIND,
                metadata={
                    **header,
                    "records": len(records),
                    "partial_tail_bytes": len(tail),
                    "uncompressed_sha256": sha256(raw),
                    "uncompressed_bytes": len(raw),
                    "first_sample": records[0]["sample"],
                    "last_sample": records[-1]["sample"],
                },
            )
    # Delete only the byte-identical staging duplicate AFTER its immutable copy
    # and catalog link have committed. Never delete historical runtime JSONL.
    if path.read_bytes() != raw:
        raise DataError("Clock spool changed during publication")
    path.unlink()
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return identity


class Journal:
    """One bounded active chunk per supervisor; caller holds its stream lock."""

    def __init__(self, store, root, base, *, min_free_bytes=10 * 1024**3, publisher=publish_chunk):
        self.store, self.root, self.base = store, Path(root), base
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.minimum = min_free_bytes
        self.publisher = publisher
        # Recovery runs before any child starts. A damaged/header-only spool is
        # retained and blocks startup; it is not silently discarded.
        for path in sorted(self.root.glob("*.jsonl")):
            publish_chunk(store, path)
        self.chunk, self.previous, self.path, self.since = 0, None, None, None
        self.index = 0

    def write(self, state, *, sample_value=None, **extra):
        row = {
            **self.base,
            "schema": 2,
            "policy_version": POLICY,
            "record": self.index,
            "state": state,
            "sample": sample_value or sample(),
            **extra,
        }
        encoded = json_bytes(row) + b"\n"
        if len(encoded) > MAX_RECORD:
            raise DataError("Clock record exceeds bound")
        if shutil.disk_usage(self.root).free < self.minimum + MAX_CHUNK + MAX_RECORD:
            raise DataError("Clock evidence capacity reserve exhausted")
        tick = time.monotonic()
        if self.path is not None and (
            self.path.stat().st_size + len(encoded) > MAX_CHUNK
            or tick - self.since >= CHUNK_SECONDS
        ):
            self.flush()
        if self.path is None:
            self.path = self.root / f"{self.base['run_id']}.{self.chunk:08d}.jsonl"
            header = {
                "schema": 2,
                "kind": KIND,
                "run_id": self.base["run_id"],
                "chunk": self.chunk,
                "previous": self.previous,
            }
            # Header and first record become one recoverable append operation.
            with self.path.open("xb") as f:
                f.write(json_bytes(header) + b"\n" + encoded)
                f.flush()
                os.fsync(f.fileno())
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            self.since = tick
        else:
            with self.path.open("ab") as f:
                f.write(encoded)
                f.flush()
                os.fsync(f.fileno())
        self.index += 1
        return row

    def flush(self):
        if self.path is not None:
            self.previous = self.publisher(self.store, self.path)
            self.path, self.since = None, None
            self.chunk += 1


def query(store, collection_id):
    """Return sampled intervals, independently of market/sequence quality."""
    entry = store.db.execute(
        "SELECT report FROM event_collections WHERE id=?", (collection_id,)
    ).fetchone()
    if entry is None:
        raise DataError("Unknown collection")
    link = json.loads(entry[0]).get("runtime_clock")
    result = {
        "collection_id": collection_id,
        "evidence_state": "unknown",
        "scope": "sampled_runtime_clock_not_per_event_accuracy",
        "link": link,
        "chunks": [],
        "intervals": [],
        "unknown_reasons": [],
    }
    if link is None:
        result["unknown_reasons"].append("collection_has_no_runtime_link")
        return result
    prefix = f"runtime-clock://{link['run_id']}/"
    chunks = []
    for row in store.db.execute(
        "SELECT artifact_id, metadata FROM fetches WHERE url>=? AND url<?",
        (prefix, prefix + "\uffff"),
    ):
        metadata = json.loads(row[1])
        chunks.append((metadata["chunk"], row[0], metadata))
    chunks.sort(key=lambda c: c[0])
    previous, record_index, last = None, 0, None
    start = link["collection_start"]["boottime_ns"]
    end = link.get("collection_end", {}).get("boottime_ns")
    before = [n for n, _, m in chunks if m["last_sample"]["boottime_ns"] < start]
    after = [n for n, _, m in chunks if end is not None and m["first_sample"]["boottime_ns"] > end]
    lower = before[-1] if before else 0
    upper = after[0] if after else len(chunks) - 1
    for n, (number, identity, metadata) in enumerate(chunks):
        if number != n or metadata["previous"] != previous or metadata["run_id"] != link["run_id"]:
            raise DataError("Missing/conflicting clock evidence chunk chain")
        previous = identity
        if not lower <= n <= upper:
            record_index += metadata["records"]
            continue
        result["chunks"].append(identity)
        blob = store.raw(identity)[0]
        header, records, tail = decode_chunk(blob)
        if (
            any(header[k] != metadata[k] for k in header)
            or len(records) != metadata["records"]
            or records[0]["sample"] != metadata["first_sample"]
            or records[-1]["sample"] != metadata["last_sample"]
        ):
            raise DataError("Clock evidence metadata mismatch")
        if tail:
            result["unknown_reasons"].append("truncated_uncommitted_record")
        for r in records:
            if (
                r["record"] != record_index
                or r["boot_id"] != link["boot_id"]
                or r["policy_version"] != link["policy_version"]
                or r["stream"] != link["stream"]
            ):
                raise DataError("Clock evidence identity/sequence mismatch")
            record_index += 1
            b = r["sample"]["boottime_ns"]
            if last is not None and b < last["sample"]["boottime_ns"]:
                raise DataError("Clock evidence boottime reversed")
            if last is not None:
                a = last["sample"]["boottime_ns"]
                if b > start and (end is None or a < end):
                    earlier = last.get("decision", {})
                    verdict = r.get("decision", {})
                    # A later good observation cannot certify the preceding
                    # uncertain interval. Keep both endpoints for interpretation.
                    stale = (earlier.get("reference_stale"), verdict.get("reference_stale"))
                    result["intervals"].append(
                        {
                            "start_boottime_ns": max(a, start),
                            "end_boottime_ns": min(b, end) if end is not None else b,
                            "sample_before": last["sample"],
                            "sample_after": r["sample"],
                            "current_estimate_usable": bool(
                                earlier.get("current_estimate_usable")
                                and verdict.get("current_estimate_usable")
                            ),
                            "reference_stale": True if True in stale else (
                                False if stale == (False, False) else None
                            ),
                            "raw_start_allowed": bool(
                                earlier.get("raw_start_allowed") and verdict.get("raw_start_allowed")
                            ),
                            "reasons": sorted(set(
                                earlier.get("reasons", ["no_clock_decision"])
                                + verdict.get("reasons", ["no_clock_decision"])
                            )),
                            "sample_before_decision": earlier,
                            "sample_after_decision": verdict,
                            "history": r.get("history"),
                            "provider": r.get("clock", {}).get("provider"),
                            "clock_observation": r.get("clock"),
                        }
                    )
            last = r
    intervals = result["intervals"]
    if not intervals or intervals[0]["start_boottime_ns"] != start:
        result["unknown_reasons"].append("start_not_covered_by_committed_evidence")
    if end is None:
        result["unknown_reasons"].append("collection_end_unknown")
    elif not intervals or intervals[-1]["end_boottime_ns"] != end:
        result["unknown_reasons"].append("end_not_covered_by_committed_evidence")
    if not result["unknown_reasons"]:
        result["evidence_state"] = "available"
    result["note"] = "Available means evidence exists, not that its clock or market data is good"
    return result


def query_root(root, collection_id):
    from .checkpoint_locks import checkpoint_locks
    from .fileio import layout_lock, packed_record, read_packed, safe_file

    root = Path(root).resolve()
    checkpoint = (root / "backup.json").is_file()
    catalog = root / "state/catalog.sqlite"
    uri = catalog.as_uri() + "?mode=ro" + ("&immutable=1" if checkpoint else "")
    with (
        checkpoint_locks([root]) if checkpoint else nullcontext(),
        nullcontext() if checkpoint else layout_lock(root),
        closing(sqlite3.connect(uri, uri=True)) as db,
    ):
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")

        class Reader:
            def raw(self, identity):
                r = db.execute("SELECT path FROM artifacts WHERE id=?", (identity,)).fetchone()
                if r is None:
                    raise DataError("Missing clock artifact")
                pack = packed_record(db, r[0])
                blob = read_packed(root, pack) if pack else safe_file(root, r[0]).read_bytes()
                if sha256(blob) != identity:
                    raise DataError("Clock artifact hash mismatch")
                return blob, None

        reader = Reader()
        reader.db = db
        return query(reader, collection_id)
