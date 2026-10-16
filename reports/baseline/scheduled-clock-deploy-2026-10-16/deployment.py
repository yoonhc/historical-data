"""Native boundary: exact root approval, sealed installation, resumable handoff."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "vendor")]
import handoff as h
from installation import install


def load_checked():
    tool = json.loads((HERE / "tool-seal.json").read_bytes())
    plan = json.loads((HERE / "host-plan.json").read_bytes())
    h.c.require(tool["source_sha256"] == h.c.sha(h.c.encode(tool["files"])) == plan["tool_sha256"]
                and tool["tool_id"] == plan["tool_id"], "Tool identity differs")
    h.c.require(re.fullmatch(r"scheduled-clock-handoff-20261016-[0-9a-f]{16}", plan["tool_id"])
                and re.fullmatch(r"scheduled-clock-policy-20261016-[0-9a-f]{16}", plan["release_id"]), "Unsafe release name")
    for name, digest in tool["files"].items():
        h.c.require(not Path(name).is_absolute() and ".." not in Path(name).parts, "Unsafe tool input")
        h.c.require(h.c.sha((HERE / name).read_bytes()) == digest, "Tool input changed: " + name)
    h.validate(plan, authority(plan))
    h.t.validate(plan["inner"], {"purpose": h.t.PURPOSE, "plan_id": plan["inner"]["plan_id"], "enabled": True,
        "approval_reference": "inspection only", "directions": ["apply", "rollback"]})
    raw = (HERE / "preflight-result.json").read_bytes()
    evidence = json.loads(raw)
    h.c.require(h.c.sha(raw) == plan["preflight"]["sha256"] and evidence["passed"]
                and evidence["before"] == evidence["after"] and evidence["operating_unchanged"]
                and evidence["release_id"] == plan["release_id"]
                and evidence["plan_id"] == plan["inner"]["plan_id"]
                and evidence["tool_id"] == plan["preflight"]["tool_id"], "Preflight evidence differs")
    h.c.require(plan["temporary_masks"] == [h.c.SYSTEM_ROOT + n for n in h.c.SERVICES]
                and plan["manager_mutations"] == {"system": [*h.c.SERVICES, *h.c.TIMERS, h.NEW_WAITER], "user": []},
                "Host operation scope differs")
    return plan


def authority(plan):
    # The production caller separately requires root and --approve-plan EXACT_ID.
    # Using this value for input inspection never persists or grants authority.
    return {"purpose": h.PURPOSE, "plan_id": plan["plan_id"], "enabled": True,
            "approval_reference": "explicit-root-command:" + plan["plan_id"], "directions": ["apply", "rollback"]}


def approval_check(plan, args, uid=None):
    h.c.require((os.geteuid() if uid is None else uid) == 0, "Administrator execution required")
    h.c.require(args.approve_plan == plan["plan_id"], "Explicit exact-plan approval is required")
    h.c.require(h.c.boot() == plan["boot_id"], "Boot changed; do not apply this plan")
    review = None
    if args.request_review or args.approve_review_hash:
        h.c.require(args.action == "rollback" and args.request_review and args.approve_review_hash,
                    "Exact rollback review path and approved hash required")
        raw = args.request_review.read_bytes()
        h.c.require(h.c.sha(raw) == args.approve_review_hash, "Rollback review hash differs")
        review = json.loads(raw)
        h.c.require(review.get("plan_id") == plan["plan_id"]
                    and review.get("action") == "preserve-for-reviewed-rollback" and review.get("reason"), "Invalid rollback review")
    return review


def tool_install(plan):
    tool = json.loads((HERE / "tool-seal.json").read_bytes())
    names = [*tool["files"], "tool-seal.json", "host-plan.json"]
    entries = {n: {"kind": "file", "mode": 0o644, "sha256": h.c.sha((HERE / n).read_bytes()), "source": str(HERE / n)}
               for n in names}
    target = Path("/opt/marketdata/tools") / plan["tool_id"]
    install(target, entries, identity=plan["plan_id"])
    return target


@contextmanager
def operation_lock():
    # Separate from coordinator: held across normal drain, never blocks T05's SH.
    path = Path("/var/lib/marketdata-clock/scheduled-policy-handoff.lock")
    for parent in path.parents:
        m = h.c.metadata(parent)
        h.c.require(m["kind"] == "directory" and m["uid"] == 0 and not m["mode"] & 0o022, "Untrusted lock parent")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        m = h.c.metadata(path)
        h.c.require(m == {"kind": "file", "uid": 0, "gid": 0, "mode": 0o600}, "Unsafe handoff lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def release_install(plan):
    payload = json.loads((HERE / "payload.json").read_bytes())
    entries = {name.removeprefix("candidate/"): {**spec, "source": str(Path(plan["payload_source"]) / name)}
               for name, spec in payload.items() if name.startswith("candidate/")}
    target = Path("/opt/marketdata/releases") / plan["release_id"]
    result = install(target, entries, identity=plan["source_sha256"])
    imports = {}
    for variant in ("base", "daily", "preserve"):
        python = target / variant / ".venv/bin/python"
        script = ("import hashlib,json,os,sys,marketdata,marketdata.cli,marketdata.job_clock; "
                  "from pathlib import Path; "
                  "print(json.dumps({'uid':os.getuid(),'executable':sys.executable,'prefix':sys.prefix,"
                  "'modules':{m.__name__:{'path':str(Path(m.__file__).resolve()),'sha256':hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()} "
                  "for m in (marketdata,marketdata.cli,marketdata.job_clock)}}))")
        r = subprocess.run(h.c.user_command([str(python), "-B", "-I", "-c", script]), capture_output=True, text=True, timeout=30)
        h.c.require(r.returncode == 0, "Final-path import failed: " + variant + ":" + r.stderr)
        value = json.loads(r.stdout)
        h.c.require(value["uid"] == 1000 and value["executable"] == str(python)
                    and value["prefix"] == str(target / variant / ".venv"), "Final interpreter differs")
        for info in value["modules"].values():
            relative = str(Path(info["path"]).relative_to(target))
            h.c.require(info["sha256"] == entries[relative]["sha256"], "Final import is not sealed")
        imports[variant] = value
    return {**result, "imports": imports}


def observe(plan, direction):
    if direction == "apply":
        root = Path("/opt/marketdata/releases") / plan["release_id"]
        command = [str(root / "base/.venv/bin/python"), "-B", "-I", str(HERE / "readiness.py"), str(root), h.t.BINDING]
    else:
        profile = json.loads(plan["inner"]["operations"][h.t.PROFILE]["before"]["text"])
        command = shlex.split(profile["guard_command"])
    r = subprocess.run(h.c.user_command(command), capture_output=True, text=True, timeout=30)
    h.c.require(r.returncode in (0, 75), "Post-commit read-only check failed: " + r.stderr + r.stdout)
    return {"code": r.returncode, "ready": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr,
            "direction": direction, "reopens_boot_step": False}


def execute(plan, args):
    review = approval_check(plan, args)
    installed_tool = Path("/opt/marketdata/tools") / plan["tool_id"]
    if HERE != installed_tool:
        # Re-exec only the root-owned sealed copy before holding production timers.
        target = tool_install(plan)
        command = ["/usr/bin/python3", "-B", "-I", str(target / "handoff.py"), "--action", args.action,
                   "--approve-plan", args.approve_plan]
        if review is not None:
            command += ["--request-review", str(args.request_review.resolve()), "--approve-review-hash", args.approve_review_hash]
        raise SystemExit(subprocess.run(command).returncode)
    # An installed tool is itself rechecked, including root ownership and mode.
    tool_install(plan)
    inner_dir = "/var/lib/marketdata-clock/scheduled-transitions/" + plan["inner"]["plan_id"]
    allowed = [*plan["inner"]["operations"], h.t.LEDGER, *[inner_dir + "/" + n for n in
               ("plan.json", "approval.json", "progress.json", "apply-receipt.json", "rollback-receipt.json")]]
    directory = h.STATE + "/" + plan["plan_id"]
    fs = h.c.SafeFiles("/", allowed, {}, control_prefix=directory)
    attempt = "attempt-" + uuid.uuid4().hex
    events = []

    def record(event):
        events.append({**event, "utc": datetime.now(timezone.utc).isoformat()})
        fs.save(directory + "/" + attempt + "-commands.json", events)

    m = h.c.Managers(allowed=[("system", n) for n in plan["manager_mutations"]["system"]], record=record)
    host = h.Host(plan, fs, m)
    result = {"plan_id": plan["plan_id"], "tool_id": plan["tool_id"], "release_id": plan["release_id"],
              "direction": args.action, "started_at": datetime.now(timezone.utc).isoformat(), "passed": False,
              "clock_control": False, "checkpoint_deletion": False, "automatic_retirement": "masked"}
    with operation_lock():
        try:
            host.immutable()
            result["protected_processes_before"] = {n: {k: v for k, v in m.show("system", n).items()
                                                       if k in ("MainPID", "InvocationID", "ActiveState")} for n in h.c.IDENTITIES}
            executor = h.Handoff(plan, authority(plan), fs, m, host, installer=lambda: release_install(plan),
                                 observation=lambda d: observe(plan, d), request_review=review)
            state = executor.run(args.action)
            result.update(passed=True, phase="configuration_committed_operating_observation_pending", result=state["result"])
        except Exception as exc:
            result.update(phase="held_for_review", error=type(exc).__name__ + ": " + str(exc))
        finally:
            try:
                result["protected_processes_after"] = {n: {k: v for k, v in m.show("system", n).items()
                                                          if k in ("MainPID", "InvocationID", "ActiveState")} for n in h.c.IDENTITIES}
                result["protected_processes_unchanged"] = result.get("protected_processes_before") == result["protected_processes_after"]
            except Exception as exc:
                result["observation_error"] = str(exc)
                result["passed"] = False
            result["finished_at"] = datetime.now(timezone.utc).isoformat()
            fs.save(directory + "/" + attempt + ".json", result)
        print(json.dumps({**result, "report": directory + "/" + attempt + ".json"}, indent=2))
    if not result["passed"]:
        raise SystemExit(75)
