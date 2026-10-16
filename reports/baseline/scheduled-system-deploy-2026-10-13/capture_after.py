"""Read-only operating verification; writes evidence only in this report folder."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
HANDOFF = HERE.with_name("scheduled-system-handoff-2026-10-12")
sys.path.insert(0, str(HANDOFF))
import handoff_common as c
from handoff import Host, STATE, reader, pinned
from handoff_inputs import load_checked, environment_files

plan = load_checked(HANDOFF / "host-plan-preflight-passed.json")
managers = c.Managers()
fs = c.SafeFiles("/", (), {})
host = Host(plan, fs, managers)
directory = Path(STATE) / plan["plan_id"]
transition = Path("/var/lib/marketdata-clock/scheduled-transitions") / plan["inner"]["plan_id"]
result = {"observed_at": datetime.now(timezone.utc).isoformat(), "plan_id": plan["plan_id"],
          "boot_id": c.boot(), "operating_mutations": False, "checks": {}, "errors": []}


def check(name, function):
    try:
        value = function()
        if value is False:
            raise ValueError("Comparison differs")
        result["checks"][name] = True if value is None else value
    except Exception as exc:
        result["checks"][name] = False
        result["errors"].append({"check": name, "error": f"{type(exc).__name__}: {exc}"})


check("protected_configuration", host.immutable)
result["root_evidence"] = {}
mirror = HERE / "root-evidence"
mirror.mkdir(exist_ok=True)
names = [directory / n for n in ("apply.json", "commands.json", "approval.json", "installed.json")]
names += sorted((directory / "deployment").glob("*.json"))
names += [transition / n for n in ("progress.json", "apply-receipt.json")]
for source in names:
    raw = source.read_bytes()
    label = ("transition-" if source.parent == transition else "") + source.name
    target = mirror / label
    if target.exists():
        c.require(target.read_bytes() == raw, "Existing evidence copy differs: " + label)
    else:
        target.write_bytes(raw)
    result["root_evidence"][str(source)] = {"copy": str(target.relative_to(HERE)), "sha256": c.sha(raw)}

applied = json.loads((directory / "apply.json").read_bytes())
receipt = json.loads((transition / "apply-receipt.json").read_bytes())
result["handoff"] = {"phase": applied["phase"], "cancelled": list(applied["cancelled"]),
                     "start_requests": {k: v["accepted"] for k, v in applied["start_requests"].items()}}
check("handoff_complete", lambda: applied["phase"] == "complete")
check("ledger_matches_receipt", lambda: fs.json("/var/lib/marketdata-clock/boot-state.json") == receipt["ledger_after"])
preserved = lambda value: {k: v for k, v in value.items() if k not in ("profile_sha256", "configuration_history")}
check("boot_closed_correction_preserved", lambda: preserved(receipt["ledger_after"]) == preserved(plan["inner"]["ledger_before"]))
check("configuration_history_extended", lambda: receipt["ledger_after"]["configuration_history"][:-1] == plan["inner"]["ledger_before"].get("configuration_history", []))
check("configuration_files", lambda: all(fs.node(p) == op["after"] for p, op in plan["inner"]["operations"].items()))
check("configuration_ownership", lambda: all(c.metadata(p) == op["after"] for p, op in plan["ownership"].items()))
check("timer_enable_links", lambda: all(fs.node(p) == op["after"] for p, op in plan["enable_links"].items()))
root = Path("/opt/marketdata/releases") / plan["inner"]["release_id"]
files = environment_files()


def installation():
    for name, spec in files.items():
        path = root / name
        meta = c.metadata(path)
        c.require(meta["uid"] == 0, "Not root owned: " + name)
        if "link" in spec:
            c.require(path.is_symlink() and os.readlink(path) == spec["link"], "Link differs: " + name)
        else:
            c.require(meta["mode"] == spec["mode"] and c.sha(path.read_bytes()) == spec["sha256"], "File differs: " + name)
    return {"files": len(files), "root_owned": True, "hashes_match": True}


check("installed_release", installation)
for manager in ("user", "system"):
    result[manager] = managers.states(manager, (*c.SERVICES, *c.TIMERS, c.WAITER))
check("old_services_timers_masked_idle", lambda: all(
    result["user"][n]["properties"]["LoadState"] == "masked" and result["user"][n]["classification"] == "idle"
    for n in (*c.SERVICES, *c.TIMERS)))
check("system_timers_enabled_active", lambda: all(
    result["system"][n]["properties"]["UnitFileState"] == "enabled" and
    result["system"][n]["properties"]["ActiveState"] == "active" for n in c.TIMERS))
for name, spec in plan["inner"]["topology"]["units"].items():
    properties = result["system"].get(name, {}).get("properties") or managers.show("system", name)
    check("effective:" + name, lambda p=properties, s=spec: reader.verify(p, s, quiescent=False))
check("work_uid_and_clock_protection", lambda: all(
    result["system"][n]["properties"]["User"] == "yoonhc" and
    result["system"][n]["properties"]["ProtectClock"] == "yes" for n in c.SERVICES))

attempt = json.loads((directory / "deployment/attempt-0d473fad0e2245b59b7cf8b1b3956359.json").read_bytes())
result["identities"] = {name: {k: (p := managers.show("system", name))[k] for k in ("MainPID", "InvocationID", "ActiveState")} for name in c.IDENTITIES}
check("chrony_collectors_same_invocation", lambda: result["identities"] == attempt["operating_before"])
result["service_results"] = {}
for name in c.SERVICES:
    r = managers.command("system", "show", name, "--no-pager", "--property=Result,ExecMainCode,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp,SuccessExitStatus,InvocationID,ControlGroup")
    result["service_results"][name] = c.parse(r.stdout)
gate = pinned.load_gate()
result["clock_now"] = gate.rt.probe(gate.rt.load_profile(Path("/etc/marketdata/runtime.json")))
result["configuration_verified"] = not result["errors"]
out = HERE / ("operating-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
out.write_bytes(c.encode(result))
print(json.dumps({"report": str(out), "configuration_verified": result["configuration_verified"],
                  "errors": result["errors"], "handoff": result["handoff"],
                  "service_results": result["service_results"], "clock_ready": result["clock_now"]["ready"]}, indent=2))
