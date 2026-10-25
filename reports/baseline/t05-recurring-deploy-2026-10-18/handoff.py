"""Explicit approved T05 operation. Default/check is read-only.

Apply never requests the retirement service; it starts only the reviewed
calendar timer. The plan's recurring grant authorises future runtime plans.
"""

# ruff: noqa: E402 -- isolated Python explicitly imports the reviewed local tools.
import argparse
import json
import os
import sys
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine
import host
import installation
from vendor import handoff_common as c


def verified_plan():
    plan = json.loads((HERE / "operation-plan.json").read_bytes())
    c.require(engine.operation_id(plan) == plan["operation_plan_id"], "Operation digest differs")
    for name, digest in plan["tool_files"].items():
        c.require(c.sha((HERE / name).read_bytes()) == digest, "Tool differs: " + name)
    return plan


def files_for(plan):
    root = "/var/lib/marketdata-clock/t05-recurring-operations/" + plan["operation_plan_id"]
    return c.SafeFiles("/", plan["changes"], {}, control_prefix=root)


def source_check(plan):
    for name, spec in plan["release_entries"].items():
        source = Path(spec["source"])
        if spec["kind"] == "symlink":
            c.require(
                source.is_symlink() and os.readlink(source) == spec["target"],
                "Candidate link differs: " + name,
            )
        else:
            c.require(
                source.is_file()
                and not source.is_symlink()
                and c.sha(source.read_bytes()) == spec["sha256"],
                "Candidate differs: " + name,
            )
    for path, digest in plan["verification_inputs"].items():
        c.require(
            c.sha(Path(path).read_bytes()) == digest, "Reviewed verification differs: " + path
        )


def seal_tool(plan):
    target = Path("/opt/marketdata/tools") / plan["tool_id"]
    entries = {
        name: {"kind": "file", "source": str(HERE / name), "sha256": digest, "mode": 0o644}
        for name, digest in plan["tool_files"].items()
    }
    entries["operation-plan.json"] = {
        "kind": "file",
        "source": str(HERE / "operation-plan.json"),
        "sha256": c.sha((HERE / "operation-plan.json").read_bytes()),
        "mode": 0o644,
    }
    if HERE == target:
        installation.verify(target, entries, plan["tool_id"] + ":" + plan["operation_plan_id"])
        return
    installation.install(target, entries, plan["tool_id"] + ":" + plan["operation_plan_id"])
    # Do not execute a user-writable deployment helper after granting authority.
    os.execv(
        "/usr/bin/python3",
        ["/usr/bin/python3", "-B", "-I", str(target / "handoff.py"), *sys.argv[1:]],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("check", "apply", "stop", "rollback"), default="check")
    parser.add_argument("--approve-operation")
    args = parser.parse_args()
    plan = verified_plan()
    files = files_for(plan)
    adapter = host.Host(plan, files, HERE)
    if args.action == "check":
        c.require(args.approve_operation is None, "Check does not consume approval")
        source_check(plan)
        adapter.invariants(activation=True)
        nodes = {p: files.node(p) == item["before"] for p, item in plan["changes"].items()}
        c.require(all(nodes.values()), "Initial configuration differs")
        for p in nodes:
            adapter.permissions(p)
        adapter.require_timer_idle()
        adapter.require_idle()
        adapter.lock_metadata()
        blockers = adapter.unfinished(review_only=True)
        print(
            json.dumps(
                {
                    "passed": not blockers,
                    "read_only": True,
                    "operation_plan_id": plan["operation_plan_id"],
                    "before_paths": len(nodes),
                    "release_objects": len(plan["release_entries"]),
                    "unfinished": blockers,
                    "installed": False,
                    "activated": False,
                },
                indent=2,
            )
        )
        return 0 if not blockers else 75
    c.require(
        os.geteuid() == 0, "Run this exact operation from an authenticated administrator terminal"
    )
    c.require(
        args.approve_operation == plan["operation_plan_id"], "Exact operation approval required"
    )
    # The root-owned tool is installed before entering production operations.
    # Its installer is independently resumable under the same operation lock.
    if HERE != Path("/opt/marketdata/tools") / plan["tool_id"]:
        with host.operation_lock():
            seal_tool(plan)
    seal_tool(plan)
    with host.operation_lock():
        if args.action == "apply":
            source_check(plan)
        event = {
            "operation_plan_id": plan["operation_plan_id"],
            "action": args.action,
            "passed": False,
            "clock_control": False,
            "checkpoint_deletion_requested": False,
        }
        try:
            event["protected_processes_before"] = host.protected_processes(adapter.m)
            event["result"] = engine.Engine(plan, files, adapter).run(
                args.action, args.approve_operation
            )
            event["protected_processes_after"] = host.protected_processes(adapter.m)
            event["protected_processes_unchanged"] = (
                event["protected_processes_before"] == event["protected_processes_after"]
            )
            event["passed"] = True
        except Exception as exc:
            event["error"] = type(exc).__name__ + ": " + str(exc)
            event["traceback"] = traceback.format_exc()
        finally:
            path = adapter.root + "/attempts/" + uuid.uuid4().hex + ".json"
            event["report"] = path
            files.save(path, event, immutable=True)
            print(json.dumps(event, indent=2), flush=True)
        return 0 if event["passed"] else 75


if __name__ == "__main__":
    raise SystemExit(main())
