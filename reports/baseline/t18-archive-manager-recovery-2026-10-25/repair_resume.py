"""Separately approved adapter repair for one existing T18 handoff.

Preserves the original authority, tool seal, operation and failed attempt.
Repairs native job/command response handling and compares dependency sets without
ordering artifacts. Default is inspection.
"""

import argparse
import fcntl
import importlib.util
import json
import os
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("repair_native", HERE / "native_host.py")
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)
op, c = n.op, n.c
RECOVERIES = Path("/var/lib/marketdata-clock/t18-output-recoveries")


def checked():
    repair = json.loads((HERE / "recovery-plan.json").read_bytes())
    op.require(
        repair["recovery_id"] == c.sha(c.canonical({k: v for k, v in repair.items() if k != "recovery_id"})),
        "Recovery digest differs",
    )
    op.require(repair["purpose"] == "t18-manager-response-recovery-v2", "Wrong repair purpose")
    for name, digest in {**repair["files"], **repair["evidence_files"]}.items():
        op.require(not Path(name).is_absolute() and ".." not in Path(name).parts, "Unsafe file name")
        op.require(c.file_sha(HERE / name) == digest, "Recovery file differs: " + name)
    original = Path(op.STATE) / repair["original_operation_id"]
    n.safe(original)
    old = n.load("original_checked_handoff", original / "tools/handoff.py")
    plan = old.checked()
    op.require(plan["operation_id"] == repair["original_operation_id"], "Wrong original operation")
    op.require(c.file_sha(original / "tools/operation-plan.json") == repair["original_operation_sha256"], "Original plan changed")
    op.require((HERE / "operation-plan.json").read_bytes() == (original / "tools/operation-plan.json").read_bytes(), "Copied plan differs")
    return repair, plan, original


def initial_boundary(repair, directory):
    for name, digest in repair["initial_records"].items():
        op.require(c.file_sha(n.safe(directory / name)) == digest, "Recovery boundary changed: " + name)
    for path, digest in repair.get("initial_inputs", {}).items():
        op.require(c.file_sha(n.safe(Path(path))) == digest, "Recovery input changed: " + path)
    for name in repair.get("absent_records", ("transaction.json", "apply-receipt.json", "rollback-receipt.json")):
        op.require(not (directory / name).exists(), "Configuration already started: " + name)


def normalized_units(rows):
    # These dependency properties are sets; daemon-reload can reorder output.
    # All other fields, including PID, invocation and state, stay exact.
    return {unit: {key: sorted(value.split()) if key in ("After", "Requires") else value for key, value in row.items()} for unit, row in rows.items()}


def seal(repair, args):
    n.safe(RECOVERIES.parent)
    RECOVERIES.mkdir(exist_ok=True, mode=0o755)
    n.safe(RECOVERIES)
    root = RECOVERIES / repair["recovery_id"]
    root.mkdir(exist_ok=True, mode=0o755)
    n.safe(root)
    target = root / "tools"
    target.mkdir(exist_ok=True, mode=0o755)
    n.safe(target)
    files = {**repair["files"], **repair["evidence_files"], "recovery-plan.json": c.file_sha(HERE / "recovery-plan.json")}
    for name, digest in files.items():
        path = target / name
        op.require(path.parent == target, "Only flat sealed repair files are supported")
        raw = (HERE / name).read_bytes()
        op.require(c.sha(raw) == digest, "Recovery changed during seal")
        if not path.exists():
            n.atomic(path, raw)
        n.safe(path)
        op.require(c.file_sha(path) == digest, "Sealed recovery differs")
    os.execv("/usr/bin/python3", ["/usr/bin/python3", "-B", "-I", str(target / "repair_resume.py"), "--sealed", "--action", args.action, "--approve-recovery", args.approve_recovery])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("inspect", "resume", "rollback"), default="inspect")
    parser.add_argument("--approve-recovery")
    parser.add_argument("--sealed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    repair, plan, original = checked()
    if args.action == "inspect":
        print(json.dumps({"recovery_id": repair["recovery_id"], "original_operation_id": plan["operation_id"], "new_approval_required": True, "operating_mutations": False}, indent=2))
        return 0
    op.require(os.geteuid() == 0, "Native root context required")
    op.require(args.approve_recovery == repair["recovery_id"], "Exact separate recovery approval required")
    if not args.sealed:
        seal(repair, args)
    root = RECOVERIES / repair["recovery_id"]
    op.require(HERE == root / "tools", "Unexpected recovery seal")
    for path in (HERE, *HERE.iterdir()):
        n.safe(path)
    fd = os.open(n.safe(Path("/var/lib/marketdata-clock/t18-output-handoff.lock")), os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        host = n.Host(plan, original)
        control = root / "accepted.json"
        authority = original / "authority.json"
        op.require(c.file_sha(authority) == repair["authority_sha256"], "Original authority changed")
        if not control.exists():
            initial_boundary(repair, original)
            n.atomic(control, c.encode({"recovery_id": repair["recovery_id"], "accepted_at": host.now(), "original_authority_sha256": repair["authority_sha256"]}))
        else:
            accepted = json.loads(n.safe(control).read_bytes())
            op.require(accepted["recovery_id"] == repair["recovery_id"] and accepted["original_authority_sha256"] == repair["authority_sha256"], "Recovery acceptance differs")
        report = {"recovery_id": repair["recovery_id"], "original_operation_id": plan["operation_id"], "action": args.action, "started_at": host.now(), "passed": False, "clock_control": False, "checkpoint_deletion_requested": False}
        try:
            host.immutable()
            report["protected_before"] = host.show(c.PROCESS_UNITS)
            host.verify_release()
            if not (original / "transaction.json").exists():
                host.readonly_check()
            result = op.Operation(plan, host).run("apply" if args.action == "resume" else "rollback")
            report.update(passed=True, result=result)
        except (op.Hold, BlockingIOError) as exc:
            report.update(state="held", error=type(exc).__name__ + ": " + str(exc))
        except Exception as exc:
            report.update(state="failed", error=type(exc).__name__ + ": " + str(exc), traceback=traceback.format_exc())
        finally:
            report["finished_at"] = host.now()
            report["original_authority_unchanged"] = c.file_sha(authority) == repair["authority_sha256"]
            try:
                report["protected_after"] = host.show(c.PROCESS_UNITS)
                report["protected_processes_unchanged"] = bool(report.get("protected_before")) and normalized_units(report["protected_before"]) == normalized_units(report["protected_after"])
                report["timers_after"] = host.timers(op.TIMERS)
            except Exception as exc:
                report["protected_processes_unchanged"] = None
                report["final_observation_error"] = type(exc).__name__ + ": " + str(exc)
            if not report["original_authority_unchanged"] or not report["protected_processes_unchanged"]:
                report["passed"] = False
            name = "attempt-" + uuid.uuid4().hex + ".json"
            host.save(name, report, immutable=True)
            n.atomic(root / name, c.encode(report))
            print(json.dumps({k: v for k, v in report.items() if k not in ("protected_before", "protected_after", "timers_after", "traceback")}, indent=2))
            print("Root evidence: " + str(root / name))
        return 0 if report["passed"] else 75 if report.get("state") == "held" else 1
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
