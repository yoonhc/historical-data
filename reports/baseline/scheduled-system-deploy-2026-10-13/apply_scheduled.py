"""Provision the approved record and import check, then call the frozen handoff.

Without --run this validates inputs only. No transition logic is implemented here.
The frozen executor owns masks, drain, configuration changes and manager actions.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

HERE = Path(__file__).resolve().parent
HANDOFF = HERE.with_name("scheduled-system-handoff-2026-10-12")
PLAN_ID = "6a71f295ec760f442edf0f88b04c6cf9378d9383fb9c45c0dc7c8c140eac3f2b"
TOOL_ID = "scheduled-handoff-20261012-b48b6beb0b9b72b9"
PINNED = {
    "tool-seal.json": "97b09ff747a3b0eb58990655828f6ab12403c9af65e00ed33a2f03d56308e822",
    "host-plan-preflight-passed.json": "a1d7bd23d302cfa5e7a3f8f3bc723a73e83f89359ed4f37fe1984a0e5cde19d5",
}
CRITICAL = tuple("marketdata-" + name + ".service" for name in ("pack", "backup", "restic-archive"))

IMPORT_PROBE = """
import hashlib,json,os,sys
from pathlib import Path
import marketdata
import marketdata.cli
modules={name:Path(module.__file__).resolve() for name,module in
         [('marketdata',marketdata),('marketdata.cli',marketdata.cli)]}
