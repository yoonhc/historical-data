"""Run the parallel restic archive and record evidence for its first three new daily dates.

Timer provenance is required to count a date. A manual service start still archives safely,
but never counts as a scheduled success. No init, deletion, retention or collector restart.
"""

import argparse
import fcntl
import json
import os
import pwd
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from marketdata import job_clock as jc
from marketdata.domain import json_bytes, now, sha256
from marketdata.store import atomic_write

from scripts.archive_backups import (
    Busy,
    Profile,
    discover,
    execute,
    load_state,
    no_symlinks,
    overview,
    require,
    timestamp,
)

SERVICE = "marketdata-restic-archive.service"
TIMER = "marketdata-restic-archive.timer"
TRIGGER_KEYS = (
    "INVOCATION_ID",
    "TRIGGER_UNIT",
    "TRIGGER_TIMER_REALTIME_USEC",
    "TRIGGER_TIMER_MONOTONIC_USEC",
)


def observation_path(profile):
    return profile.state_file.with_name("observation.json")


def save(profile, observation):
    observation["updated_at"] = now().isoformat()
    atomic_write(observation_path(profile), json_bytes(observation))


def load(profile):
    path = observation_path(profile)
    no_symlinks(path)
    require(path.is_file(), "Initialize the observation once before activating the timer")
    result = json.loads(path.read_bytes())
    require(
        result["version"] == 1 and result["identity"] == profile.identity,
        "Observation belongs to another source/scope/repository",
    )
    return result


@contextmanager
def observation_lock(profile):
    path = observation_path(profile).with_suffix(".lock")
    no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Busy("Another scheduled observation is running") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def start_observation(profile):
    profile.guard()
    with observation_lock(profile):
        require(not observation_path(profile).exists(), "Observation exists; do not reset evidence")
        entries, _ = discover(profile)
        result = {
            "version": 1,
            "identity": profile.identity,
            "started_at": now().isoformat(),
            "date_basis": "checkpoint_finished_at UTC",
            "required_distinct_dates": 3,
            "excluded_backup_ids": sorted(e["backup_id"] for e in entries),
            "qualified_dates": {},
            "runs": [],
            "restore": {"status": "awaiting_new_snapshot"},
        }
        save(profile, result)
        return result


def unit_bus_path(name):
    return "/org/freedesktop/systemd1/unit/" + "".join(
        c if c.isascii() and c.isalnum() else f"_{ord(c):02x}" for c in name
    )


