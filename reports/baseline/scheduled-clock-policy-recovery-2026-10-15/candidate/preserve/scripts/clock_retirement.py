"""T05: clock-bounded checkpoint retirement in a system service.

preview cannot delete; apply-plan needs a separate exact-plan approval; run
needs a new recurring system-timer grant. Old grants never authorize this code.
"""

import argparse
import json
import re
import sys
import tomllib
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from marketdata import checkpoint_evidence as evidence
from marketdata.checkpoint_locks import CheckpointBusy, plain_path
from marketdata.domain import WriterBusy, json_bytes, now, sha256

from scripts import archive_backups as archive
from scripts import checkpoint_schedule as legacy
from scripts import restic_schedule
from scripts import retire_checkpoints as retirement
from scripts import retirement_clock as clock
from scripts.restic_trial import require

PURPOSE = "system-clock-bounded-checkpoint-retirement-v2"
UNITS = (
    "marketdata-checkpoint-retirement.service",
    "marketdata-checkpoint-retirement.timer",
    "marketdata-checkpoint-retirement-preview.service",
    "marketdata-checkpoint-retirement-apply@.service",
)


@dataclass(frozen=True)
class Settings:
    retention_policy: Path
    authorization_file: Path
    manual_authorization_file: Path
    clock_binding_file: Path
    state_root: Path
    unit_directory: Path

    @classmethod
    def load(cls, path):
        values = tomllib.loads(plain_path(path).read_text())
        require(set(values) == set(cls.__annotations__), "Explicit T05 settings required")
        for key, value in values.items():
            require(isinstance(value, str) and Path(value).is_absolute(), f"Absolute {key}")
            require(
                not any(c in value for c in "\n\r%") and ".." not in Path(value).parts,
                "Unsafe path",
            )
            values[key] = plain_path(value)
        require(len(set(values.values())) == len(values), "Separate T05 paths required")
        return cls(**values)


def scope(settings, policy, profile):
    value = legacy.scope(settings, policy, profile)
    binding = json.loads(plain_path(settings.clock_binding_file).read_bytes())
    require(
        set(binding) == {"bootstrap_profile", "bootstrap_profile_sha256"}, "Clock binding fields"
    )
    require(re.fullmatch(archive.HEX, binding["bootstrap_profile_sha256"]), "Clock binding hash")
    require(Path(binding["bootstrap_profile"]).is_absolute(), "Absolute bootstrap profile")
    value.update(
        purpose=PURPOSE,
        clock_policy=clock.POLICY,
        clock_binding=binding,
        system_units={
            name: sha256(plain_path(settings.unit_directory / name).read_bytes()) for name in UNITS
        },
    )
    value["schedule"]["manager"] = "system"
    value["schedule"]["manual_service"] = "marketdata-checkpoint-retirement-apply@PLAN_ID.service"
    value["allowed_action"] = "clock_bounded_existing_retirement_only"
    for protected in (profile.backup_dir, profile.restic_repository, profile.restore_work_root):
        require(
            not retirement.overlap(settings.state_root, protected),
            "T05 state overlaps protected data",
        )
    require(settings.state_root != profile.state_root, "Separate T05 control state")
    return value


def proposal(settings):
    policy = retirement.Policy.load(settings.retention_policy)
    p = archive.Profile.load(policy.archive_profile)
    boundary = scope(settings, policy, p)
    return {
        "schema": 1,
        "purpose": PURPOSE,
        "grant_kind": "recurring",
        "action": "run",
        "enabled": False,
        "approval_reference": None,
        "approved_at": None,
        "scope": boundary,
        "scope_id": sha256(json_bytes(boundary)),
    }