print(json.dumps({'uid':os.getuid(),'executable':sys.executable,'prefix':sys.prefix,
 'modules':{name:{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            for name,p in modules.items()}}))
"""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def inputs():
    # Check the executable sources before importing them; their own loader also
    # checks the frozen candidate manifest and embedded configuration plan.
    for name, digest in PINNED.items():
        require(sha(HANDOFF / name) == digest, "Pinned input changed: " + name)
    seal = json.loads((HANDOFF / "tool-seal.json").read_bytes())
    for name, digest in seal["files"].items():
        require(sha(HANDOFF / name) == digest, "Frozen tool changed: " + name)
    base = HANDOFF.with_name("scheduled-system-migration-2026-10-12")
    for name, digest in seal["base_files"].items():
        require(sha(base / name) == digest, "Frozen candidate input changed: " + name)
    manifest = json.loads((base / "manifest.json").read_bytes())
    for name, digest in manifest["source_sha256"].items():
        require(sha(base / "candidate" / name) == digest, "Frozen source changed: " + name)
    sys.path.insert(0, str(HANDOFF))
    import handoff_common as c
    import handoff_inputs as engine
    from handoff import Host, STATE, validate_outer
    plan = engine.load_checked(HANDOFF / "host-plan-preflight-passed.json")
    require(plan["plan_id"] == PLAN_ID and plan["tool_id"] == TOOL_ID, "Wrong approved scope")
    require(c.boot() == plan["boot_id"], "Different boot: review recovery instead of applying")
    approval = json.loads((HERE / "approval.json").read_bytes())
    validate_outer(plan, approval)
    receipt = plan["preflight"]
    require(sha(receipt["report"]) == receipt["sha256"], "Preflight evidence changed")
    checked = json.loads(Path(receipt["report"]).read_bytes())
    require(checked["passed"] and checked["operating_unchanged"] and checked["tool_id"] == TOOL_ID,
            "Missing successful exact-tool preflight")
    return c, engine, Host, STATE, plan, approval


def check_before(plan, fs, host):
    host.immutable()
    host.baseline()
    require(fs.json("/var/lib/marketdata-clock/boot-state.json") == plan["inner"]["ledger_before"],
            "Before ledger differs")
    for path, op in {**plan["inner"]["operations"], **plan["enable_links"]}.items():
        require(fs.node(path) == op["before"], "Before configuration differs: " + path)


def require_quiet(states):
    busy = {name: value["classification"] for name, value in states.items()
            if value["classification"] != "idle"}
    require(not busy, "Preservation work is not idle; leave it running and review later: " + repr(busy))


def wait_before_install(host, managers, *, timeout=600, monotonic=time.monotonic, pause=time.sleep):
    """Only observe; retain old scheduling while waiting for a suitable window."""
    started = monotonic()
    deadline = started + timeout
    last_notice = None
    while True:
        states = managers.states("user", CRITICAL)
        busy = {name: value["classification"] for name, value in states.items()
                if value["classification"] != "idle"}
        ready = host.clock_ready()
        if ready and not busy:
            return {"strict_ready": True, "preservation_work_idle": True,
                    "wait_seconds": monotonic() - started}
        current = monotonic()
        require(current < deadline, "Pre-install readiness/idle wait expired; existing jobs remain unchanged")
        if last_notice is None or current - last_notice >= 30:
            print(json.dumps({"phase": "waiting_before_install", "strict_ready": ready,
                              "preservation_work": busy, "old_jobs_unchanged": True}), flush=True)
            last_notice = current
        pause(min(10, deadline - current))


def validate_import(value, root, entries):
    root = Path(root)
    require(value["uid"] == 1000, "Import did not run as UID 1000")
    require(value["executable"] == str(root / ".venv/bin/python"), "Interpreter entrypoint differs")
    require(value["prefix"] == str(root / ".venv"), "Virtual environment prefix differs")
    expected = {
        "marketdata": ".venv/lib/python3.12/site-packages/marketdata/__init__.py",
        "marketdata.cli": ".venv/lib/python3.12/site-packages/marketdata/cli.py",
    }
    for name, relative in expected.items():
        require(value["modules"][name] == {"path": str(root / relative), "sha256": entries[relative]["sha256"]},
                "Imported module is not the sealed installed package: " + name)


def identity(managers, names):
    result = {}
    for name in names:
        observed = managers.show("system", name)
        result[name] = {key: observed[key] for key in ("MainPID", "InvocationID", "ActiveState")}
    return result


@contextlib.contextmanager
def setup_lock(c, fs, directory):
    # Same per-plan handoff inode; release before the frozen executor acquires it.
    path = directory + "/handoff.lock"
    if fs.node(path)["kind"] == "absent":
        fs.write(path, {"kind": "file", "text": "", "sha256": c.sha(b""), "mode": 0o600})
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def run(c, engine, Host, state_root, plan, approval):
    require(os.geteuid() == 0, "Administrator authentication required")
    directory = state_root + "/" + PLAN_ID
    fs = c.SafeFiles("/", (), {}, control_prefix=directory)
    managers = c.Managers()  # This wrapper performs read-only manager queries.
    host = Host(plan, fs, managers)
    record = {"plan_id": PLAN_ID, "tool_id": TOOL_ID, "release_id": plan["inner"]["release_id"],
              "wrapper_sha256": sha(__file__), "approval_sha256": c.sha(c.encode(approval)),
              "started_at": utc(), "passed": False, "phase": "prechecks",
              "clock_control": False, "checkpoint_deletion": False, "automatic_retirement": "masked"}
    report = directory + "/deployment/attempt-" + uuid.uuid4().hex + ".json"
    try:
        # No new masks or stop/start requests if an earlier apply needs recovery.
        require(fs.node(directory + "/apply.json")["kind"] == "absent",
                "An apply already started; inspect its phase and use the frozen resume/rollback procedure")
        check_before(plan, fs, host)
        record["phase"] = "waiting_before_install"
        record["preparation"] = wait_before_install(host, managers)
        check_before(plan, fs, host)
        record["operating_before"] = identity(managers, c.IDENTITIES)
        with setup_lock(c, fs, directory):
            require(fs.node(directory + "/apply.json")["kind"] == "absent", "Apply started during setup")
            fs.save(directory + "/plan.json", plan, immutable=True)
            fs.save(directory + "/approval.json", approval, immutable=True)
            fs.save(directory + "/deployment/authorization.json", {
                "plan_id": PLAN_ID, "approval": approval,
                "wrapper_sha256": record["wrapper_sha256"], "pinned_inputs": PINNED,
            }, immutable=True)
            record["phase"] = "installing_fixed_release"
            fs.save(report, record)
            print(record["phase"], flush=True)
            root = Path("/opt/marketdata/releases") / plan["inner"]["release_id"]
            record["installation"] = engine.install_release(root)
            probe = subprocess.run(c.user_command([root / ".venv/bin/python", "-B", "-I", "-c", IMPORT_PROBE]),
                                   cwd=root, capture_output=True, text=True, timeout=30)
            require(probe.returncode == 0, "Final-path import failed: " + probe.stderr)
            imported = json.loads(probe.stdout)
            validate_import(imported, root, engine.environment_files())
            record["installed_import"] = imported
            fs.save(directory + "/deployment/installed-import.json", imported, immutable=True)
            require_quiet(managers.states("user", CRITICAL))
            check_before(plan, fs, host)
        record["phase"] = "approved_handoff"
        fs.save(report, record)
        print(record["phase"], flush=True)
        engine.live_execution(plan, SimpleNamespace(approval=Path(directory + "/approval.json"),
                                                   action="apply", drain_seconds=600))
        state = fs.json(directory + "/apply.json")
        require(state["phase"] == "complete", "Handoff has not completed")
        record["handoff_result"] = state["result"]
        record["operating_after"] = identity(managers, c.IDENTITIES)
        record["protected_processes_unchanged"] = record["operating_before"] == record["operating_after"]
        require(record["protected_processes_unchanged"], "Chrony/collector identity changed: observation required")
        record.update(passed=True, phase="configuration_applied_operating_observation_pending")
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        record["finished_at"] = utc()
        fs.save(report, record)
        print(json.dumps({**record, "report": report}, indent=2, ensure_ascii=False), flush=True)
    return 0 if record["passed"] else 75


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    values = inputs()
    if args.run:
        raise SystemExit(run(*values))
    print(json.dumps({"plan_id": PLAN_ID, "tool_id": TOOL_ID, "input_validation": "passed",
                      "user_approved": True, "operating_apply": False, "root_authentication_required": True}))