def service_context(service=SERVICE, timer=TIMER, *, manager="user"):
    """Positive, cross-checked timer evidence; ambiguous/missing information only undercounts."""
    require(manager in {"user", "system"}, "Explicit systemd manager")
    manager_arg = "--" + manager
    context = {
        "manager": manager,
        "uid": os.getuid(),
        "user": pwd.getpwuid(os.getuid()).pw_name,
        "pid": os.getpid(),
        "python": sys.executable,
        "cwd": os.getcwd(),
        "module": str(Path(__file__).resolve()),
        "module_sha256": sha256(Path(__file__).read_bytes()),
        "environment": {key: os.environ.get(key) for key in TRIGGER_KEYS},
        "scheduled": False,
        "service_verified": False,
        "provenance_method": "current_start_job_activation_details",
        "manager_evidence": {},
    }
    env = context["environment"]
    if not re.fullmatch(r"[0-9a-f]{32}", env.get("INVOCATION_ID") or ""):
        context["classification"] = "manual_cli_or_unknown"
        return context
    try:
        output = subprocess.check_output(
            ["systemctl", manager_arg, "show", service, "--property=InvocationID,MainPID,Id,Type"],
            text=True,
            timeout=10,
        )
        props = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        context["service_properties"] = props
        require(
            props.get("InvocationID") == env["INVOCATION_ID"]
            and props.get("MainPID") == str(os.getpid())
            and props.get("Id") == service
            and props.get("Type") == "oneshot",
            "Invocation/PID does not belong to the archive service",
        )
        context["service_verified"] = True

        def query(path, interface, prop, signature):
            raw = subprocess.check_output(
                [
                    "busctl",
                    manager_arg,
                    "--json=short",
                    "get-property",
                    "org.freedesktop.systemd1",
                    path,
                    "org.freedesktop.systemd1." + interface,
                    prop,
                ],
                text=True,
                timeout=10,
            )
            result = json.loads(raw)
            context["manager_evidence"][f"{path}:{interface}.{prop}"] = result
            require(result["type"] == signature, "Unexpected manager property type")
            return result["data"]

        # Unit activation details (and TRIGGER_* env) can outlive an invocation on systemd 255.
        # A oneshot's current start Job has its own details and remains live until ExecStart exits.
        service_path = unit_bus_path(service)
        job_id, job_path = query(service_path, "Unit", "Job", "(uo)")
        require(
            job_id > 0 and job_path == f"/org/freedesktop/systemd1/job/{job_id}",
            "No current start job",
        )
        require(query(job_path, "Job", "Unit", "(so)") == [service, service_path], "Wrong job unit")
        require(query(job_path, "Job", "JobType", "s") == "start", "Not a start job")
        require(query(job_path, "Job", "State", "s") == "running", "Start job is not running")
        pairs = query(job_path, "Job", "ActivationDetails", "a(ss)")
        details = dict(pairs)
        require(len(details) == len(pairs), "Duplicate activation detail keys")
        context["start_job"] = {"id": job_id, "path": job_path, "activation_details": details}
        if not details:
            context["classification"] = "manual_service_or_unknown"
            return context
        require(details.get("trigger_unit") == timer, "Start job was not triggered by this timer")
        real = int(details.get("trigger_timer_realtime_usec", "0"))
        mono = int(details.get("trigger_timer_monotonic_usec", "0"))
        require(real > 0 and mono > 0, "Missing job timer timestamps")
        for prop, expected in (("LastTriggerUSec", real), ("LastTriggerUSecMonotonic", mono)):
            actual = query(unit_bus_path(timer), "Timer", prop, "t")
            require(actual == expected, "Job timer trigger stamp differs from manager")
        require(
            query(service_path, "Unit", "Job", "(uo)") == [job_id, job_path],
            "Service start job changed during verification",
        )
        context.update(
            scheduled=True,
            classification="verified_timer",
            trigger_at=datetime.fromtimestamp(real / 1_000_000, UTC).isoformat(),
            environment_matches_job=(
                env["TRIGGER_UNIT"] == timer
                and env["TRIGGER_TIMER_REALTIME_USEC"] == str(real)
                and env["TRIGGER_TIMER_MONOTONIC_USEC"] == str(mono)
            ),
        )
    except Exception as exc:
        context.update(classification="unconfirmed_service", provenance_error=str(exc))
    return context


def eligible_records(observation, context, receipt, state):
    """Require a new checkpoint AND new successful storage/check in this proven timer run."""
    if not context.get("scheduled") or not receipt.get("ok"):
        return []
    if receipt.get("action") != "run" or receipt.get("result", {}).get("processed", 0) <= 0:
        return []
    started, finished = timestamp(receipt["started_at"]), timestamp(receipt["finished_at"])
    check = state.get("repository_check") or {}
    if not check.get("ok") or not check.get("read_data"):
        return []
    if not started <= timestamp(check["started_at"]) <= timestamp(check["finished_at"]) <= finished:
        return []
    entries = []
    for record in state["checkpoints"].values():
        entry, stored = record["entry"], record["stored"]
        if entry["backup_id"] in observation["excluded_backup_ids"] or not stored:
            continue
        if timestamp(entry["checkpoint_at"]) <= timestamp(observation["started_at"]):
            continue
        sid = stored["snapshot_id"]
        if sid not in check.get("snapshot_ids", []):
            continue
        if not stored.get("archive_started_at") or not stored.get("archive_finished_at"):
            continue  # Unknown original invocation is not turned into a scheduled success.
        if not (
            started
            <= timestamp(stored["archive_started_at"])
            <= timestamp(stored["archive_finished_at"])
            <= timestamp(check["started_at"])
        ):
            continue
        proof = [
            a
            for a in record["attempts"]
            if a.get("snapshot_id") == sid
            and a.get("status") == "stored"
            and a.get("returncode") == 0
            and timestamp(a["started_at"]) >= started
        ]
        if not proof:
            continue
        entries.append(
            {
                "date_utc": timestamp(entry["checkpoint_at"]).date().isoformat(),
                "backup_id": entry["backup_id"],
                "snapshot_id": sid,
                "checkpoint_at": entry["checkpoint_at"],
                "manifest_sha256": entry["manifest_sha256"],
                "source_path": entry["path"],
                "archive_run_id": receipt["run_id"],
                "invocation_id": context["environment"]["INVOCATION_ID"],
                "trigger_at": context["trigger_at"],
                "archive_started_at": stored["archive_started_at"],
                "archive_finished_at": stored["archive_finished_at"],
                "checked_at": check["finished_at"],
                "check_receipt": check["receipt"],
                "read_data": True,
            }
        )
    return sorted(entries, key=lambda e: (e["checkpoint_at"], e["backup_id"]))