def manual_proposal(settings, plan_id, *, resume=False):
    """Produce a disabled, exact-plan request; never write or grant authority."""
    require(re.fullmatch(archive.HEX, plan_id or ""), "Full plan ID required")
    policy = retirement.Policy.load(settings.retention_policy)
    agent = retirement.Retirement(policy, settings.state_root / "proposal-commands")
    agent.root = settings.state_root / "retention"
    plan = agent.load_plan(plan_id)
    agent.check_code(plan)
    require(plan.get("clock_safety", {}).get("policy") == clock.POLICY, "T05 plan required")
    result = proposal(settings)
    require(plan.get("automation", {}).get("scope_id") == result["scope_id"], "Plan scope differs")
    result.update(
        grant_kind="exact_plan",
        action="apply-plan",
        plan_id=plan_id,
        approved_boot_id=clock.boot_id(),
        resume_interrupted=resume,
    )
    return result


def authorize(settings, policy, profile, action, plan_id=None):
    expected = scope(settings, policy, profile)
    if (
        retirement.Policy.load(settings.retention_policy) != policy
        or archive.Profile.load(policy.archive_profile) != profile
    ):
        raise legacy.Deferred("configuration_changed", "Configuration changed", attention=True)
    path = (
        settings.manual_authorization_file
        if action == "apply-plan"
        else settings.authorization_file
    )
    if not plain_path(path).is_file():
        raise legacy.Deferred("authorization_missing", "New T05 approval required", attention=True)
    if action != "preview":
        # Operator approvals are root-owned, read-only to the service account.
        clock.trusted(path)
        for config in (
            settings.retention_policy,
            policy.archive_profile,
            settings.clock_binding_file,
        ):
            # Archive profile contains only paths and IDs, never the password.
            # Its existing user ownership is preserved and bound by fingerprint.
            if config != policy.archive_profile:
                clock.trusted(config)
        clock.trusted(settings.unit_directory)
        clock.trusted(legacy.checkpoint_code.CODE_ROOT)
    grant = json.loads(path.read_bytes())
    expected_kind = "exact_plan" if action == "apply-plan" else "recurring"
    expected_action = "apply-plan" if action == "apply-plan" else "run"
    if (
        grant.get("schema") != 1
        or grant.get("purpose") != PURPOSE
        or grant.get("grant_kind") != expected_kind
        or grant.get("action") != expected_action
        or grant.get("scope") != expected
        or grant.get("scope_id") != sha256(json_bytes(expected))
    ):
        raise legacy.Deferred(
            "authorization_scope_changed", "Review code/clock/system-unit scope", attention=True
        )
    require(type(grant.get("enabled")) is bool, "Explicit approval enabled flag")
    if action != "preview" and not grant["enabled"]:
        raise legacy.Deferred(
            "authorization_pending", "Deletion has not been approved", attention=True
        )
    if grant["enabled"]:
        require(bool(grant.get("approval_reference")), "Approval reference required")
        require(archive.timestamp(grant["approved_at"]) <= now(), "Future approval")
    if action == "apply-plan":
        require(
            grant.get("plan_id") == plan_id and grant.get("action") == "apply-plan",
            "Exact manual plan approval required",
        )
        require(
            grant.get("approved_boot_id") == clock.boot_id(), "Manual approval is for another boot"
        )
        require(type(grant.get("resume_interrupted")) is bool, "Explicit manual recovery decision")
    return {
        "scope_id": grant["scope_id"],
        "enabled": grant["enabled"],
        "approval_reference": grant.get("approval_reference"),
        "approved_at": grant.get("approved_at"),
        "plan_id": grant.get("plan_id"),
        "grant_sha256": sha256(path.read_bytes()),
        "resume_interrupted": grant.get("resume_interrupted", False),
    }


def protected_by_time(lower_utc_ns, completion_upper_utc_ns, hours):
    return (
        lower_utc_ns - completion_upper_utc_ns
        < (hours * 3600 + clock.POLICY["retention_margin_seconds"]) * 10**9
    )


