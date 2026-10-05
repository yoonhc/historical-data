"""Policy-authorized, once-daily working-checkpoint retirement.

Preview cannot delete. Run requires its own recurring grant and proven timer
invocation. The existing Retirement.plan/apply audits and locks remain intact.
Lock order: this runner -> maintenance -> repository -> checkpoint parents.
"""

import argparse
import fcntl
import json
import os
import re
import stat
import sys
import tomllib
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from marketdata.checkpoint_locks import CheckpointBusy, plain_path
from marketdata.domain import WriterBusy, json_bytes, now, sha256

from scripts import archive_backups as archive
from scripts import checkpoint_code, restic_schedule
from scripts import retire_checkpoints as retirement
from scripts.restic_trial import require

SERVICE = "marketdata-checkpoint-retirement.service"
TIMER = "marketdata-checkpoint-retirement.timer"
CALENDAR = "*-*-* 06:40:00 UTC"
ZONE = ZoneInfo("Asia/Seoul")
PURPOSE = "recurring-working-checkpoint-retirement-v1"
EXIT_CODES = {"preview_ready": 0, "no_candidates": 0, "complete": 0, "deferred": 75, "failed": 1}


class Deferred(Exception):
    def __init__(self, reason, detail, *, attention=False):
        self.reason, self.detail, self.attention = reason, detail, attention
        super().__init__(detail)


@dataclass(frozen=True)
class Settings:
    retention_policy: Path
    authorization_file: Path

    @classmethod
    def load(cls, path):
        values = tomllib.loads(plain_path(path).read_text())
        require(set(values) == set(cls.__annotations__), "Unexpected/missing runner settings")
        for key, value in values.items():
            require(
                isinstance(value, str) and Path(value).is_absolute(), f"Absolute {key} required"
            )
            values[key] = plain_path(value)
        require(
            values["retention_policy"] != values["authorization_file"], "Separate grant required"
        )
        return cls(**values)


def scope(settings, policy, profile):
    code = checkpoint_code.current_code()
    # The release launcher must override an editable install of another tree.
    roots = {
        "marketdata": checkpoint_code.CODE_ROOT / "packages/marketdata/src/marketdata",
        "scripts": checkpoint_code.CODE_ROOT / "scripts",
    }
    for name, module in list(sys.modules.items()):
        package = name.split(".")[0]
        file = getattr(module, "__file__", None)
        if package in roots and file:
            require(Path(file).resolve().is_relative_to(roots[package]), "Mixed release imports")
    require(
        policy.keep_latest == 3 and policy.min_age_hours == 72, "Initial policy must be 3 / 72h"
    )
    require(policy.apply_enabled, "Use a separate final policy; preview itself never calls apply")
    return {
        "purpose": PURPOSE,
        "code": code,
        "uid": os.getuid(),
        "settings": {k: str(v) for k, v in vars(settings).items()},
        "policy_sha256": policy.fingerprint,
        "profile_sha256": profile.fingerprint,
        "identity": profile.identity,
        "paths": {k: str(getattr(profile, k)) for k in sorted(archive.PATH_FIELDS)},
        "restic_binary_sha256": sha256(profile.restic_binary.read_bytes()),
        "keep_latest": 3,
        "min_age_hours": 72,
        "schedule": {
            "service": SERVICE,
            "timer": TIMER,
            "on_calendar": CALENDAR,
            "timezone": "Asia/Seoul",
            "local_time": "15:40:00",
            "persistent": False,
            "retry": "next_calendar_day",
            "automatic_resume": False,
        },
        "allowed_action": "existing_retirement_plan_and_apply_only",
    }


def proposal(settings):
    policy = retirement.Policy.load(settings.retention_policy)
    profile = archive.Profile.load(policy.archive_profile)
    boundary = scope(settings, policy, profile)
    return {
        "schema": 1,
        "purpose": PURPOSE,
        "enabled": False,
        "approved_at": None,
        "approval_reference": None,
        "scope": boundary,
        "scope_id": sha256(json_bytes(boundary)),
    }