def add_dates(observation, records):
    added = []
    for record in records:
        day = record["date_utc"]
        if (
            day not in observation["qualified_dates"]
            and len(observation["qualified_dates"]) < observation["required_distinct_dates"]
        ):
            observation["qualified_dates"][day] = record
            added.append(day)
    return added


def reconcile_restore(observation, state):
    restore = observation["restore"]
    if not restore.get("snapshot_id"):
        return
    candidates = [
        r
        for r in state.get("restore_checks", [])
        if r["snapshot_id"] == restore["snapshot_id"]
        and timestamp(r["started_at"]) >= timestamp(restore["selected_at"])
    ]
    successful = [r for r in candidates if r.get("ok") and r.get("finished_at")]
    if successful:
        restore.update(status="passed", proof=successful[-1])
    elif restore["status"] == "running":
        # This function runs under the wrapper's lock, before any new restore starts.
        restore.update(
            status="interrupted_or_failed",
            last_available_proof=candidates[-1] if candidates else None,
        )


def progress(observation):
    dates = sorted(observation["qualified_dates"])
    required = observation["required_distinct_dates"]
    return {
        "started_at": observation["started_at"],
        "required_distinct_dates": required,
        "qualified_dates": dates,
        "completed_dates": len(dates),
        "remaining_dates": max(0, required - len(dates)),
        "date_evidence": observation["qualified_dates"],
        "restore": observation["restore"],
        "complete": len(dates) >= required and observation["restore"]["status"] == "passed",
        "recorded_runs": len(observation["runs"]),
        "last_run": observation.get("last_run"),
    }


def perform_restore(profile, observation):
    try:
        proof = execute(profile, "restore-check", observation["restore"]["snapshot_id"])
        observation["restore"].update(status="passed", proof=proof["result"])
        return proof
    except BaseException as exc:
        observation["restore"].update(status="failed", error=str(exc), failed_at=now().isoformat())
        raise
    finally:
        save(profile, observation)


def restore_observation(profile, sid):
    """Explicit recovery drill for a new checked snapshot, without awarding a scheduled date."""
    require(re.fullmatch(r"[0-9a-f]{64}", sid or ""), "An explicit full snapshot ID is required")
    with observation_lock(profile):
        observation = load(profile)
        state = load_state(profile)
        reconcile_restore(observation, state)
        records = [
            r
            for r in state["checkpoints"].values()
            if r["stored"] and r["stored"]["snapshot_id"] == sid
        ]
        require(len(records) == 1, "Snapshot must belong to this archive scope")
        entry, stored = records[0]["entry"], records[0]["stored"]
        require(
            entry["backup_id"] not in observation["excluded_backup_ids"]
            and timestamp(entry["checkpoint_at"]) > timestamp(observation["started_at"]),
            "Restore observation requires a new checkpoint from the observation period",
        )
        check = state.get("repository_check") or {}
        require(
            check.get("ok")
            and check.get("read_data")
            and sid in check.get("snapshot_ids", [])
            and stored.get("archive_finished_at")
            and timestamp(stored["archive_finished_at"]) <= timestamp(check["started_at"]),
            "Snapshot requires successful storage and a subsequent full repository check",
        )
        previous = observation["restore"]
        require(previous["status"] != "passed", "Observation restore has already passed")
        require(
            not previous.get("snapshot_id") or previous["snapshot_id"] == sid,
            "Do not replace the previously selected snapshot",
        )
        observation["restore"] = {
            "status": "running",
            "snapshot_id": sid,
            "backup_id": entry["backup_id"],
            "date_utc": timestamp(entry["checkpoint_at"]).date().isoformat(),
            "selected_at": now().isoformat(),
            "selection": "explicit manual drill of a new checked operating snapshot; no date credit",
            "automatic_attempts": previous.get("automatic_attempts", 0),
            "manual_attempts": previous.get("manual_attempts", 0) + 1,
            "previous_attempt": previous if previous.get("snapshot_id") else None,
        }
        save(profile, observation)
        proof = perform_restore(profile, observation)
        return {"ok": True, "restore": proof, "progress": progress(observation)}


