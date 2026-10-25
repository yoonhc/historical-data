"""T18 approved source-only install, connection transition, timer resume/stop/rollback.

Default is inspection. An exact operation approval is required for any mutation.
This program never issues a business service start/stop, reset-failed or deletion.
"""

import argparse
import fcntl
import importlib.util
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("handoff_host", HERE / "native_host.py")
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)
op, c = n.op, n.c


def checked():
    plan = json.loads((HERE / "operation-plan.json").read_bytes())
    op.validate(plan)
    op.require(plan.get("ready_for_separate_approval") is True, "Candidate is not finalized")
    for name, digest in plan["tool_files"].items():
        op.require(
            not Path(name).is_absolute() and ".." not in Path(name).parts, "Unsafe tool path"
        )
        op.require(c.file_sha(HERE / name) == digest, "Tool differs: " + name)
    for name, digest in plan["evidence_files"].items():
        op.require(c.file_sha(HERE / name) == digest, "Validation evidence differs: " + name)
    preflight = json.loads((HERE / plan["system_preflight"]).read_bytes())
    op.require(
        preflight["passed"]
        and preflight["operating_unchanged"]
        and preflight["cleanup"]["paths_absent"]
        and not preflight["cleanup"]["errors"],
        "Administrator preflight is incomplete",
    )
    op.require(
        preflight["preparation_id"] == plan["prepared"]["preparation_id"],
        "Preflight preparation differs",
    )
    op.require(
        Path("/proc/sys/kernel/random/boot_id").read_text().strip() == plan["prepared"]["boot_id"],
        "Boot changed; explicit plan review needed",
    )
    return plan


def seal(plan, args):
    root = Path(op.STATE)
    n.safe(root.parent)
    root.mkdir(exist_ok=True, mode=0o755)
    n.safe(root)
    directory = root / plan["operation_id"]
    directory.mkdir(exist_ok=True, mode=0o755)
    n.safe(directory)
    target = directory / "tools"
    target.mkdir(exist_ok=True, mode=0o755)
    n.safe(target)
    files = {
        **plan["tool_files"],
        **plan["evidence_files"],
        "operation-plan.json": c.file_sha(HERE / "operation-plan.json"),
    }
    for name, digest in files.items():
        destination = target / name
        destination.parent.mkdir(exist_ok=True, parents=True, mode=0o755)
        if not destination.exists():
            raw = (HERE / name).read_bytes()
            op.require(c.sha(raw) == digest, "Source changed while sealing")
            n.atomic(destination, raw)
        n.safe(destination)
        op.require(c.file_sha(destination) == digest, "Sealed tool differs")
    for name, item in plan["prepared"]["release_entries"].items():
        destination = target / "payload" / name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if not destination.exists():
            raw = Path(item["source"]).read_bytes()
            op.require(c.sha(raw) == item["sha256"], "Payload changed while sealing")
            n.atomic(destination, raw)
        n.safe(destination)
        op.require(c.file_sha(destination) == item["sha256"], "Sealed payload differs")
    os.execv(
        "/usr/bin/python3",
        [
            "/usr/bin/python3",
            "-B",
            "-I",
            str(target / "handoff.py"),
            "--sealed",
            "--action",
            args.action,
            "--approve-operation",
            args.approve_operation,
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--action", choices=("inspect", "check", "apply", "stop", "rollback"), default="inspect"
    )
    parser.add_argument("--approve-operation")
    parser.add_argument("--sealed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    plan = checked()
    if args.action == "inspect":
        print(
            json.dumps(
                {
                    "operation_id": plan["operation_id"],
                    "release_id": plan["prepared"]["release_id"],
                    "expected_scope": plan["prepared"]["recurring_proposal_after"]["scope_id"],
                    "operating_applied": False,
                    "approval_required": True,
                },
                indent=2,
            )
        )
        return 0
    op.require(os.geteuid() == 0, "Native root context required")
    directory = Path(op.STATE) / plan["operation_id"]
    if args.action == "check":
        # No operation directories, authority, receipts or locks are created.
        host = n.Host(plan, directory if directory.exists() else Path("/var/lib/marketdata-clock"))
        host.immutable()
        positions = {
            path: [
                side
                for side, value in pair.items()
                if Path(path).read_bytes() == value["text"].encode()
            ]
            for path, pair in plan["prepared"]["changes"].items()
        }
        print(
            json.dumps(
                {
                    "operation_id": plan["operation_id"],
                    "files": positions,
                    "units": host.show((*op.SERVICES, *op.TIMERS)),
                    "ledger": json.loads(Path(c.LEDGER).read_bytes()),
                    "operation_state": host.read("state.json") if directory.exists() else None,
                    "transition": host.read("transaction.json") if directory.exists() else None,
                    "grant_sha256": c.file_sha(c.GRANT),
                    "mutation": False,
                },
                indent=2,
            )
        )
        return 0
    op.require(args.approve_operation == plan["operation_id"], "Exact separate approval required")
    if not args.sealed:
        seal(plan, args)
    op.require(
        HERE == directory / "tools" and re.fullmatch(r"[0-9a-f]{64}", directory.name),
        "Unexpected sealed location",
    )
    for path in (HERE, *HERE.rglob("*")):
        n.safe(path)
    lock = Path("/var/lib/marketdata-clock/t18-output-handoff.lock")
    n.safe(lock, exists=False)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(
                json.dumps(
                    {
                        "operation_id": plan["operation_id"],
                        "state": "held",
                        "reason": "handoff_lock_busy",
                        "operating_configuration_changed": False,
                    }
                )
            )
            return 75
        host = n.Host(plan, directory)
        report = {
            "operation_id": plan["operation_id"],
            "action": args.action,
            "started_at": host.now(),
            "passed": False,
            "clock_control": False,
            "checkpoint_deletion_requested": False,
        }
        try:
            host.immutable()
            report["before"] = host.show(c.PROCESS_UNITS)
            state = op.Operation(plan, host).run(args.action)
            report.update(passed=True, result=state)
        except (op.Hold, BlockingIOError, subprocess.TimeoutExpired) as exc:
            report.update(
                state="held",
                error=type(exc).__name__ + ": " + str(exc),
                running_bodies_forced=False,
            )
        except Exception as exc:
            report.update(state="failed", error=type(exc).__name__ + ": " + str(exc))
        finally:
            report["finished_at"] = host.now()
            try:
                report["after"] = host.show(c.PROCESS_UNITS)
                report["protected_processes_unchanged"] = report.get("before") == report["after"]
            except Exception as exc:
                report["protected_processes_unchanged"] = None
                report["final_observation_error"] = type(exc).__name__ + ": " + str(exc)
            if not report["protected_processes_unchanged"]:
                report["passed"] = False
            name = "attempt-" + uuid.uuid4().hex + ".json"
            host.save(name, report, immutable=True)
            print(
                json.dumps(
                    {k: v for k, v in report.items() if k not in ("before", "after")}, indent=2
                )
            )
            print("Root evidence: " + host.record_path(name))
        return 0 if report["passed"] else 75 if report.get("state") == "held" else 1
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
