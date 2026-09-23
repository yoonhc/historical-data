"""Immutable raw/Parquet files and a transactional local publication catalog."""

import fcntl
import json
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .domain import (
    PARSER_VERSION,
    POLICY_VERSION,
    ConflictError,
    DataError,
    WriterBusy,
    day_ranges,
    json_bytes,
    micros,
    now,
    sha256,
    utc,
)
from .fileio import packed_record, read_file
from .schema import economic_row, row_key, schema


def atomic_write(path: Path, blob: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".staging-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        (self.root / "state").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "state/catalog.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, path TEXT NOT NULL, kind TEXT NOT NULL,
                observed_at TEXT NOT NULL, bytes INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS fetches (
                id INTEGER PRIMARY KEY, artifact_id TEXT REFERENCES artifacts(id),
                url TEXT NOT NULL, observed_at TEXT NOT NULL, metadata TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS fetches_observed ON fetches(observed_at);
            CREATE TABLE IF NOT EXISTS revisions (
                id TEXT PRIMARY KEY, key_id TEXT NOT NULL, key_json TEXT NOT NULL,
                day TEXT NOT NULL, path TEXT NOT NULL, file_sha TEXT NOT NULL,
                row_count INTEGER NOT NULL, quality TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS active (
                key_id TEXT NOT NULL, day TEXT NOT NULL, revision_id TEXT REFERENCES revisions(id),
                PRIMARY KEY (key_id, day));
            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY, key_id TEXT, day TEXT, source_id TEXT,
                kind TEXT NOT NULL, blocking INTEGER NOT NULL, details TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS queries (
                id INTEGER PRIMARY KEY, key_id TEXT, start TEXT, end TEXT, source_id TEXT,
                UNIQUE(key_id, start, end, source_id));
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, key_id TEXT, start TEXT, end TEXT, state TEXT,
                source_id TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 1, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY, manifest TEXT NOT NULL, path TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sync_targets (
                key_id TEXT PRIMARY KEY, key_json TEXT NOT NULL, start TEXT NOT NULL,
                policy TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sync_runs (
                id TEXT PRIMARY KEY, key_id TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
                started_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT,
                state TEXT NOT NULL, report TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS sync_runs_key_time ON sync_runs(key_id, started_at);
            CREATE TABLE IF NOT EXISTS sync_coverage (
                revision_id TEXT PRIMARY KEY REFERENCES revisions(id), coverage TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS event_collections (
                id TEXT PRIMARY KEY, dataset TEXT NOT NULL, kind TEXT NOT NULL,
                started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                state TEXT NOT NULL, report TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS event_segments (
                id TEXT PRIMARY KEY, collection_id TEXT NOT NULL REFERENCES event_collections(id),
                dataset TEXT NOT NULL, instrument_id TEXT NOT NULL, kind TEXT NOT NULL,
                path TEXT NOT NULL, file_sha TEXT NOT NULL,
                source_id TEXT NOT NULL REFERENCES artifacts(id),
                first_time TEXT NOT NULL, last_time TEXT NOT NULL,
                first_id INTEGER NOT NULL, last_id INTEGER NOT NULL, row_count INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS event_segments_time
                ON event_segments(dataset, instrument_id, first_time, last_time);
            CREATE TABLE IF NOT EXISTS file_packs (
                id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL,
                members INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS packed_files (
                path TEXT PRIMARY KEY, pack_id TEXT NOT NULL REFERENCES file_packs(id),
                sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, kind TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS packed_files_pack ON packed_files(pack_id);
        """)
        # Logical queries may keep an older SQLite read snapshot. Physical lookup
        # must always see the latest committed layout after a pack/prune operation.
        catalog_uri = (self.root / "state/catalog.sqlite").as_uri()
        self._files_db = sqlite3.connect(f"{catalog_uri}?mode=ro", uri=True, isolation_level=None)
        self._files_db.row_factory = sqlite3.Row

    def close(self):
        self._files_db.close()
        self.db.close()

    @contextmanager
    def writer(self, *, timeout=0):
        with (self.root / "state/writer.lock").open("a") as lock:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise WriterBusy("Another writer owns this store; retry later") from exc
                    time.sleep(min(0.1, remaining))
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def put_raw(self, blob, url, kind, observed=None, metadata=None):
        observed = utc(observed) if observed else now()
        identity = sha256(blob)
        relative = f"data/raw/{identity[:2]}/{identity}/payload"
        path = self.root / relative
        if path.exists() or packed_record(self._files_db, relative):
            if sha256(self.file_bytes(relative)) != identity:
                raise DataError("Corrupted raw content-addressed file")
        else:
            atomic_write(path, blob)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO artifacts VALUES (?,?,?,?,?)",
                (identity, relative, kind, observed.isoformat(), len(blob)),
            )
            self.db.execute(
                "INSERT INTO fetches(artifact_id,url,observed_at,metadata) VALUES(?,?,?,?)",
                (identity, url, observed.isoformat(), json_bytes(metadata or {}).decode()),
            )
        return identity

    def raw(self, source_id):
        record = self.db.execute("SELECT * FROM artifacts WHERE id=?", (source_id,)).fetchone()
        if record is None:
            raise DataError("Unknown raw artifact")
        blob = self.file_bytes(record["path"])
        if sha256(blob) != source_id:
            raise DataError("Raw artifact hash mismatch")
        return blob, utc(record["observed_at"])

    def record_fetch(self, identity, url, observed, metadata):
        with self.db:
            self.db.execute(
                "INSERT INTO fetches(artifact_id,url,observed_at,metadata) VALUES(?,?,?,?)",
                (identity, url, utc(observed).isoformat(), json_bytes(metadata).decode()),
            )

    def job(self, job_id, key, start, end, state, source_id=None, error=None):
        with self.db:
            self.db.execute(
                """INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET state=excluded.state, source_id=excluded.source_id,
                error=excluded.error, attempts=jobs.attempts + CASE WHEN excluded.state='running'
                THEN 1 ELSE 0 END, updated_at=excluded.updated_at""",
                (
                    job_id,
                    key.id,
                    utc(start).isoformat(),
                    utc(end).isoformat(),
                    state,
                    source_id,
                    error,
                    1,
                    now().isoformat(),
                ),
            )

    def issue(self, key, day, source_id, kind, details, blocking=True):
        with self.db:
            serialized = json_bytes(details).decode()
            existing = self.db.execute(
                """SELECT id FROM issues WHERE key_id=? AND day IS ? AND source_id IS ?
                AND kind=? AND blocking=? AND details=? AND resolved=0""",
                (key.id, day, source_id, kind, int(blocking), serialized),
            ).fetchone()
            if existing:
                return
            self.db.execute(
                """INSERT INTO issues(key_id,day,source_id,kind,blocking,details,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    key.id,
                    day,
                    source_id,
                    kind,
                    int(blocking),
                    serialized,
                    now().isoformat(),
                ),
            )

    def _revision_table(self, record, *, columns=None):
        path = self.root / record["path"]
        blob = self.file_bytes(record["path"])
        if sha256(blob) != record["file_sha"]:
            raise DataError(f"Canonical hash mismatch: {path}")
        return pq.ParquetFile(pa.BufferReader(blob)).read(columns=columns)

    def file_bytes(self, relative):
        return read_file(self.root, self._files_db, relative)

    def file_info(self, relative):
        from .fileio import layout_lock, safe_file

        with layout_lock(self.root):
            packed = packed_record(self._files_db, relative)
            if packed:
                return {
                    "bytes": packed["bytes"],
                    "pack_id": packed["pack_id"],
                    "allocated_bytes": None,
                }
            stat = safe_file(self.root, relative).stat()
            return {"bytes": stat.st_size, "pack_id": None, "allocated_bytes": stat.st_blocks * 512}

    def file_exists(self, relative):
        return bool(packed_record(self._files_db, relative)) or (self.root / relative).exists()

    def _revision_rows(self, record):
        return self._revision_table(record).to_pylist()

    def publish(
        self, key, batch, start, end, source_id, *, accept_revisions=False, authoritative=False
    ):
        start, end = utc(start), utc(end)
        results = []
        for begin, stop in day_ranges(start, end):
            day = begin.date().isoformat()
            incoming = [r for r in batch.rows if begin <= r["timestamp"] < stop]
            zero = any(
                "zero_oi" == i["kind"] and begin <= utc(i["timestamp"]) < stop for i in batch.issues
            )
            if zero:
                self.issue(key, day, source_id, "quarantined_zero_oi", batch.issues)
                results.append({"date": day, "state": "quarantined", "rows": len(incoming)})
                continue
            current = self.db.execute(
                """SELECT r.* FROM revisions r JOIN active a
                ON a.revision_id=r.id WHERE a.key_id=? AND a.day=?""",
                (key.id, day),
            ).fetchone()
            old = self._revision_rows(current) if current else []
            old_map = {row_key(r, key): r for r in old}
            # An authoritative subrange cannot erase the rest of the UTC day.
            new_map = {
                natural: row
                for natural, row in old_map.items()
                if not authoritative or not begin <= row["timestamp"] < stop
            }
            changes = []
            for row in incoming:
                natural = row_key(row, key)
                previous = old_map.get(natural)
                if previous and economic_row(previous) == economic_row(row):
                    new_map[natural] = previous
                else:
                    if previous:
                        changes.append(str(natural))
                    new_map[natural] = row
            removed = set(old_map) - set(new_map)
            if (changes or removed) and not accept_revisions:
                self.issue(
                    key,
                    day,
                    source_id,
                    "conflict",
                    {
                        "start": begin,
                        "end": stop,
                        "changed": changes,
                        "removed": [str(k) for k in removed],
                    },
                )
                raise ConflictError(f"Conflicting revision for {key.id} {day}; raw retained")
            rows = sorted(new_map.values(), key=lambda r: row_key(r, key))
            if old == rows and current:
                revision_id = current["id"]
            else:
                content = {
                    "key": key.asdict(),
                    "day": day,
                    "rows": rows,
                    "schema": 1,
                    "parser": PARSER_VERSION,
                    "policy": POLICY_VERSION,
                }
                revision_id = sha256(json_bytes(content))
                relative = f"data/canonical/{key.dataset}/{sha256(key.id.encode())[:16]}/{day}/{revision_id}.parquet"
                table = pa.Table.from_pylist(rows, schema=schema(key))
                sink = pa.BufferOutputStream()
                pq.write_table(table, sink, compression="zstd")
                blob = sink.getvalue().to_pybytes()
                path = self.root / relative
                if not path.exists():
                    atomic_write(path, blob)
                elif sha256(path.read_bytes()) != sha256(blob):
                    raise DataError("Existing revision has different Parquet bytes")
                quality = {
                    "issues": batch.issues,
                    "source_id": source_id,
                    "accepted_revision": bool(changes or removed),
                }
                with self.db:
                    self.db.execute(
                        "INSERT OR IGNORE INTO revisions VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            revision_id,
                            key.id,
                            json_bytes(key.asdict()).decode(),
                            day,
                            relative,
                            sha256(blob),
                            len(rows),
                            json_bytes(quality).decode(),
                            now().isoformat(),
                        ),
                    )
                    self.db.execute(
                        """INSERT INTO active VALUES(?,?,?) ON CONFLICT(key_id,day)
                                    DO UPDATE SET revision_id=excluded.revision_id""",
                        (key.id, day, revision_id),
                    )
            with self.db:
                if accept_revisions:
                    issues = self.db.execute(
                        "SELECT * FROM issues WHERE key_id=? AND day=? AND resolved=0",
                        (key.id, day),
                    ).fetchall()
                    full_clean_day = (
                        authoritative
                        and begin == utc(day)
                        and stop == utc(day) + timedelta(days=1)
                        and key.step
                        and len(incoming) == 86400 // key.step
                    )
                    for issue in issues:
                        details = json.loads(issue["details"])
                        covered_conflict = (
                            issue["kind"] == "conflict"
                            and "start" in details
                            and begin <= utc(details["start"])
                            and utc(details["end"]) <= stop
                        )
                        if covered_conflict or full_clean_day:
                            self.db.execute(
                                "UPDATE issues SET resolved=1 WHERE id=?", (issue["id"],)
                            )
                if batch.query_complete:
                    self.db.execute(
                        "INSERT OR IGNORE INTO queries(key_id,start,end,source_id) VALUES(?,?,?,?)",
                        (key.id, begin.isoformat(), stop.isoformat(), source_id),
                    )
            results.append(
                {"date": day, "state": "published", "rows": len(rows), "revision": revision_id}
            )
        return results

    def revision_list(self, key, start, end, snapshot_id=None):
        start, end = utc(start), utc(end)
        if snapshot_id:
            manifest = self.snapshot_manifest(snapshot_id)
            if not utc(manifest["start"]) <= start < end <= utc(manifest["end"]):
                raise DataError("Requested range is outside snapshot")
            if key.id not in manifest["keys"]:
                raise DataError("Dataset absent from snapshot")
            records = [r for r in manifest["revisions"] if r["key_id"] == key.id]
        else:
            records = [
                dict(r)
                for r in self.db.execute(
                    """SELECT r.* FROM revisions r
                JOIN active a ON a.revision_id=r.id WHERE a.key_id=? ORDER BY r.day""",
                    (key.id,),
                )
            ]
        return [
            r for r in records if utc(r["day"]) < end and utc(r["day"]) + timedelta(days=1) > start
        ]

    def iter_tables(self, key, start, end, snapshot_id=None):
        """Yield verified daily Arrow tables from the selected immutable revisions."""
        start, end = utc(start), utc(end)
        if start >= end:
            raise DataError("Expected start < end")
        records = self.revision_list(key, start, end, snapshot_id)
        for record in sorted(records, key=lambda r: r["day"]):
            table = self._revision_table(record)
            mask = pc.and_(
                pc.greater_equal(table["timestamp"], pa.scalar(start)),
                pc.less(table["timestamp"], pa.scalar(end)),
            )
            yield table.filter(mask).sort_by([("timestamp", "ascending")])

    def read(self, key, start, end, snapshot_id=None):
        tables = list(self.iter_tables(key, start, end, snapshot_id))
        table = pa.concat_tables(tables) if tables else pa.Table.from_pylist([], schema=schema(key))
        return table.sort_by([("timestamp", "ascending")])

    def coverage(self, key, start, end):
        start, end = utc(start), utc(end)
        if start >= end:
            raise DataError("Expected start < end")
        if key.step and (
            micros(start) % (key.step * 1_000_000) or micros(end) % (key.step * 1_000_000)
        ):
            raise DataError("Coverage bounds must align with dataset interval")
        records = {r["day"]: r for r in self.revision_list(key, start, end)}
        row_count, missing_count, gaps = 0, 0, []
        for begin, stop in day_ranges(start, end):
            record = records.get(begin.date().isoformat())
            timestamps = (
                self._revision_table(record, columns=["timestamp"])["timestamp"]
                .cast(pa.int64())
                .to_pylist()
                if record
                else []
            )
            first, last = micros(begin), micros(stop)
            stamps = [stamp for stamp in timestamps if first <= stamp < last]
            row_count += len(stamps)
            if key.step:
                step = key.step * 1_000_000
                missing = sorted(set(range(first, last, step)) - set(stamps))
                missing_count += len(missing)
                for value in missing:
                    if gaps and gaps[-1][1] == value:
                        gaps[-1][1] += step
                    else:
                        gaps.append([value, value + step])
        blocking = [
            dict(r)
            for r in self.db.execute(
                """SELECT * FROM issues WHERE key_id=?
            AND blocking=1 AND resolved=0 AND day>=? AND day<=?""",
                (
                    key.id,
                    start.date().isoformat(),
                    (end - timedelta(microseconds=1)).date().isoformat(),
                ),
            )
        ]
        if key.step:
            step = key.step * 1_000_000
            return {
                "key": key.asdict(),
                "rows": row_count,
                "expected": (micros(end) - micros(start)) // step,
                "missing": missing_count,
                "gap_ranges_us": gaps,
                "blocking_issues": blocking,
                "complete": not missing_count and not blocking,
                "semantics": "source_label_grid_only"
                if key.dataset == "open_interest"
                else "bar_grid",
            }
        intervals = sorted(
            (utc(r["start"]), utc(r["end"]))
            for r in self.db.execute("SELECT start,end FROM queries WHERE key_id=?", (key.id,))
        )
        cursor = start
        for a, b in intervals:
            if a <= cursor:
                cursor = max(cursor, b)
        return {
            "key": key.asdict(),
            "rows": row_count,
            "query_complete": cursor >= end,
            "complete": cursor >= end and not blocking,
            "blocking_issues": blocking,
            "semantics": "source_query_coverage_not_proof_of_event_completeness",
        }

    def create_snapshot(self, keys, start, end, *, allow_gaps=False):
        start, end = utc(start), utc(end)
        if start >= end or len({k.id for k in keys}) != len(keys) or not keys:
            raise DataError("Invalid snapshot keys/range")
        # Pin the catalog read transaction while resolving all input datasets.
        with self.db:
            self.db.execute("BEGIN")
            coverages = [self.coverage(k, start, end) for k in keys]
            if not allow_gaps and any(not c["complete"] for c in coverages):
                raise DataError("Snapshot has gaps or blocking issues; inspect coverage")
            revisions = [r for k in keys for r in self.revision_list(k, start, end)]
            manifest = {
                "format_version": 1,
                "start": start,
                "end": end,
                "keys": {k.id: k.asdict() for k in keys},
                "coverage": coverages,
                "revisions": revisions,
                "allow_gaps": allow_gaps,
                "availability_mode": "historical_assumption",
                "oi_archive_clock": "unverified_no_automatic_offset",
            }
            identity = sha256(json_bytes(manifest))
            relative = f"data/snapshots/{identity}.json"
            atomic_write(self.root / relative, json_bytes(manifest))
            self.db.execute(
                "INSERT OR IGNORE INTO snapshots VALUES(?,?,?)",
                (identity, json_bytes(manifest).decode(), relative),
            )
        return identity

    def snapshot_manifest(self, identity):
        row = self.db.execute("SELECT * FROM snapshots WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise DataError("Unknown snapshot")
        blob = (self.root / row["path"]).read_bytes()
        if sha256(blob) != identity or blob.decode() != row["manifest"]:
            raise DataError("Snapshot manifest integrity failure")
        return json.loads(blob)

    def validate(self):
        failures, count = [], 0
        for record in self.db.execute("SELECT * FROM artifacts"):
            count += 1
            path = self.root / record["path"]
            try:
                valid = sha256(self.file_bytes(record["path"])) == record["id"]
            except (OSError, DataError):
                valid = False
            if not valid:
                failures.append(str(path))
        for record in self.db.execute("SELECT * FROM revisions"):
            count += 1
            try:
                self._revision_table(record)
            except (OSError, DataError) as exc:
                failures.append(str(exc))
        for record in self.db.execute("SELECT * FROM event_segments"):
            count += 1
            try:
                self._revision_table(record)
            except (OSError, DataError) as exc:
                failures.append(str(exc))
        from .packing import verify_packs

        try:
            verify_packs(self)
        except (OSError, DataError) as exc:
            failures.append(str(exc))
        return {"checked": count, "failures": failures, "ok": not failures}
