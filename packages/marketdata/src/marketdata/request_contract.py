"""Exact input identity and committed result contracts for eight scheduled jobs.

No scheduler, clock policy, data repair, or administrative override lives here.
"""

import datetime as dt
import hashlib
import json
import sqlite3
import tomllib
from contextlib import closing
from decimal import Decimal
from pathlib import Path

VERSION = "scheduled-request-v2"


class RequestError(RuntimeError):
    pass


def json_default(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        # Match marketdata.domain's stored numeric representation without float loss.
        return str(value)
    raise TypeError(f"Unsupported request value: {type(value).__name__}")


def encode(value):
    return json.dumps(value, sort_keys=True, default=json_default).encode() + b"\n"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def option(args, name, *, required=False):
    values = []
    for index, item in enumerate(args):
        if item == name:
            if index + 1 == len(args):
                raise RequestError(f"Missing value: {name}")
            values.append(args[index + 1])
        elif item.startswith(name + "="):
            values.append(item.partition("=")[2])
    if len(values) > 1 or (required and not values):
        raise RequestError(f"Expected one {name}")
    return values[0] if values else None


def inputs(job, args, binding, release):
    files = {}

    def read(path, kind="toml"):
        path = Path(path).absolute()
        if path.is_symlink() or not path.is_file():
            raise RequestError(f"Expected regular configuration: {path}")
        raw = path.read_bytes()
        files[str(path)] = {"resolved": str(path.resolve()), "sha256": digest(raw)}
        if kind == "toml":
            return tomllib.loads(raw.decode())
        return json.loads(raw) if kind == "json" else None

    read(binding, "json")
    if job in ("oi", "daily"):
        read(option(args, "--config", required=True))
        probe = option(args, "--probe-config")
        if probe:
            read(probe)
    elif job == "backup":
        settings = read(option(args, "--settings", required=True))
        policy = read(settings["retention_policy"])
        read(policy["archive_profile"])
        read(settings["clock_binding_file"], "json")
    elif job in ("restic-archive", "network-observer"):
        read(option(args, "--profile", required=True))
    # A unified checkout can change without a new immutable release directory.
    # Bind all shared business code, not only the three former policy files.
    required = (
        "scripts/run_job.py",
        "packages/marketdata/src/marketdata/request_contract.py",
        "packages/marketdata/src/marketdata/job_clock.py",
        "packages/marketdata/src/marketdata/checkpoint_clock.py",
        "pyproject.toml",
        "packages/marketdata/pyproject.toml",
        "uv.lock",
    )
    code_paths = {release / name for name in required}
    for folder in ("packages/marketdata/src/marketdata", "scripts", "integrations/runtime"):
        code_paths.update((release / folder).rglob("*.py"))
    for path in sorted(code_paths):
        read(path, "code")
    return {
        "schema": VERSION,
        "job": job,
        "arguments": args,
        "release": str(release.resolve()),
        "files": files,
    }


RECORDS = {"sync_runs": "report", "daily_runs": "record"}


def record_bytes(ref):
    column = (
        "id,artifact_id,url,observed_at,metadata"
        if ref["table"] == "fetches"
        else RECORDS[ref["table"]]
    )
    path = Path(ref["path"]).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(f"SELECT {column} FROM {ref['table']} WHERE id=?", (ref["id"],)).fetchone()
    if row is None:
        raise RequestError("Committed job record missing")
    if ref["table"] == "fetches":
        return encode(dict(row))
    return row[0].encode()


def _probe_report(raw):
    row = json.loads(raw)
    return {
        **json.loads(row["metadata"])["oi_probe"],
        "source_id": row["artifact_id"],
        "url": row["url"],
    }


def _probe_reference(value):
    # A payload can be fetched repeatedly. Bind the actual observation, not just
    # source_id or the mutable latest view. observed_at uses the existing index.
    r = value["result"]
    if len(value["files"]) != 1 or Path(value["files"][0]).name != "oi-probe-latest.json":
        raise RequestError("Expected OI probe storage path")
    path = (Path(value["files"][0]).absolute().parent / "catalog.sqlite").resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        rows = db.execute(
            "SELECT id FROM fetches WHERE observed_at=? AND artifact_id=? "
            "AND json_extract(metadata,'$.oi_probe.probe_id')=? LIMIT 2",
            (r["received_at"], r["source_id"], r["probe_id"]),
        ).fetchall()
    if len(rows) != 1:
        raise RequestError("Expected one committed probe observation")
    ref = {"path": str(path), "table": "fetches", "id": rows[0][0]}
    if _probe_report(record_bytes(ref)) != r:
        raise RequestError("Probe response/receipt mismatch")
    return ref


def range_bytes(ref):
    if (
        ref.get("kind") != "network-jsonl-v1"
        or type(ref.get("offset")) is not int
        or ref["offset"] < 0
        or type(ref.get("length")) is not int
        or ref["length"] <= 0
    ):
        raise RequestError("Invalid observation range")
    with Path(ref["path"]).open("rb") as stream:
        stream.seek(0, 2)
        if ref["offset"] + ref["length"] > stream.tell():
            raise RequestError("Committed observation range missing")
        if ref["offset"]:
            stream.seek(ref["offset"] - 1)
            if stream.read(1) != b"\n":
                raise RequestError("Observation does not start at a record boundary")
        stream.seek(ref["offset"])
        raw = stream.read(ref["length"])
    if not raw.endswith(b"\n") or b"\n" in raw[:-1] or digest(raw) != ref["sha256"]:
        raise RequestError("Committed observation changed; explicit review required")
    return raw


def seal_result(envelope):
    value = json.loads(encode(envelope))
    value["ranges"] = []
    if value["job"] == "oi-probe" and value["result"].get("source_id"):
        value["records"] = [_probe_reference(value)]
        value["files"] = []
    if value["job"] == "network-observer" and value["result"].get("status") != "busy":
        value["ranges"] = [value["result"]["observation_ref"]]
        range_bytes(value["ranges"][0])
    value["files"] = [
        {"path": str(Path(path).absolute()), "sha256": digest(Path(path).read_bytes())}
        for path in value["files"]
    ]
    value["records"] = [{**ref, "sha256": digest(record_bytes(ref))} for ref in value["records"]]
    return value


def verify_result(value):
    for ref in value["files"]:
        if digest(Path(ref["path"]).read_bytes()) != ref["sha256"]:
            raise RequestError("Committed report changed; explicit review required")
    for ref in value["records"]:
        if digest(record_bytes(ref)) != ref["sha256"]:
            raise RequestError("Committed ledger record changed; explicit review required")
    for ref in value.get("ranges", []):
        range_bytes(ref)


def _file_json(value, name=None):
    refs = [r for r in value["files"] if name is None or Path(r["path"]).name == name]
    if len(refs) != 1:
        raise RequestError("Expected one committed report")
    return json.loads(Path(refs[0]["path"]).read_bytes())


def classify(job, code, value, pins, args):
    """complete means request resolved, never an assertion of data quality."""

    def outcome(state, reason, data=None):
        return {
            "state": state,
            "reason": reason,
            "data_complete": data,
            "work_exit_code": code,
            "returncode": code,
        }

    if type(code) is not int:
        raise RequestError("Work returned an invalid exit status")
    if code not in (0, 2):
        return outcome("failed", "work_failed")
    if value is None or value.get("job") != job:
        raise RequestError("No current-job committed result; exit code is insufficient")
    verify_result(value)
    r = value["result"]
    if job == "daily" and r.get("state") == "deferred_lock" and code == 0:
        return outcome("deferred", "scheduler_lock_wait")
    if job == "network-observer" and r.get("status") == "busy" and code == 0:
        return outcome("deferred", "observer_lock_wait")
    if job == "oi-probe" and r.get("state") in ("cooldown", "boundary_wait") and code == 2:
        return outcome("deferred", r["state"], False)
    if not value["files"] and not value["records"] and not value.get("ranges"):
        raise RequestError("Result has no durable report/ledger reference")

    if job in ("pack", "backup"):
        expected_mode = "daily" if job == "backup" else "hourly"
        if (
            r.get("mode") != expected_mode
            or r.get("phase") != "finished"
            or r.get("state") not in ("complete", "partial")
            or code != (0 if r.get("ok") else 2)
        ):
            raise RequestError("Maintenance result does not prove finished processing")
        saved = _file_json(value, r["id"] + ".json")
        if saved != r:
            raise RequestError("Maintenance result differs from saved report")
        if job == "backup":
            marker = _file_json(value, "backup.json")
            day = pins.get("backup_day", {}).get("day")
            if (
                r.get("work_day") != day
                or not r.get("validation", {}).get("ok")
                or marker.get("backup_id") != r.get("backup", {}).get("backup_id")
                or Path(r["backup"]["path"]).parent.name != day
            ):
                raise RequestError("Backup completion does not satisfy the pinned day")
        return outcome("complete", "finished" if code == 0 else "finished_capacity_warning", True)
    if job == "oi-quality":
        if _file_json(value, "report.json") != r or code != (2 if r["needs_review"] else 0):
            raise RequestError("Quality report/result mismatch")
        if not option(args, "--end"):
            end = dt.datetime.fromisoformat(r["comparison_end"]).date().isoformat()
            if end != pins.get("oi_quality_day", {}).get("day"):
                raise RequestError("Quality report differs from pinned range")
        return outcome(
            "complete", "report_written_with_findings" if code == 2 else "report_written"
        )
    if job == "oi":
        ids = {ref["id"] for ref in value["records"] if ref["table"] == "sync_runs"}
        if not r or ids != {row["run_id"] for row in r}:
            raise RequestError("OI run/ledger identity mismatch")
        for row in r:
            ref = next(ref for ref in value["records"] if ref["id"] == row["run_id"])
            if json.loads(record_bytes(ref)) != row:
                raise RequestError("OI result differs from committed run")
        done = all(row.get("complete") is True for row in r)
        if code != (0 if done else 2):
            raise RequestError("OI exit/result mismatch")
        if any(row["state"] == "failed" for row in r):
            return outcome("failed", "oi_run_failed", False)
        if any(row["state"] not in ("complete", "partial") for row in r):
            raise RequestError("OI run did not reach a recorded terminal state")
        return outcome("complete", "coverage_replan_retains_missing_ranges", done)
    if job == "oi-probe":
        refs = [ref for ref in value["records"] if ref["table"] == "fetches"]
        if len(refs) != 1:
            raise RequestError("Expected one committed probe observation")
        raw = record_bytes(refs[0])
        if (
            digest(raw) != refs[0]["sha256"]
            or _probe_report(raw) != r
            or not r.get("source_id")
            or not r.get("probe_id")
            or code != (0 if r.get("complete") else 2)
        ):
            raise RequestError("Probe response/receipt mismatch")
        return outcome(
            "complete",
            "observation_recorded" if code == 0 else "observation_with_findings",
            bool(r.get("complete")),
        )
    if job == "daily":
        refs = [
            ref
            for ref in value["records"]
            if ref["table"] == "daily_runs" and ref["id"] == r.get("id")
        ]
        if code != 0 or not r.get("runner_ok") or len(refs) != 1:
            raise RequestError("Scheduler run not committed")
        # Publication probe metadata is added after the daily_runs commit.
        stored = json.loads(record_bytes(refs[0]))
        if any(r.get(k) != v for k, v in stored.items()):
            raise RequestError("Scheduler result differs from ledger")
        return outcome("complete", "remaining_tasks_owned_by_daily_ledger", r.get("data_complete"))
    if job == "restic-archive":
        if (
            code != 0
            or not r.get("ok")
            or not r.get("archive", {}).get("ok")
            or _file_json(value, r["run_id"] + ".json") != r
        ):
            raise RequestError("Archive receipt not successfully committed")
        return outcome("complete", "archive_preserved")
    if job == "network-observer":
        refs = value.get("ranges", [])
        if len(refs) != 1 or refs[0] != r.get("observation_ref"):
            raise RequestError("Expected one committed network observation")
        saved = json.loads(range_bytes(refs[0]))
        if code != 0 or any(
            saved.get(k) != r.get(k)
            for k in ("status", "started_at", "finished_at", "failed_probes", "profile_sha256")
        ):
            raise RequestError("Network observation receipt mismatch")
        return outcome("complete", "network_observation_recorded")
    raise RequestError("Unknown job result contract")