def authorize(settings, policy, profile, action):
    if (
        retirement.Policy.load(settings.retention_policy) != policy
        or archive.Profile.load(policy.archive_profile) != profile
    ):
        raise Deferred(
            "configuration_changed", "Configuration changed during this run", attention=True
        )
    path = plain_path(settings.authorization_file)
    if not path.is_file():
        raise Deferred(
            "authorization_missing", "A separate recurring grant is required", attention=True
        )
    info = path.stat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o600,
        "Grant must be an owned 0600 regular file",
    )
    grant = json.loads(path.read_bytes())
    expected = scope(settings, policy, profile)
    if (
        grant.get("schema") != 1
        or grant.get("purpose") != PURPOSE
        or grant.get("scope") != expected
        or grant.get("scope_id") != sha256(json_bytes(expected))
    ):
        raise Deferred(
            "authorization_scope_changed", "Code/policy/paths/scope need review", attention=True
        )
    require(type(grant.get("enabled")) is bool, "Explicit grant enabled flag required")
    if grant["enabled"]:
        require(
            isinstance(grant.get("approval_reference"), str)
            and grant["approval_reference"].strip(),
            "Recurring approval reference required",
        )
        require(archive.timestamp(grant["approved_at"]) <= now(), "Approval time is in the future")
    elif action == "run":
        raise Deferred(
            "authorization_pending", "Recurring deletion has not been approved", attention=True
        )
    return {
        "scope_id": grant["scope_id"],
        "enabled": grant["enabled"],
        "approval_reference": grant["approval_reference"],
        "approved_at": grant["approved_at"],
    }


def proof_file(profile, name):
    path = plain_path(name)
    require(
        path.is_absolute() and path.is_relative_to(profile.reports_root), "Proof outside reports"
    )
    return path.read_bytes()


def daily_evidence(profile, state, day):
    path = profile.store_root / "state/maintenance-daily-latest.json"
    if not path.exists():
        raise Deferred("daily_missing", "No completed daily report for this local date")
    raw = plain_path(path).read_bytes()
    daily = json.loads(raw)
    began = archive.timestamp(daily["started_at"])
    if began.astimezone(ZONE).date().isoformat() != day:
        raise Deferred("daily_not_today", "Latest daily belongs to a different KST date")
    if daily.get("state") != "complete" or daily.get("ok") is not True:
        raise Deferred(
            "daily_not_complete",
            f"Daily state: {daily.get('state')}",
            attention=daily.get("state") in {"failed", "partial", "interrupted"},
        )
    require(daily.get("mode") == "daily" and daily.get("phase") == "finished", "Wrong daily report")
    require(re.fullmatch(r"[0-9a-f]{32}", daily.get("id", "")), "Daily identity missing")
    history = profile.store_root / f"reports/maintenance/{began:%Y-%m-%d}/{daily['id']}.json"
    require(json.loads(plain_path(history).read_bytes()) == daily, "Daily history/latest differ")
    require(
        daily.get("validation", {}).get("ok") is True and daily["validation"].get("failures") == [],
        "Daily validation failed",
    )
    backup = daily.get("backup", {})
    require(backup.get("ok") is True, "Daily backup failed")
    bid = backup.get("backup_id")
    require(re.fullmatch(archive.HEX, bid or ""), "Daily backup ID missing")
    record = state["checkpoints"].get(bid)
    if not record or not record.get("stored") or not record.get("input_verified_at"):
        raise Deferred("daily_not_archived", "Today's exact backup ID is not verified and stored")
    entry, stored = record["entry"], record["stored"]
    require(
        entry["backup_id"] == bid and entry["path"] == backup["path"],
        "Daily/archive binding differs",
    )
    require(entry["source_root"] == str(profile.store_root), "Daily source differs")
    checkpoint, completed, finished = (
        archive.timestamp(entry["checkpoint_at"]),
        archive.timestamp(entry["completed_at"]),
        archive.timestamp(daily["finished_at"]),
    )
    require(began <= checkpoint <= completed <= finished <= now(), "Daily times out of order")
    require(checkpoint.astimezone(ZONE).date().isoformat() == day, "Checkpoint is not today's data")
    if not stored.get("archive_started_at") or not stored.get("archive_finished_at"):
        raise Deferred(
            "archive_time_unknown", "No original archive timing evidence", attention=True
        )
    astart, aend = map(
        archive.timestamp, (stored["archive_started_at"], stored["archive_finished_at"])
    )
    require(
        completed <= archive.timestamp(record["input_verified_at"]) <= astart <= aend <= now(),
        "Archive chronology differs",
    )
    sid = stored["snapshot_id"]
    attempts = [
        a
        for a in record["attempts"]
        if a.get("status") == "stored"
        and a.get("snapshot_id") == sid
        and a.get("returncode") == 0
        and a.get("command", {}).get("started_at") == stored["archive_started_at"]
        and a.get("command", {}).get("finished_at") == stored["archive_finished_at"]
    ]
    require(len(attempts) == 1, "Unambiguous successful archive command required")
    command = attempts[0]["command"]
    require(
        command["returncode"] == 0
        and command["arguments"][0] == "backup"
        and command["cwd"] == entry["path"],
        "Archive command differs",
    )
    summary = [json.loads(line) for line in proof_file(profile, command["stdout"]).splitlines()]
    summaries = [r for r in summary if r.get("message_type") == "summary"]
    require(
        len(summaries) == 1 and summaries[0].get("snapshot_id") == sid, "Archive summary differs"
    )
    check = state.get("repository_check") or {}
    if (
        not check.get("ok")
        or check.get("status") != "complete"
        or not check.get("read_data")
        or sid not in check.get("snapshot_ids", [])
    ):
        raise Deferred(
            "full_check_pending",
            "Full read-data check must include today's snapshot",
            attention=check.get("status") == "failed",
        )
    cstart, cend = map(archive.timestamp, (check["started_at"], check["finished_at"]))
    require(aend <= cstart <= cend <= now(), "Full check precedes archive or has future times")
    receipt = check["receipt"]
    require(
        receipt["arguments"] == ["check", "--read-data"] and receipt["returncode"] == 0,
        "Successful full-data command required",
    )
    require(
        cstart
        <= archive.timestamp(receipt["started_at"])
        <= archive.timestamp(receipt["finished_at"])
        == cend,
        "Check receipt time differs",
    )
    checked = json.loads(proof_file(profile, receipt["stdout"]))
    require(
        checked.get("num_errors") == 0 and not checked.get("broken_packs"),
        "Check output has errors",
    )
    return {
        "date_kst": day,
        "daily_id": daily["id"],
        "daily_report_sha256": sha256(raw),
        "backup_id": bid,
        "snapshot_id": sid,
        "checkpoint_at": entry["checkpoint_at"],
        "daily_finished_at": daily["finished_at"],
        "archive_finished_at": stored["archive_finished_at"],
        "full_check_finished_at": check["finished_at"],
        "archive_stdout_sha256": sha256(proof_file(profile, command["stdout"])),
        "check_stdout_sha256": sha256(proof_file(profile, receipt["stdout"])),
    }


