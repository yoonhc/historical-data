"""One ingestion path for backfills, updates, local imports and repairs."""

import time
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path

from .binance import Fetcher, SourceError, archive_units, archive_url, rest_url
from .domain import (
    PARSER_VERSION,
    POLICY_VERSION,
    DataError,
    Key,
    day_ranges,
    full_days,
    json_bytes,
    now,
    sha256,
    utc,
)
from .normalize import Batch, normalize_archive, normalize_rest


class Collector:
    def __init__(self, store, fetcher=None, *, cooperative=False):
        self.store = store
        self.fetcher = fetcher or Fetcher()
        self.cooperative = cooperative
        self.writer_seconds = 0.0
        self.writer_max_seconds = 0.0

    def _write(self, function, *args, **kwargs):
        with self.store.writer(timeout=60) if self.cooperative else nullcontext():
            started = time.monotonic()
            try:
                return function(*args, **kwargs)
            finally:
                elapsed = time.monotonic() - started
                self.writer_seconds += elapsed
                self.writer_max_seconds = max(self.writer_max_seconds, elapsed)

    def _publish(self, key, batch, start, end, source_id, **options):
        if not self.cooperative:
            return self.store.publish(key, batch, start, end, source_id, **options)
        output = []
        # Parsing/HTTP waits happen outside the writer. Recheck and publish one
        # UTC day at a time so streaming flushes can acquire the same writer.
        by_day = {}
        for row in batch.rows:
            by_day.setdefault(row["timestamp"].date(), []).append(row)
        for begin, stop in day_ranges(start, end):
            part = Batch(
                by_day.get(begin.date(), []), batch.issues, batch.quarantine, batch.query_complete
            )
            output.extend(
                self._write(self.store.publish, key, part, begin, stop, source_id, **options)
            )
            time.sleep(0.01)
        return output

    def _save_response(self, response, kind):
        return self._write(
            self.store.put_raw,
            response.body,
            response.url,
            kind,
            response.received_at,
            {"headers": response.headers, "status": 200},
        )

    def import_archive(self, path, key, start, end, checksum=None, accept_revisions=False):
        path = Path(path).resolve()
        checksum = Path(checksum) if checksum else Path(str(path) + ".CHECKSUM")
        blob = path.read_bytes()
        check = checksum.read_bytes()
        self._verify_checksum(blob, check, path.name)
        check_id = self._write(self.store.put_raw, check, checksum.as_uri(), "checksum")
        identity = self._write(
            self.store.put_raw, blob, path.as_uri(), "archive", metadata={"checksum_id": check_id}
        )
        _, observed = self.store.raw(identity)
        return self._ingest_archive(
            blob, key, start, end, identity, observed, path.stem + ".csv", accept_revisions
        )

    @staticmethod
    def _verify_checksum(blob, checksum, filename):
        parts = checksum.decode().strip().split()
        if len(parts) != 2 or parts[0].lower() != sha256(blob) or parts[1].lstrip("*") != filename:
            raise DataError("Archive CHECKSUM hash or filename mismatch")

    def _ingest_archive(self, blob, key, start, end, identity, observed, member, accept_revisions):
        start, end = utc(start), utc(end)
        token = sha256(
            json_bytes([identity, key.asdict(), start, end, PARSER_VERSION, POLICY_VERSION])
        )
        self._write(self.store.job, token, key, start, end, "running", identity)
        try:
            batch = normalize_archive(blob, key, identity, observed, member)
            if any(not start <= r["timestamp"] < end for r in batch.rows):
                raise DataError("Archive row outside declared source interval")
            if key.dataset.endswith("_bars") and any(r["bar_end"] > now() for r in batch.rows):
                raise DataError("Archive contains an unfinished candle")
            results = self._publish(
                key,
                batch,
                start,
                end,
                identity,
                accept_revisions=accept_revisions,
                authoritative=True,
            )
            state = (
                "quarantined" if any(r["state"] == "quarantined" for r in results) else "published"
            )
            self._write(self.store.job, token, key, start, end, state, identity)
            return results
        except Exception as exc:
            self._write(self.store.job, token, key, start, end, "failed", identity, str(exc))
            raise

    def archive(
        self,
        key,
        start,
        end,
        *,
        monthly=True,
        refresh=False,
        accept_revisions=False,
        progress=None,
        fallback_daily=True,
    ):
        start, end = utc(start), utc(end)
        list(full_days(start, end))
        if end > now().replace(hour=0, minute=0, second=0, microsecond=0):
            raise DataError("Only completed UTC days can be requested from archives")
        output = []
        for begin, stop, month in archive_units(key, start, end, monthly):
            if not refresh and self.store.coverage(key, begin, stop)["complete"]:
                output.append({"start": begin, "end": stop, "state": "already_complete"})
                continue
            url = archive_url(key, begin, month)
            token = sha256(json_bytes([url, key.asdict(), begin, stop]))
            self._write(self.store.job, token, key, begin, stop, "running")
            try:
                zip_response = self.fetcher.fetch(url)
                identity = self._save_response(zip_response, "archive")
                checksum_response = self.fetcher.fetch(url + ".CHECKSUM")
                self._save_response(checksum_response, "checksum")
                self._verify_checksum(
                    zip_response.body, checksum_response.body, url.rsplit("/", 1)[1]
                )
                _, observed = self.store.raw(identity)
                result = self._ingest_archive(
                    zip_response.body,
                    key,
                    begin,
                    stop,
                    identity,
                    observed,
                    url.rsplit("/", 1)[1][:-4] + ".csv",
                    accept_revisions,
                )
                state = (
                    "quarantined"
                    if any(r["state"] == "quarantined" for r in result)
                    else "published"
                )
                self._write(self.store.job, token, key, begin, stop, state, identity)
                output.extend(result)
            except SourceError as exc:
                if exc.body:
                    self._write(
                        self.store.put_raw,
                        exc.body,
                        exc.url,
                        "http_error",
                        metadata={"status": exc.status},
                    )
                self._write(
                    self.store.job,
                    token,
                    key,
                    begin,
                    stop,
                    "waiting_source" if exc.status == 404 else "failed",
                    error=str(exc),
                )
                if month and exc.status == 404 and fallback_daily:
                    output.extend(
                        self.archive(
                            key,
                            begin,
                            stop,
                            monthly=False,
                            refresh=refresh,
                            accept_revisions=accept_revisions,
                            progress=progress,
                        )
                    )
                elif exc.status == 404:
                    output.append(
                        {"start": begin, "end": stop, "state": "waiting_source", "url": url}
                    )
                else:
                    raise
            except Exception as exc:
                self._write(self.store.job, token, key, begin, stop, "failed", error=str(exc))
                raise
            if progress:
                progress({"key": key.id, "through": stop, "requests": self.fetcher.requests})
        return output

    def rest(self, key, start, end, *, accept_revisions=False):
        start, end = utc(start), utc(end)
        if start >= end:
            raise DataError("Expected start < end")
        if key.dataset == "open_interest" and start < now() - timedelta(days=29):
            raise DataError(
                "REST OI backfill exceeds conservative 29-day limit; use archive series"
            )
        if end > now():
            raise DataError("Cannot collect a future interval")
        cursor, collected, source_ids, issues, quarantined = start, [], [], [], False
        token = sha256(json_bytes([key.asdict(), start, end, "rest"]))
        self._write(self.store.job, token, key, start, end, "running")
        try:
            while cursor < end:
                # OI endpoints can return the LAST `limit` rows of a wide range.
                # Bound each grid query so its entire interval fits in one page.
                page_end = end
                if key.step:
                    limit = 500 if key.dataset == "open_interest" else 1000
                    page_end = min(end, cursor + timedelta(seconds=limit * key.step))
                url, weight = rest_url(key, cursor, page_end)
                response = self.fetcher.fetch(url, weight=weight)
                identity = self._save_response(response, "rest")
                source_ids.append(identity)
                _, observed = self.store.raw(identity)
                if key.dataset == "instrument_metadata":
                    observed = response.received_at
                batch = normalize_rest(response.body, key, identity, observed)
                if key.dataset == "instrument_metadata":
                    collected = batch.rows
                    start = observed.replace(hour=0, minute=0, second=0, microsecond=0)
                    end = start + timedelta(days=1)
                    break
                if any(not cursor <= r["timestamp"] < page_end for r in batch.rows):
                    raise DataError("REST returned records outside requested page range")
                if not batch.rows:
                    if key.step:
                        cursor = page_end
                        continue
                    break
                issues.extend(batch.issues)
                quarantined |= batch.quarantine
                completed = [r for r in batch.rows if r.get("bar_end", r["timestamp"]) <= end]
                collected.extend(completed)
                last = max(r["timestamp"] for r in batch.rows)
                next_cursor = last + timedelta(milliseconds=1)
                if key.step:
                    next_cursor = last + timedelta(seconds=key.step)
                if next_cursor <= cursor:
                    raise DataError("REST pagination did not advance")
                cursor = next_cursor
            batch = Batch(collected, issues, quarantined)
            # Every raw page is referenced by rows; retain the whole page list in a manifest artifact.
            request_manifest = json_bytes(
                {
                    "key": key.asdict(),
                    "start": start,
                    "end": end,
                    "source_ids": source_ids,
                    "pagination_complete": True,
                }
            )
            manifest_id = self._write(
                self.store.put_raw, request_manifest, "internal:rest-query", "query_manifest"
            )
            result = self._publish(
                key, batch, start, end, manifest_id, accept_revisions=accept_revisions
            )
            self._write(
                self.store.job,
                token,
                key,
                start,
                end,
                "quarantined" if quarantined else "published",
                manifest_id,
            )
            return result
        except Exception as exc:
            self._write(self.store.job, token, key, start, end, "failed", error=str(exc))
            raise

    def import_legacy(
        self, root, start, end, datasets=("trade_bars", "open_interest"), progress=None
    ):
        root = Path(root).resolve()
        start, end = utc(start), utc(end)
        candidates = {}
        for path in (root / "data/raw").rglob("*.zip"):
            name = path.name
            if name.startswith("BTCUSDT-metrics-") and "open_interest" in datasets:
                key = Key("open_interest", interval="5m", series="archive")
                day = name.removeprefix("BTCUSDT-metrics-").removesuffix(".zip")
            elif name.startswith("BTCUSDT-1m-") and "trade_bars" in datasets:
                key = Key("trade_bars")
                day = name.removeprefix("BTCUSDT-1m-").removesuffix(".zip")
            else:
                continue
            if len(day) != 10 or not start <= utc(day) < end:
                continue
            sha = sha256(path.read_bytes())
            previous = candidates.get((key.id, day))
            if previous and previous[1] != sha:
                raise DataError(f"Conflicting local vintages: {key.id} {day}")
            candidates[(key.id, day)] = (path, sha, key)
        results = []
        for (_, day), (path, _, key) in sorted(candidates.items()):
            result = self.import_archive(path, key, utc(day), utc(day) + timedelta(days=1))
            results.extend({"key": key.id, **r} for r in result)
            if progress and len(results) % 50 == 0:
                progress({"files_processed": len(results), "last_day": day})
        return results