class ClockRetirement(retirement.Retirement):
    def __init__(self, policy, reports, settings, action, day, report, guard, manual_plan=None):
        super().__init__(policy, reports)
        self.history_root = self.root
        self.root = settings.state_root / "retention"
        require(
            not retirement.overlap(self.root, self.history_root), "Separate old and new journals"
        )
        self.settings, self.action, self.day, self.report = settings, action, day, report
        self.guard, self.manual_plan = guard, manual_plan
        self.decision = None

    def load_plan(self, pid, *, current_config=True):
        if not current_config and not (self.root / "plans" / f"{pid}.json").exists():
            original = self.root
            try:
                self.root = self.history_root
                return super().load_plan(pid, current_config=False)
            finally:
                self.root = original
        return super().load_plan(pid, current_config=current_config)

    def journals(self):
        new = super().journals()
        original = self.root
        try:
            self.root = self.history_root
            old = super().journals()
        finally:
            self.root = original
        require(
            not ({j["plan_id"] for j in new} & {j["plan_id"] for j in old}),
            "Duplicate journal identity",
        )
        return old + new

    @contextmanager
    def locks(self):
        with super().locks():
            self.report["authorization"] = authorize(
                self.settings, self.policy, self.p, self.action, self.manual_plan
            )
            self.guard.check(fresh=True)
            if now().astimezone(legacy.ZONE).date().isoformat() != self.day:
                raise legacy.Deferred("date_changed", "Re-evaluate on a new day")
            pending = [j["plan_id"] for j in self.journals() if j["status"] != "complete"]
            # Interrupted execution is never automatically resumed, including
            # an invocation which died before creating the original journal.
            recovery = (
                self.action == "apply-plan" and self.report["authorization"]["resume_interrupted"]
            )
            if pending and not (recovery and pending == [self.manual_plan]):
                raise legacy.Deferred(
                    "unfinished_retirement",
                    "Inspect prior plan; explicit recovery required",
                    attention=True,
                )
            complete = {j["plan_id"] for j in self.journals() if j["status"] == "complete"}
            for path in sorted((self.settings.state_root / "runs").glob("*/run.json")):
                prior = json.loads(plain_path(path).read_bytes())
                if (
                    prior["run_id"] != self.report["run_id"]
                    and prior.get("action") in {"run", "apply-plan"}
                    and prior.get("deletion_attempted")
                    and prior.get("plan_id") not in complete
                    and not (recovery and prior.get("plan_id") == self.manual_plan)
                ):
                    raise legacy.Deferred(
                        "unfinished_retirement", "Prior deletion needs review", attention=True
                    )
            # Preserve the old pre-journal unresolved-run guard as well.
            for path in sorted((self.p.reports_root / "checkpoint-scheduled").glob("*/run.json")):
                prior = json.loads(plain_path(path).read_bytes())
                if (
                    prior.get("action") == "run"
                    and prior.get("state") == "running"
                    and prior.get("plan_id")
                    and prior["plan_id"] not in complete
                ):
                    raise legacy.Deferred(
                        "unfinished_retirement", "Prior automatic run needs review", attention=True
                    )
            state = self.a.state
            if state.get("active_operation") or not (
                state.get("last_operation") or {"ok": True}
            ).get("ok"):
                raise legacy.Deferred(
                    "archive_unfinished_or_failed", "Archive operation needs review", attention=True
                )
            entries, incomplete = archive.discover(self.p)
            if incomplete:
                raise legacy.Deferred(
                    "checkpoint_incomplete", "Incomplete checkpoint", attention=True
                )
            records = state["checkpoints"]
            if any(r.get("error") for r in records.values()):
                raise legacy.Deferred(
                    "checkpoint_error", "Unresolved archive error", attention=True
                )
            if any(
                not records.get(e["backup_id"], {}).get("stored")
                or not records[e["backup_id"]].get("input_verified_at")
                for e in entries
            ):
                raise legacy.Deferred("unarchived_checkpoint", "Verified archive pending")
            self.report["daily_evidence"] = legacy.daily_evidence(self.p, state, self.day)
            yield

    def select(self, entries, instant):
        # Base audit still verifies archive/recovery/locks; calendar sorting is
        # not the T05 retention decision. Actual selection follows below.
        return [{**e, "protection": [], "age_seconds": 0} for e in entries], []

    def archived_graph(self):
        objects, locations = {}, {}
        checked = self.a.state["repository_check"]["snapshot_ids"]
        for record in self.a.state["checkpoints"].values():
            stored = record["stored"]
            binding = stored.get("checkpoint_evidence")
            if not binding:
                continue
            sid = stored["snapshot_id"]
            require(sid in checked, "Completion evidence snapshot not fully checked")
            raw, _ = self.a.restic.command(["dump", sid, "/" + evidence.SIDECAR])
            require(sha256(raw) == binding["sha256"], "Archived evidence hash differs")
            entry = record["entry"]
            ident = {k: entry[k] for k in ("backup_id", "manifest_sha256", "source_root")}
            ident["scope"] = "catalog_and_all_referenced_raw_revisions_snapshots"
            bundle = evidence.decode_bundle(raw, ident)
            require(
                bundle["object_ids"] == binding["object_ids"], "Archived evidence inventory differs"
            )
            require(bundle["bundle"]["scope"] == self.p.identity, "Archived evidence scope differs")
            for digest, value in bundle["bundle"]["objects"].items():
                require(
                    digest not in objects or objects[digest] == value,
                    "Conflicting archived evidence",
                )
                objects[digest] = value
                locations.setdefault(digest, []).append(
                    {"snapshot_id": sid, "bundle_sha256": binding["sha256"]}
                )
        return evidence.Graph(objects, self.p.identity), locations

    def audit(self, *, resume=None):
        result = super().audit(resume=resume)
        entries = result["keep"] + result["candidates"]
        graph, locations = self.archived_graph()
        current = self.guard.check(fresh=True)
        keep, candidates, proofs = evidence.select(
            entries,
            graph,
            current["lower_utc_ns"],
            keep_latest=self.policy.keep_latest,
            min_age_hours=self.policy.min_age_hours,
        )
        result.update(keep=keep, candidates=candidates)
        self.decision = {
            "policy": clock.POLICY,
            "observation": current,
            "objects": graph.objects,
            "locations": locations,
            "completion_bounds": proofs,
        }
        return result

    def plan_details(self, audit):
        self.guard.check()
        return {"clock_safety": self.decision, "run_id": self.report["run_id"]}

    def check_code(self, plan):
        super().check_code(plan)
        require(
            plan.get("clock_safety", {}).get("policy") == clock.POLICY,
            "T05 clock evidence required",
        )
        self.report["authorization"] = authorize(
            self.settings, self.policy, self.p, self.action, self.manual_plan
        )
        recovery = (
            self.action == "apply-plan" and self.report["authorization"]["resume_interrupted"]
        )
        self.guard.check(anchor=None if recovery else plan["clock_safety"]["observation"])
        if self.action == "apply-plan":
            require(plan["plan_id"] == self.manual_plan, "Manual plan changed")
        else:
            require(plan.get("run_id") == self.report["run_id"], "Plan belongs to another run")
        graph, locations = self.archived_graph()
        for digest, value in plan["clock_safety"]["objects"].items():
            require(
                graph.objects.get(digest) == value, "Approved completion/order evidence changed"
            )
            require(
                set(x["snapshot_id"] for x in locations[digest])
                >= set(x["snapshot_id"] for x in plan["clock_safety"]["locations"][digest]),
                "Approved evidence recovery point disappeared",
            )

    def plan(self):
        return super().plan(
            automation={
                "run_id": self.report["run_id"],
                "scope_id": self.report["authorization"]["scope_id"],
            }
        )

    def apply(self, pid, *, hook=lambda phase, item: None):
        plan = self.load_plan(pid)

        first_mutation = True

        def guarded(phase, item):
            nonlocal first_mutation
            recovery = (
                self.action == "apply-plan" and self.report["authorization"]["resume_interrupted"]
            )
            current = self.guard.check(
                fresh=first_mutation,
                anchor=None if recovery else plan["clock_safety"]["observation"],
            )
            first_mutation = False
            proof = plan["clock_safety"]["completion_bounds"][item["entry"]["backup_id"]][
                "completion"
            ]
            require(proof is not None, "Missing approved completion proof")
            if protected_by_time(
                current["lower_utc_ns"], proof["completion_upper_utc_ns"], self.policy.min_age_hours
            ):
                raise clock.ClockHold("Protection period is not proven at mutation")
            hook(phase, item)

        return super().apply(pid, hook=guarded)