class ScheduledRetirement(retirement.Retirement):
    def __init__(self, policy, reports, settings, action, day, report):
        super().__init__(policy, reports)
        self.settings, self.action, self.day, self.report = settings, action, day, report

    def plan(self):
        return super().plan(
            automation={
                "run_id": self.report["run_id"],
                "scope_id": self.report["authorization"]["scope_id"],
            }
        )

    def check_code(self, plan):
        super().check_code(plan)
        # Also honor a revoked/changed grant before each existing retirement step.
        self.report["authorization"] = authorize(self.settings, self.policy, self.p, self.action)
        require(
            plan.get("automation")
            == {
                "run_id": self.report["run_id"],
                "scope_id": self.report["authorization"]["scope_id"],
            },
            "Plan belongs to another automatic run or approval scope",
        )

    @contextmanager
    def locks(self):
        with super().locks():
            # Repeat both at plan and apply; no use of a stale pre-lock decision.
            self.report["authorization"] = authorize(
                self.settings, self.policy, self.p, self.action
            )
            if now().astimezone(ZONE).date().isoformat() != self.day:
                raise Deferred("date_changed", "Evaluate again on the next scheduled day")
            journals = self.journals()
            pending = [j["plan_id"] for j in journals if j["status"] != "complete"]
            if pending:
                self.report["unfinished_plan_ids"] = pending
                raise Deferred(
                    "unfinished_retirement",
                    "Inspect/resume the prior plan manually",
                    attention=True,
                )
            complete = {j["plan_id"] for j in journals if j["status"] == "complete"}
            unresolved = []
            for path in sorted((self.p.reports_root / "checkpoint-scheduled").glob("*/run.json")):
                previous = json.loads(plain_path(path).read_bytes())
                if (
                    previous.get("run_id") != self.report["run_id"]
                    and previous.get("action") == "run"
                    and previous.get("state") == "running"
                    and previous.get("plan_id")
                    and previous["plan_id"] not in complete
                ):
                    unresolved.append(
                        {"run_id": previous["run_id"], "plan_id": previous["plan_id"]}
                    )
            if unresolved:
                self.report["unfinished_automatic_runs"] = unresolved
                raise Deferred(
                    "unfinished_automatic_run",
                    "Prior planned run ended without a final result",
                    attention=True,
                )
            state = self.a.state
            if state.get("active_operation"):
                raise Deferred(
                    "archive_unfinished", "An archive operation is unresolved", attention=True
                )
            if not (state.get("last_operation") or {"ok": True}).get("ok"):
                raise Deferred(
                    "archive_failed", "Last archive operation needs attention", attention=True
                )
            entries, incomplete = archive.discover(self.p)
            if incomplete:
                raise Deferred(
                    "checkpoint_incomplete",
                    "Incomplete checkpoint folders need review",
                    attention=True,
                )
            records = state["checkpoints"]
            if any(r.get("error") for r in records.values()):
                raise Deferred("checkpoint_error", "Unresolved checkpoint error", attention=True)
            if any(
                not records.get(e["backup_id"], {}).get("stored")
                or not records[e["backup_id"]].get("input_verified_at")
                for e in entries
            ):
                raise Deferred(
                    "unarchived_checkpoint", "Not every local checkpoint is verified and archived"
                )
            self.report["daily_evidence"] = daily_evidence(self.p, self.a.state, self.day)
            yield


