"""Explicit baseline registration, posthoc witnessing, and evidenced daily backup.

inventory is read-only apart from existing cooperative locks and explicit report
output. register/observe require their own exact-inventory approval, never a
retirement grant. backup never registers a missing baseline on its own.
"""

import argparse
import json
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from marketdata import backup, maintenance
from marketdata import job_clock as jc
from marketdata import checkpoint_clock as clock
from marketdata import checkpoint_evidence as evidence
from marketdata.checkpoint_locks import checkpoint_locks, plain_path
from marketdata.domain import json_bytes, now, sha256
from marketdata.store import Store

from scripts import archive_backups as archive
from scripts import checkpoint_code
from scripts.clock_retirement import Settings
from scripts.restic_trial import require
from scripts.retire_checkpoints import Policy


def profile(settings):
    return archive.Profile.load(Policy.load(settings.retention_policy).archive_profile)


def registry(settings, p):
    return evidence.Registry(
        settings.state_root / "evidence", p.identity, checkpoint_code.current_code()["code_id"]
    )


@contextmanager
def inputs(p):
    p.guard()
    with (
        maintenance.maintenance_lock(SimpleNamespace(root=p.store_root)),
        archive.repository_lock(p),
    ):
        entries, incomplete = archive.discover(p)
        require(not incomplete, "Incomplete checkpoint requires separate review")
        state = archive.load_state(p)
        checked = state.get("repository_check") or {}
        require(
            checked.get("ok") and checked.get("read_data") and checked.get("status") == "complete",
            "Full archive check required",
        )
        require(
            not state.get("active_operation")
            and (state.get("last_operation") or {"ok": True}).get("ok"),
            "Unresolved archive operation",
        )
        yield entries, state


def inventory_value(settings, p, entries, state):
    identities = []
    for entry in entries:
        record = state["checkpoints"].get(entry["backup_id"], {})
        require(
            record.get("entry") == entry
            and record.get("input_verified_at")
            and not record.get("error"),
            "Unverified checkpoint identity",
        )
        require(
            (record.get("stored") or {}).get("snapshot_id")
            in state["repository_check"]["snapshot_ids"],
            "Checkpoint not stored and fully checked",
        )
        require(archive.checkpoint(Path(entry["path"]), p) == entry, "Manifest changed")
        identities.append(
            {
                "backup_id": entry["backup_id"],
                "manifest_sha256": entry["manifest_sha256"],
                "source_root": entry["source_root"],
                "scope": backup.SCOPE,
            }
        )
    value = {
        "schema": 1,
        "purpose": "checkpoint-evidence-registration-v1",
        "scope": p.identity,
        "settings_sha256": sha256(json_bytes({k: str(v) for k, v in vars(settings).items()})),
        "profile_sha256": p.fingerprint,
        "policy": clock.POLICY,
        "code": checkpoint_code.current_code(),
        "entries": sorted(identities, key=lambda i: i["backup_id"]),
    }
    value["plan_id"] = sha256(json_bytes(value))
    return value


def inventory(settings):
    p = profile(settings)
    with inputs(p) as (entries, state), checkpoint_locks([e["path"] for e in entries]):
        return inventory_value(settings, p, entries, state)


def fresh_observer(settings, *, host_factory=clock.Host):
    def observe():
        binding = json.loads(plain_path(settings.clock_binding_file).read_bytes())
        output = settings.state_root / "evidence-observations" / (uuid.uuid4().hex + ".jsonl")
        evidence.mkdir(output.parent)
        with host_factory(binding).lock() as host:
            guard = clock.Guard(host.query, output)
            try:
                return guard.check(fresh=True)
            finally:
                guard.close()

    return observe


def register(settings, plan, approval, *, action="register", observer=None):
    require(action in {"register", "observe"}, "Evidence action differs")
    require(
        approval.get("purpose") == "checkpoint-evidence-registration-v1"
        and approval.get("enabled") is True
        and approval.get("action") == action
        and approval.get("plan_id") == plan["plan_id"]
        and bool(approval.get("approval_reference")),
        "Explicit evidence registration approval required",
    )
    p = profile(settings)
    reg = registry(settings, p)
    with (
        inputs(p) as (entries, state),
        reg.lock(create=True),
        checkpoint_locks([e["path"] for e in entries]),
    ):
        require(
            inventory_value(settings, p, entries, state) == plan,
            "Inventory changed; review a new plan",
        )
        if action == "register":
            reg.baseline(plan["entries"])
        else:
            require(bool(reg.graph().baselines), "No registered baseline")
        observe = observer or fresh_observer(settings)
        results = {}
        for ident in plan["entries"]:
            try:
                results[ident["backup_id"]] = {
                    "object_id": reg.observe(ident, observe),
                    "state": "available",
                }
            except Exception as exc:
                results[ident["backup_id"]] = {
                    "state": "missing",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
        evidence.immutable(
            reg.root
            / "registration-receipts"
            / (plan["plan_id"] + "-" + uuid.uuid4().hex + ".json"),
            {
                "plan": plan,
                "approval": approval,
                "results": results,
                "finished_at": now(),
                "deletion_authority": False,
            },
        )
        return {"registered": True, "results": results, "deletion_authority": False}


def run_backup(settings, *, min_free_gib=10, max_files=20000, observer=None):
    p = profile(settings)
    p.guard()
    producer = evidence.Producer(registry(settings, p), observer or fresh_observer(settings))
    store = Store(p.store_root)
    try:
        return maintenance.run(
            store,
            mode="daily",
            min_free_gib=min_free_gib,
            max_files=max_files,
            backup_dir=p.backup_dir,
            backup_mount=p.backup_mount,
            incremental=True,
            wait_for_lock=True,
            evidence=producer,
        )
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("action", choices=("inventory", "register", "observe", "backup", "inspect"))
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--min-free-gib", type=float, default=10)
    parser.add_argument("--max-files", type=int, default=20000)
    args = parser.parse_args()
    try:
        if args.action == "inspect":
            result = evidence.decode_bundle(plain_path(args.bundle).read_bytes())
        else:
            settings = Settings.load(args.settings)
            if args.action == "inventory":
                result = inventory(settings)
            else:
                clock.trusted(args.settings)
                clock.trusted(settings.clock_binding_file)
                clock.trusted(checkpoint_code.CODE_ROOT)
                if args.action == "backup":
                    result = run_backup(
                        settings, min_free_gib=args.min_free_gib, max_files=args.max_files
                    )
                else:
                    approval = json.loads(clock.trusted(args.approval).read_bytes())
                    result = register(
                        settings,
                        json.loads(plain_path(args.plan).read_bytes()),
                        approval,
                        action=args.action,
                    )
        if args.action == "backup":
            p = profile(settings)
            history = p.store_root / f"reports/maintenance/{result['started_at']:%Y-%m-%d}/{result['id']}.json"
            jc.finished("backup", result, files=[history, Path(result["backup"]["path"]) / "backup.json"])
        print(json_bytes(result).decode())
        return 2 if args.action == "backup" and not result.get("ok") else 0
    except (jc.ClockDeferred, jc.ClockFailure):
        raise
    except Exception as exc:
        print(json_bytes({"ok": False, "error": f"{type(exc).__name__}: {exc}"}).decode())
        return 1


if __name__ == "__main__":
    sys.exit(main())
