"""Finalize only from passed native preflight and current isolated transition tests."""

import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("build_operation", HERE / "operation.py")
op = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op)
c = op.c


def main():
    prepared = json.loads((HERE / "prepared-plan.json").read_bytes())
    preflight_name = "system-preflight-result-c044d2ddabfe4623.json"
    preflight = json.loads((HERE / preflight_name).read_bytes())
    op.require(
        preflight["passed"]
        and preflight["operating_unchanged"]
        and preflight["preparation_id"] == prepared["preparation_id"],
        "Native preflight differs",
    )
    tests = {}
    for name in ("preflight-local-tests.xml", "transition-tests.xml", "native-adapter-tests.xml"):
        root = ET.parse(HERE / name).getroot()
        suites = list(root.iter("testsuite"))
        op.require(
            suites
            and all(
                int(s.attrib.get("failures", 0)) == 0 and int(s.attrib.get("errors", 0)) == 0
                for s in suites
            ),
            "Local tests are not passed: " + name,
        )
        tests[name] = {
            "sha256": c.file_sha(HERE / name),
            "tests": sum(int(s.attrib["tests"]) for s in suites),
        }
    tools = {
        n: c.file_sha(HERE / n)
        for n in ("common.py", "operation.py", "native_host.py", "handoff.py", "verify_requests.py")
    }
    evidence = {
        n: c.file_sha(HERE / n)
        for n in (
            preflight_name,
            "prepared-plan.json",
            "preflight-seal.json",
            "local-import-result.json",
            "timer-dependencies.txt",
            *tests,
        )
    }
    processes = {
        name: {
            k: preflight["after"]["units"][name][k]
            for k in ("MainPID", "InvocationID", "ActiveState")
        }
        for name in c.PROCESS_UNITS
    }
    plan = {
        "schema": 1,
        "purpose": op.PURPOSE,
        "prepared": prepared,
        "tool_id": "t18-output-operation-20261022-" + c.sha(c.canonical(tools))[:16],
        "tool_files": tools,
        "evidence_files": evidence,
        "system_preflight": preflight_name,
        "isolated_tests": tests,
        "protected_processes": processes,
        "drain_seconds": 600,
        "resume_timer": c.TIMER,
        "runtime_timer_holds": list(op.TIMERS),
        "additional_resume_headroom_bytes": 1024**3,
        "checkpoint_manifest_paths": sorted(
            str(p)
            for p in Path("/mnt/backup/marketdata-historical/automatic").glob("*/*/backup.json")
        ),
        "ready_for_separate_approval": True,
        "production_approval": False,
        "scope_change_requires_explicit_approval": True,
        "rollback_archiver_timer": "held",
        "policy_changes": [],
    }
    plan["operation_id"] = c.sha(c.canonical(plan))
    op.validate(plan)
    target = HERE / "operation-plan.json"
    if target.exists():
        op.require(
            json.loads(target.read_bytes()) == plan,
            "Existing operation is fixed; review a new plan explicitly",
        )
    else:
        target.write_bytes(c.encode(plan))
    print(
        json.dumps(
            {k: plan[k] for k in ("operation_id", "tool_id", "production_approval")}, indent=2
        )
    )


if __name__ == "__main__":
    main()