def run(profile):
    with observation_lock(profile):
        observation = load(profile)
        reconcile_restore(observation, load_state(profile))
        clock_start = jc.active().point("archive_preservation_start") if jc.active() else None
        context = service_context()
        run_id = uuid.uuid4().hex
        report = profile.reports_root / "scheduled" / (run_id + ".json")
        record = {
            "run_id": run_id,
            "started_at": now().isoformat(),
            "context": context,
            "ok": False,
            "counted_dates": [],
            **({"clock_observation": clock_start.evidence} if clock_start else {}),
            "archive": None,
            "restore": None,
        }
        observation["runs"].append(
            {"run_id": run_id, "report": str(report), "started_at": record["started_at"]}
        )
        save(profile, observation)
        atomic_write(report, json_bytes(record))
        try:
            receipt = execute(profile, "run")
            record["archive"] = receipt
            clock_known = True
            if jc.active():
                try:
                    record["clock_at_qualification"] = (
                        jc.active().point("archive_qualification").evidence
                    )
                except jc.ClockDeferred as exc:
                    clock_known = False
                    record["clock_qualification_reason"] = str(exc)
            record["freshness_known"] = clock_known
            candidates = (
                eligible_records(observation, context, receipt, load_state(profile))
                if clock_known
                else []
            )
            record["counted_dates"] = add_dates(observation, candidates)
            save(profile, observation)
            atomic_write(report, json_bytes(record))
            # One bounded automatic drill on an explicitly recorded new, scheduled snapshot.
            if (
                context["scheduled"]
                and observation["qualified_dates"]
                and observation["restore"]["status"] == "awaiting_new_snapshot"
            ):
                selected = observation["qualified_dates"][min(observation["qualified_dates"])]
                observation["restore"] = {
                    "status": "running",
                    "snapshot_id": selected["snapshot_id"],
                    "backup_id": selected["backup_id"],
                    "date_utc": selected["date_utc"],
                    "selected_at": now().isoformat(),
                    "scheduled_run_id": run_id,
                    "automatic_attempts": 1,
                    "selection": "first qualifying new daily date",
                }
                save(profile, observation)
                record["restore"] = perform_restore(profile, observation)
            record["ok"] = True
            return record
        except BaseException as exc:
            record["error"] = str(exc)
            raise
        finally:
            record["finished_at"] = now().isoformat()
            observation["last_run"] = {
                "run_id": run_id,
                "report": str(report),
                "ok": record["ok"],
                "finished_at": record["finished_at"],
                "scheduled": context["scheduled"],
                "classification": context["classification"],
                "provenance_error": context.get("provenance_error"),
            }
            record["progress"] = progress(observation)
            save(profile, observation)
            atomic_write(report, json_bytes(record))


def status(profile):
    return {"archive": overview(profile), "observation": progress(load(profile))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("action", choices=("start-observation", "run", "status", "restore-check"))
    parser.add_argument("--snapshot-id")
    args = parser.parse_args()
    try:
        p = Profile.load(args.profile)
        require(
            args.action == "restore-check" or args.snapshot_id is None, "Unexpected snapshot ID"
        )
        if args.action in ("run", "restore-check"):
            os.nice(max(0, 10 - os.getpriority(os.PRIO_PROCESS, 0)))
            subprocess.run(["ionice", "-c", "2", "-n", "7", "-p", str(os.getpid())], check=True)
            result = run(p) if args.action == "run" else restore_observation(p, args.snapshot_id)
        elif args.action == "start-observation":
            result = start_observation(p)
        else:
            result = status(p)
        if args.action == "run":
            jc.finished(
                "restic-archive",
                result,
                files=[p.reports_root / "scheduled" / (result["run_id"] + ".json")],
            )
        print(json_bytes(result).decode(), flush=True)
    except Busy as exc:
        print(
            json_bytes({"ok": False, "status": "lock_busy", "error": str(exc)}).decode(), flush=True
        )
        raise SystemExit(75)
    except (jc.ClockDeferred, jc.ClockFailure):
        raise
    except (Exception, KeyboardInterrupt) as exc:
        print(
            json_bytes({"ok": False, "type": type(exc).__name__, "error": str(exc)}).decode(),
            flush=True,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
