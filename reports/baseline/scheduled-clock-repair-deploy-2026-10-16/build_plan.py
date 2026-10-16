"""Freeze the host executor and exact plan; never writes operating state."""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "vendor")]
import handoff as h

PREFLIGHT = HERE.with_name("scheduled-clock-handoff-2026-10-16")
RESULT = "system-preflight-result-48167a9e9eb24fdd.json"
CONTROLS = ("Type User Group WorkingDirectory Restart RestartUSec KillMode KillSignal TimeoutStartUSec "
            "TimeoutStopUSec RuntimeMaxUSec ProtectClock ProtectSystem ProtectHome PrivateTmp NoNewPrivileges "
            "UMask Nice IOSchedulingClass IOSchedulingPriority EnvironmentFiles").split()
EXTERNAL = (
    "/etc/marketdata/runtime.json", "/etc/chrony/marketdata.conf", "/etc/marketdata/rtc-utc-confirmation.json",
    "/etc/marketdata/scheduled-system/oi-live.toml", "/etc/marketdata/daily-scheduler.toml",
    "/etc/marketdata/daily-publication-probe.toml", "/etc/marketdata/checkpoint-clock-retirement/settings.toml",
    "/etc/marketdata/checkpoint-clock-retirement/policy.toml", "/etc/marketdata/checkpoint-clock-retirement/recurring.json",
    "/etc/marketdata/checkpoint-clock-retirement/manual.json", "/home/yoonhc/.config/marketdata/network-observer.env",
    "/home/yoonhc/.config/marketdata/network-observer.toml", "/home/yoonhc/.config/marketdata/restic-service.env",
    "/home/yoonhc/.config/marketdata/restic-archive.toml",
)


def build():
    seal = json.loads((PREFLIGHT / "tool-seal.json").read_bytes())
    for name, digest in seal["files"].items():
        h.c.require(h.c.sha((PREFLIGHT / name).read_bytes()) == digest, "Preflight input changed: " + name)
    raw = (PREFLIGHT / RESULT).read_bytes()
    result = json.loads(raw)
    h.c.require(result["passed"] and result["operating_unchanged"] and result["before"] == result["after"]
                and not result["cleanup"]["errors"] and result["cleanup"]["paths_absent"]
                and set(result["cleanup"]["unit_load_states"].values()) == {"not-found"}, "Preflight not complete")
    h.c.require(result["tool_id"] == seal["tool_id"] and result["plan_id"] == seal["plan_id"], "Preflight identity differs")
    (HERE / "preflight-result.json").write_bytes(raw)
    (HERE / "preflight-seal.json").write_bytes((PREFLIGHT / "tool-seal.json").read_bytes())
    inner = json.loads((HERE / "configuration-plan.json").read_bytes())
    fixed = json.loads((HERE / "candidate-inputs.json").read_bytes())
    recovery = json.loads((HERE / "recovery-plan.json").read_bytes())
    native = json.loads((HERE / "host-before.json").read_bytes())
    h.c.require(native["boot_id"] == inner["boot_id"] and native["ledger"] == inner["ledger_before"], "Native baseline differs")
    files = {str(p.relative_to(HERE)): h.c.sha(p.read_bytes()) for pattern in ("*.py", "vendor/*.py", "tests/*.py")
             for p in sorted(HERE.glob(pattern))}
    for name in ("candidate-inputs.json", "configuration-plan.json", "payload.json", "host-before.json",
                 "preflight-result.json", "preflight-seal.json", "preflight-reuse.json", "recovery-plan.json"):
        files[name] = h.c.sha((HERE / name).read_bytes())
    for path in sorted((HERE / "recovery-inputs").iterdir()):
        files[str(path.relative_to(HERE))] = h.c.sha(path.read_bytes())
    digest = h.c.sha(h.c.encode(files))
    tool_id = "scheduled-clock-handoff-20261016-" + digest[:16]
    tool = {"tool_id": tool_id, "source_sha256": digest, "files": files}
    plan = {
        "purpose": h.PURPOSE, "schema": 1, "tool_id": tool_id, "tool_sha256": digest,
        "release_id": fixed["release_id"], "source_sha256": fixed["source_sha256"],
        "payload_source": "per-object sources sealed in payload.json", "inner": inner, "boot_id": inner["boot_id"],
        "recovery_plan_id": recovery["plan_id"], "recover_before_configuration_change": True,
        "policy_changes": h.t.POLICY, "drain_seconds": 600, "request_root": h.REQUESTS,
        "preflight": {"path": str(PREFLIGHT / RESULT), "sha256": h.c.sha(raw), "tool_id": seal["tool_id"]},
        "external_files": {p: h.c.sha(Path(p).read_bytes()) for p in EXTERNAL},
        "protected_metadata": native["protected_metadata"],
        "effective_controls": {n: {k: native["effective"][n][k] for k in CONTROLS if k in native["effective"][n]}
                               for n in h.c.SERVICES},
        "temporary_masks": [h.c.SYSTEM_ROOT + n for n in h.c.SERVICES],
        "manager_mutations": {"system": [*h.c.SERVICES, *h.c.TIMERS, h.NEW_WAITER], "user": []},
        "preserved": ["all workload data and requests", "T05 baseline/completion/order", "boot correction evidence",
                      "collectors and Chrony invocation", "eight timers and sixteen user masks", "four retirement masks"],
    }
    plan["plan_id"] = h.c.sha(h.c.encode(plan))
    (HERE / "tool-seal.json").write_bytes(h.c.encode(tool))
    (HERE / "host-plan.json").write_bytes(h.c.encode(plan))
    (HERE / "approval.template.json").write_bytes(h.c.encode({"purpose": h.PURPOSE, "plan_id": plan["plan_id"],
        "enabled": False, "approval_reference": "", "directions": ["apply", "rollback"]}))
    print(json.dumps({"plan_id": plan["plan_id"], "tool_id": tool_id, "operating_approval": False}))


if __name__ == "__main__":
    build()
