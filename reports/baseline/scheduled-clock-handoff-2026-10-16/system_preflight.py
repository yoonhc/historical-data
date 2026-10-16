"""Temporary system units only. No operating configuration, service or clock writes.

Run after review with sudo; default is input inspection. Existing operating units
are queried, never started/stopped. All mutating manager calls are nonce-restricted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))
import handoff_common as c
import transition as t
from trial_worker import VARIANTS

CURRENT_BINDING = "/etc/marketdata/checkpoint-clock-retirement/clock-binding.json"
PROTECTED_PROCESSES = ("chrony.service", "marketdata-collector@depth.service", "marketdata-collector@aggtrade.service")


def command(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, timeout=45, **kw)


def write(path, raw, mode=0o644, uid=0):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fchmod(f.fileno(), mode)
        os.fchown(f.fileno(), uid, uid)
        os.fsync(f.fileno())


def checked():
    seal = json.loads((HERE / "tool-seal.json").read_bytes())
    for name, digest in seal["files"].items():
        c.require(c.sha((HERE / name).read_bytes()) == digest, "Tool input changed: " + name)
    payload = json.loads((HERE / "payload.json").read_bytes())
    source = Path(seal["payload_source"])
    for rel, node in payload.items():
        p = source / rel
        c.require(not Path(rel).is_absolute() and ".." not in Path(rel).parts, "Unsafe payload path")
        if node["kind"] == "file":
            c.require(p.is_file() and not p.is_symlink() and c.sha(p.read_bytes()) == node["sha256"], "Payload differs: " + rel)
        else:
            c.require(p.is_symlink() and os.readlink(p) == node["target"], "Payload link differs: " + rel)
    plan = json.loads((HERE / "configuration-plan.json").read_bytes())
    t.validate(plan, {"enabled": True, "purpose": t.PURPOSE, "plan_id": plan["plan_id"],
                      "approval_reference": "preflight is not deployment approval", "directions": ["apply", "rollback"]})
    c.require(c.boot() == plan["boot_id"], "Boot changed; refresh the review before testing")
    templates = {}
    manifest = json.loads((HERE / "candidate-inputs.json").read_bytes())
    for p, digest in manifest["after"].items():
        if p.startswith(t.SYSTEM):
            raw = (source / "rendered/units" / Path(p).name).read_bytes()
            c.require(c.sha(raw) == digest, "Rendered unit changed")
            templates[Path(p).name] = raw.decode()
    return seal, payload, plan, templates


def snapshot(plan, managers):
    nodes = {}
    for path, op in plan["operations"].items():
        p = Path(path)
        if p.is_symlink():
            value = {"kind": "symlink", "target": os.readlink(p)}
        elif p.is_file():
            value = {"kind": "file", "sha256": c.sha(p.read_bytes()), "mode": p.stat().st_mode & 0o777}
        else:
            value = {"kind": "absent"}
        before = {k: v for k, v in op["before"].items() if k != "text"}
        c.require(value == before, "Operating configuration differs: " + path)
        nodes[path] = {**value, "ownership": c.metadata(p)}
    for path, expected in plan["protected"].items():
        p = Path(path)
        if expected["kind"] == "digest":
            c.require(c.sha(p.read_bytes()) == expected["sha256"], "Protected input differs: " + path)
        else:
            c.require(p.is_symlink() and os.readlink(p) == expected["target"], "Protected link differs: " + path)
        nodes[path] = {**expected, "ownership": c.metadata(p)}
    for key, expected in plan["retirement_masks"].items():
        owner, name = key.split(":")
        path = Path(c.SYSTEM_ROOT if owner == "system" else c.USER_ROOT) / name
        c.require(path.is_symlink() and os.readlink(path) == "/dev/null", "Retirement mask differs")
        nodes[str(path)] = {**expected, "ownership": c.metadata(path)}
    ledger = json.loads(Path(t.LEDGER).read_bytes())
    c.require(ledger == plan["ledger_before"], "Operating ledger changed")
    identities = {}
    for name in PROTECTED_PROCESSES:
        result = managers.command("system", "show", name,
                                 "--property=MainPID,InvocationID,ActiveState").stdout
        identities[name] = c.parse(result)
    return {"boot_id": c.boot(), "configuration": nodes, "ledger": ledger, "processes": identities}


def install_trial(trial, payload, source):
    c.require(trial.parent == Path("/opt") and re.fullmatch(r"md-t16-[0-9a-f]{16}", trial.name), "Unsafe trial path")
    trial.mkdir(mode=0o755)
    for rel, node in sorted(payload.items()):
        p = trial / rel
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if node["kind"] == "symlink":
            os.symlink(node["target"], p)
        else:
            raw = (source / rel).read_bytes()
            c.require(c.sha(raw) == node["sha256"], "Input changed during install")
            write(p, raw, node["mode"])
    write(trial / "trial_worker.py", (HERE / "trial_worker.py").read_bytes())
    # No copied group-write cache permissions; no editable install/resolver calls.
    for p in trial.rglob("*"):
        if p.is_symlink():
            c.require(p.lstat().st_uid == 0, "Link ownership")
        else:
            c.require(p.stat().st_uid == 0 and not p.stat().st_mode & 0o022, "Unsealed trial object: " + str(p))


def trial_unit(template, trial, state, name, waiter, command_line):
    # Keep final execution protection, user, environment, kill/timeout options.
    text = template.replace("marketdata-jobs-clock-ready.service", waiter)
    text += ("\n[Service]\nExecStart=\nExecStart=" + command_line + "\nWorkingDirectory=" + str(state)
             + "\nRestart=no\nReadOnlyPaths=/home/yoonhc/projects/algoTrade/historical_data /etc/marketdata /var/lib/marketdata-clock /var/lib/marketdata-checkpoint-retirement\n"
             + "PrivateNetwork=yes\n")
    for line in text.splitlines():
        if line.startswith(("Requires=", "Wants=", "After=", "Before=")):
            c.require(not any(v.startswith("marketdata-") for v in line.split("=", 1)[1].split()),
                      "Trial dependency refers to an operating unit")
    return text


def save_report(path, value):
    # Drop root for writing into the user's report directory.
    code = "import os,sys; p=sys.argv[1]; data=sys.stdin.buffer.read(); fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644); f=os.fdopen(fd,'wb'); f.write(data); f.flush(); os.fsync(f.fileno()); f.close()"
    r = subprocess.run(["/usr/sbin/runuser", "-u", "yoonhc", "--", "/usr/bin/python3", "-I", "-c", code, str(path)],
                       input=c.encode(value), capture_output=True, timeout=30)
    c.require(r.returncode == 0, "Cannot save preflight report: " + r.stderr.decode())


def perform():
    c.require(os.geteuid() == 0, "sudo is required for temporary system units")
    seal, payload, plan, templates = checked()
    nonce = uuid.uuid4().hex[:16]
    prefix = "md-t16-" + nonce
    trial, state = Path("/opt") / prefix, Path("/var/lib") / prefix
    created = []
    manager = c.Managers()
    result = {"passed": False, "tool_id": seal["tool_id"], "release_id": seal["release_id"],
              "plan_id": plan["plan_id"], "started_at": datetime.now(timezone.utc).isoformat(),
              "tests": {}, "cleanup": {"errors": []}, "operating_applied": False}
    before = snapshot(plan, manager)
    result["before"] = before
    c.require(not trial.exists() and not trial.is_symlink() and not state.exists() and not state.is_symlink(), "Trial collision")

    def unit(name, text):
        c.require(name.startswith(prefix + "-"), "Nontrial unit")
        path = Path("/run/systemd/system") / name
        c.require(not path.exists() and not path.is_symlink(), "Unit exists")
        write(path, text.encode())
        created.append((name, path))
        manager.allowed.add(("system", name))

    def wait_for(name, seconds=50):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            props = manager.show("system", name)
            props.update(c.parse(manager.command("system", "show", name, "--property=ExecMainStatus,Result").stdout))
            if c.classify(props, manager.jobs("system")) == "idle":
                return props
            time.sleep(0.2)
        raise RuntimeError("Trial did not finish: " + name)

    def child_state(name):
        p = state / name
        p.mkdir(mode=0o700)
        os.chown(p, 1000, 1000)
        return p

    def wait_output(path):
        until = time.monotonic() + 50
        while not path.exists():
            c.require(time.monotonic() < until, "Missing trial output: " + str(path))
            time.sleep(0.2)
        return json.loads(path.read_bytes())

    try:
        print("installing sealed three-variant trial", flush=True)
        install_trial(trial, payload, Path(seal["payload_source"]))
        state.mkdir(mode=0o755)
        waiter = prefix + "-ready.service"
        cmd = f"{trial}/candidate/base/.venv/bin/python -B -I {trial}/candidate/policy/run_job.py --job oi --binding {CURRENT_BINDING} --wait"
        wait_text = "\n".join(line for line in templates[Path(t.WAITER).name].splitlines()
                              if not line.startswith("ExecStart="))
        unit(waiter, wait_text + "\n[Service]\nExecStart=" + cmd + "\nTimeoutStartSec=45s\nRestart=no\n")
        manager.command("system", "daemon-reload")
        print("checking actual read-only waiter and eight protected entrypoints", flush=True)
        for job in t.JOBS:
            directory = child_state(job)
            name = prefix + "-" + job + ".service"
            variant = VARIANTS.get(job, "base")
            cmd = f"{trial}/candidate/{variant}/.venv/bin/python -B -I {trial}/trial_worker.py --trial {trial} --state {directory} --job {job}"
            unit(name, trial_unit(templates["marketdata-" + job + ".service"], trial, directory, name, waiter, cmd))
            manager.command("system", "daemon-reload")
            manager.command("system", "start", "--no-block", name)
            props = wait_for(name)
            output = directory / "result.json"
            c.require(output.is_file(), "Worker did not save final output: " + job)
            worker = json.loads(output.read_bytes())
            result["tests"][job] = {"effective": props, "worker": worker,
                                    "junit": (directory / "tests.xml").read_text()}
            c.require(worker["passed"] and props.get("ExecMainStatus") == "0" and props["User"] == "yoonhc"
                      and props["ProtectClock"] == "yes", "Protected job test failed: " + job)
            c.require("marketdata-clock-ready.service" not in props.get("Requires", "").split()
                      and not props.get("ExecStartPre") and waiter in props.get("Requires", "").split(),
                      "Strict waiter leaked into the trial")
        print("checking failed finalization across separate system invocations", flush=True)
        directory = child_state("invocations")
        name = prefix + "-resume.service"
        cmd = f"{trial}/candidate/base/.venv/bin/python -B -I {trial}/trial_worker.py --trial {trial} --state {directory} --job oi-quality --mode process"
        unit(name, trial_unit(templates["marketdata-oi-quality.service"], trial, directory, name, waiter, cmd))
        manager.command("system", "daemon-reload")
        manager.command("system", "start", "--no-block", name)
        failed = wait_for(name)
        c.require(failed.get("ExecMainStatus") == "70", "Injected close failure did not fail the service")
        first = json.loads((directory / "failed.json").read_bytes())
        manager.command("system", "reset-failed", name)
        manager.command("system", "start", "--no-block", name)
        resumed = wait_for(name)
        second = json.loads((directory / "resumed.json").read_bytes())
        c.require(resumed.get("ExecMainStatus") == "0" and first["invocation"] != second["invocation"], "New invocation did not recover")
        result["tests"]["two_invocations"] = {"failed": first, "resumed": second, "effective": resumed}
        print("checking actual timer attribution through the new restic wrapper", flush=True)
        directory = child_state("provenance")
        name, timer = prefix + "-context.service", prefix + "-context.timer"
        cmd = f"{trial}/candidate/preserve/.venv/bin/python -B -I {trial}/trial_worker.py --trial {trial} --state {directory} --job restic-archive --mode provenance --service {name} --timer {timer}"
        unit(name, trial_unit(templates["marketdata-restic-archive.service"], trial, directory, name, waiter, cmd))
        unit(timer, f"[Timer]\nOnActiveSec=1s\nAccuracySec=1ms\nUnit={name}\n")
        manager.command("system", "daemon-reload")
        manager.command("system", "start", "--no-block", timer)
        timed = wait_output(directory / "context-0.json")
        wait_for(name)
        c.require(timed["actual"]["scheduled"] and timed["actual"]["manager"] == "system"
                  and timed["actual"]["manager_adapter"]["purpose"] == "scheduled-policy-system-provenance-v1"
                  and not timed["wrong_unit"]["scheduled"], "Timer identity not proven")
        manager.command("system", "stop", timer)
        manager.command("system", "start", "--no-block", name)
        manual = wait_output(directory / "context-1.json")
        wait_for(name)
        c.require(not manual["actual"]["scheduled"], "Manual service start mislabelled as timer")
        result["tests"]["restic_provenance"] = {"timer": timed, "manual": manual}
        result["waiter"] = manager.show("system", waiter)
        result["passed"] = True
    except BaseException as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        result["diagnostics"] = {}
        for name, path in created:
            try:
                result["diagnostics"][name] = {"show": manager.show("system", name),
                    "journal": command(["/usr/bin/journalctl", "-u", name, "-n", "40", "--no-pager", "-o", "cat"]).stdout}
            except Exception as exc:
                result["diagnostics"][name] = {"error": str(exc)}
        for name, path in reversed(created):
            try:
                manager.command("system", "stop", name, check=False)
                props = manager.show("system", name)
                c.require(props.get("MainPID", "0") == "0" and props.get("ControlPID", "0") == "0", "Trial PID remains")
                manager.command("system", "reset-failed", name, check=False)
                path.unlink()
            except Exception as exc:
                result["cleanup"]["errors"].append(name + ": " + str(exc))
        manager.command("system", "daemon-reload")
        result["cleanup"]["unit_load_states"] = {name: manager.show("system", name).get("LoadState") for name, _ in created}
        if any(v != "not-found" for v in result["cleanup"]["unit_load_states"].values()):
            result["cleanup"]["errors"].append("Trial unit still loaded")
        if not result["cleanup"]["errors"]:
            for path, parent in ((trial, Path("/opt")), (state, Path("/var/lib"))):
                if path.exists():
                    c.require(path.parent == parent and path.name == prefix and not path.is_symlink(), "Unsafe cleanup")
                    shutil.rmtree(path)
        result["cleanup"]["paths_absent"] = not trial.exists() and not state.exists()
        try:
            result["after"] = snapshot(plan, manager)
            result["operating_unchanged"] = result["after"] == before
        except Exception as exc:
            result["operating_unchanged"] = False
            result["after_error"] = str(exc)
        result["passed"] = (result["passed"] and result["operating_unchanged"]
                            and not result["cleanup"]["errors"] and result["cleanup"]["paths_absent"])
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        output = HERE / ("system-preflight-result-" + nonce + ".json")
        save_report(output, result)
        print(json.dumps({"passed": result["passed"], "report": str(output),
                          "operating_unchanged": result["operating_unchanged"], "cleanup": result["cleanup"]}, indent=2))
    return result["passed"]


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", action="store_true")
    args = p.parse_args()
    if args.run:
        raise SystemExit(0 if perform() else 1)
    seal, payload, plan, _ = checked()
    print(json.dumps({"checked": True, "tool_id": seal["tool_id"], "plan_id": plan["plan_id"],
                      "temporary_units_only": True, "operating_approval": False}))