@contextmanager
def runner_lock(root):
    retirement.durable_directory(root)
    with plain_path(root / "runner.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Deferred(
                "runner_busy", "Another automatic retirement evaluation is running"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def execute(settings, action):
    require(action in {"preview", "run"}, "Expected preview or run")
    started = now()
    day = started.astimezone(ZONE).date().isoformat()
    policy = retirement.Policy.load(settings.retention_policy)
    profile = archive.Profile.load(policy.archive_profile)
    rid = uuid.uuid4().hex
    out = profile.reports_root / "checkpoint-scheduled" / rid
    retirement.durable_directory(out)
    path = out / "run.json"
    report = {
        "schema": 1,
        "run_id": rid,
        "action": action,
        "date_kst": day,
        "started_at": started,
        "state": "running",
        "phase": "preflight",
        "ok": False,
        "needs_attention": False,
        "plan_id": None,
        "selection_assessed": False,
        "deleted_checkpoints": 0,
        "deletion_attempted": False,
        "report": str(path),
        "next_evaluation": "next scheduled calendar day; no automatic retry",
    }
    retirement.persist(path, report)
    agent = None
    try:
        with runner_lock(profile.state_file.parent / "working-retention/automatic"):
            report["authorization"] = authorize(settings, policy, profile, action)
            if action == "run":
                context = restic_schedule.service_context(SERVICE, TIMER)
                report["context"] = context
                if not context.get("scheduled") or not context.get("service_verified"):
                    raise Deferred(
                        "unconfirmed_timer",
                        "Recurring grant only permits the named timer",
                        attention=True,
                    )
                trigger = archive.timestamp(context["trigger_at"]).astimezone(ZONE)
                if trigger.date().isoformat() != day or (trigger.hour, trigger.minute) != (15, 40):
                    raise Deferred(
                        "wrong_timer_date", "Expected today's 15:40 KST trigger", attention=True
                    )
                require(archive.timestamp(context["trigger_at"]) <= now(), "Future timer trigger")
            agent = ScheduledRetirement(policy, out / "commands", settings, action, day, report)
            plan = agent.plan()
            report.update(
                plan_id=plan["plan_id"], plan=plan, selection_assessed=True, phase="planned"
            )
            retirement.persist(path, report)
            if not plan["candidates"]:
                report.update(state="no_candidates", ok=True)
            elif action == "preview":
                report.update(state="preview_ready", ok=True)
            else:
                report.update(phase="applying", deletion_attempted=True)
                retirement.persist(path, report)
                result = agent.apply(plan["plan_id"])
                report.update(
                    state="complete",
                    ok=True,
                    result=result,
                    deleted_checkpoints=len(result["journal"]["items"]),
                )
    except (Deferred, WriterBusy, archive.Busy, CheckpointBusy) as exc:
        if isinstance(exc, Deferred):
            reason, detail, attention = exc.reason, exc.detail, exc.attention
        else:
            reason = {
                WriterBusy: "maintenance_busy",
                archive.Busy: "archive_busy",
                CheckpointBusy: "checkpoint_busy",
            }[type(exc)]
            detail, attention = str(exc), False
        report.update(state="deferred", reason=reason, detail=detail, needs_attention=attention)
    except BaseException as exc:
        report.update(state="failed", needs_attention=True, error=f"{type(exc).__name__}: {exc}")
    finally:
        if agent and report["plan_id"]:
            journal = agent.root / "journals" / f"{report['plan_id']}.json"
            if journal.exists():
                report["retirement_journal"] = str(journal)
                value = json.loads(journal.read_bytes())
                report["deleted_checkpoints"] = sum(
                    i["phase"] == "complete" for i in value["items"].values()
                )
                report["unfinished_retirement"] = value["status"] != "complete"
                if report["unfinished_retirement"]:
                    report.update(state="failed", ok=False, needs_attention=True)
        report.update(finished_at=now(), phase="finished", exit_code=EXIT_CODES[report["state"]])
        retirement.persist(path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("action", choices=("propose", "preview", "run"))
    args = parser.parse_args()
    try:
        settings = Settings.load(args.settings)
        result = proposal(settings) if args.action == "propose" else execute(settings, args.action)
        print(json_bytes(result).decode())
        return result.get("exit_code", 0)
    except Exception as exc:
        print(
            json_bytes(
                {
                    "state": "failed",
                    "needs_attention": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            ).decode()
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