def system_context(service, timer):
    return restic_schedule.service_context(service, timer, manager="system")


def installed_service(settings, service, action):
    """Bind the running system unit to the reviewed root-owned fragment."""
    raw = clock.command(
        [
            "systemctl",
            "--system",
            "show",
            service,
            "--property=FragmentPath,DropInPaths,NeedDaemonReload",
        ]
    )
    fields = {}
    for line in raw.splitlines():
        key, value = line.split("=", 1)
        require(key not in fields, "Duplicate unit property")
        fields[key] = value
    require(
        fields.get("NeedDaemonReload") == "no" and fields.get("DropInPaths") == "",
        "Unreviewed effective system unit",
    )
    actual = clock.trusted(fields["FragmentPath"])
    name = UNITS[3] if action == "apply-plan" else UNITS[0]
    require(
        actual.read_bytes() == (settings.unit_directory / name).read_bytes(),
        "Installed system unit differs from approved unit",
    )


def execute(settings, action, plan_id=None, *, host_factory=clock.Host):
    require(action in {"preview", "run", "apply-plan"}, "Unknown T05 action")
    require(
        (action == "apply-plan") == (plan_id is not None), "Only manual application takes a plan"
    )
    if plan_id is not None:
        require(re.fullmatch(archive.HEX, plan_id), "Full plan ID required")
    rid = uuid.uuid4().hex
    out = settings.state_root / "runs" / rid
    retirement.durable_directory(out)
    path = out / "run.json"
    report = {
        "schema": 1,
        "run_id": rid,
        "action": action,
        "started_at": now(),
        "state": "running",
        "phase": "preflight",
        "ok": False,
        "plan_id": plan_id,
        "selection_assessed": False,
        "deletion_attempted": False,
        "deleted_checkpoints": 0,
        "completed_before_run": 0,
        "deleted_this_run": 0,
        "already_complete": False,
        "needs_attention": False,
        "report": str(path),
        "next_evaluation": "next calendar day; no automatic resume",
    }
    retirement.persist(path, report)
    guard, agent = None, None
    try:
        policy = retirement.Policy.load(settings.retention_policy)
        profile = archive.Profile.load(policy.archive_profile)
        with legacy.runner_lock(settings.state_root / "runner"):
            report["authorization"] = authorize(settings, policy, profile, action, plan_id)
            if action != "preview":
                service = (
                    legacy.SERVICE
                    if action == "run"
                    else f"marketdata-checkpoint-retirement-apply@{plan_id}.service"
                )
                context = system_context(service, legacy.TIMER)
                report["context"] = context
                if not context.get("service_verified"):
                    raise legacy.Deferred(
                        "unconfirmed_service", "Verified system service required", attention=True
                    )
                installed_service(settings, service, action)
                if action == "run" and not context.get("scheduled"):
                    raise legacy.Deferred(
                        "unconfirmed_timer", "New grant requires system timer", attention=True
                    )
                if action == "apply-plan" and context.get("scheduled"):
                    raise legacy.Deferred(
                        "manual_trigger_required",
                        "Manual approval is not recurring authority",
                        attention=True,
                    )
                if action == "apply-plan" and (
                    context.get("classification") != "manual_service_or_unknown"
                    or context.get("start_job", {}).get("activation_details") != {}
                    or context.get("provenance_error")
                ):
                    raise legacy.Deferred(
                        "manual_trigger_unconfirmed",
                        "Cannot prove manual start job",
                        attention=True,
                    )
            binding = json.loads(settings.clock_binding_file.read_bytes())
            with host_factory(binding).lock() as host:
                guard = clock.Guard(host.query, out / "clock.jsonl").start()
                current = guard.check()
                day = now().astimezone(legacy.ZONE).date().isoformat()
                report.update(date_kst=day, initial_clock=current)
                if action == "run":
                    trigger = archive.timestamp(context["trigger_at"]).astimezone(legacy.ZONE)
                    require(
                        trigger.date().isoformat() == day
                        and (trigger.hour, trigger.minute) == (15, 40),
                        "Expected today's 15:40 trigger",
                    )
                    require(archive.timestamp(context["trigger_at"]) <= now(), "Future trigger")
                agent = ClockRetirement(
                    policy, out / "commands", settings, action, day, report, guard, plan_id
                )
                plan = agent.load_plan(plan_id) if action == "apply-plan" else agent.plan()
                report.update(
                    plan_id=plan["plan_id"], plan=plan, selection_assessed=True, phase="planned"
                )
                retirement.persist(path, report)
                if not plan["candidates"]:
                    report.update(state="no_candidates", ok=True)
                elif action == "preview":
                    report.update(state="preview_ready", ok=True)
                else:
                    previous = agent.root / "journals" / f"{plan['plan_id']}.json"
                    if previous.exists():
                        report["completed_before_run"] = sum(
                            item["phase"] == "complete"
                            for item in json.loads(previous.read_bytes())["items"].values()
                        )
                    report.update(phase="applying", deletion_attempted=True)
                    retirement.persist(path, report)
                    result = agent.apply(plan["plan_id"])
                    report.update(
                        state="complete",
                        ok=True,
                        result=result,
                        already_complete=result.get("already_complete", False),
                        deleted_checkpoints=len(result["journal"]["items"]),
                    )
                guard.check()
    except (clock.ClockHold, legacy.Deferred, WriterBusy, archive.Busy, CheckpointBusy) as exc:
        report.update(
            state="deferred",
            ok=False,
            reason=getattr(exc, "reason", type(exc).__name__),
            detail=str(exc),
            needs_attention=getattr(exc, "attention", False),
        )
    except BaseException as exc:
        report.update(
            state="failed", ok=False, needs_attention=True, error=f"{type(exc).__name__}: {exc}"
        )
    finally:
        if guard:
            try:
                guard.close()
            except Exception as exc:
                report.update(state="failed", ok=False, needs_attention=True, error=str(exc))
        if agent and report["plan_id"]:
            journal = agent.root / "journals" / f"{report['plan_id']}.json"
            if journal.exists():
                value = json.loads(journal.read_bytes())
                report["retirement_journal"] = str(journal)
                report["deleted_checkpoints"] = sum(
                    i["phase"] == "complete" for i in value["items"].values()
                )
                report["unfinished_retirement"] = value["status"] != "complete"
                report["deleted_this_run"] = max(
                    0, report["deleted_checkpoints"] - report["completed_before_run"]
                )
                if report["unfinished_retirement"]:
                    report.update(state="failed", ok=False, needs_attention=True)
        report.update(
            finished_at=now(), phase="finished", exit_code=legacy.EXIT_CODES[report["state"]]
        )
        retirement.persist(path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument(
        "action", choices=("propose", "propose-manual", "preview", "apply-plan", "run")
    )
    parser.add_argument("--plan-id")
    parser.add_argument("--resume-interrupted", action="store_true")
    args = parser.parse_args()
    try:
        settings = Settings.load(args.settings)
        require(
            not args.resume_interrupted or args.action == "propose-manual",
            "Recovery is proposed and separately approved, never enabled by a run flag",
        )
        if args.action in {"apply-plan", "run"}:
            clock.trusted(args.settings)
        if args.action == "propose":
            result = proposal(settings)
        elif args.action == "propose-manual":
            result = manual_proposal(settings, args.plan_id, resume=args.resume_interrupted)
        else:
            result = execute(settings, args.action, args.plan_id)
        print(json_bytes(result).decode())
        return result.get("exit_code", 0)
    except Exception as exc:
        print(json_bytes({"state": "failed", "error": f"{type(exc).__name__}: {exc}"}).decode())
        return 1


if __name__ == "__main__":
    sys.exit(main())
