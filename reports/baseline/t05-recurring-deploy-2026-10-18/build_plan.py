"""Read live before-state and write only an unapproved plan in this workspace."""

# ruff: noqa: E402 -- isolated Python explicitly imports the reviewed local tools.
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine
import host
from vendor import handoff_common as c

CANDIDATE = HERE.parent / "t05-recurring-candidate-2026-10-18"
TOOLS = (
    "handoff.py",
    "engine.py",
    "host.py",
    "installation.py",
    "inspect_installed.py",
    "vendor/handoff_common.py",
)


def main():
    c.require(os.getuid() == 1000, "Plan capture is performed as the ordinary account")
    manifest = json.loads((CANDIDATE / "manifest.json").read_bytes())
    proposal = json.loads((CANDIDATE / "projected-recurring.disabled.json").read_bytes())
    c.require(
        manifest["release_id"] == "t05-recurring-retirement-20261018-e57a7995f03fb301",
        "Candidate identity differs",
    )
    report = CANDIDATE / "system-preflight-result-fe42ff4321294b9a.json"
    c.require(
        c.sha(report.read_bytes())
        == "bbabde69b834da8661703057c3add83b7820c80f940cc1257afd9998f3c58e95",
        "Reviewed root evidence differs",
    )
    review = json.loads(report.read_bytes())
    c.require(
        review["passed"]
        and review["operating_unchanged"]
        and review["cleanup"]["paths_absent"]
        and not review["cleanup"]["errors"],
        "Root preflight not complete",
    )
    tool_files = {n: c.sha((HERE / n).read_bytes()) for n in TOOLS}
    tool_id = "t05-recurring-operation-20261018-" + c.sha(c.encode(tool_files))[:16]
    reader = c.SafeFiles("/", (), {})
    manager = c.Managers()
    units = [engine.SERVICE, engine.AUTO + "-preview.service", engine.AUTO + "-apply@.service"]
    changes = {}
    order = []
    for unit in units:
        path = host.CONFIG + "units/" + unit
        changes[path] = {
            "before": reader.node(path),
            "after": engine.file_node((CANDIDATE / "deployment" / unit).read_bytes()),
        }
        order.append(path)
    actual = engine.SYSTEM + engine.AUTO + "-apply@.service"
    changes[actual] = {"before": reader.node(actual), "after": changes[order[-1]]["after"]}
    order.append(actual)
    for unit in (engine.SERVICE, engine.TIMER):
        path = engine.SYSTEM + unit
        c.require(reader.node(path) == engine.MASK, "Initial system mask differs")
        changes[path] = {
            "before": engine.MASK,
            "after": engine.file_node((CANDIDATE / "deployment" / unit).read_bytes()),
        }
        order.append(path)
    before_grant = reader.node(engine.GRANT)
    old = json.loads(before_grant["text"])
    c.require(
        old["enabled"] is False
        and old["approved_at"] is None
        and old["approval_reference"] is None
        and old["scope_id"] == "84a0b92ae6f8bf0e38ac10ecb4a2615a32af72aebf1c8c28d591c02087cf4983",
        "Existing recurring scope differs",
    )
    c.require(
        proposal["scope_id"] == "311ab16f019b4473185c487b219430c1fab4e90c4257f813bacfa696b1ba29a5",
        "Projected scope differs",
    )
    changes[engine.GRANT] = {
        "before": before_grant,
        "after": engine.file_node((CANDIDATE / "projected-recurring.disabled.json").read_bytes()),
    }
    link = engine.SYSTEM + "timers.target.wants/" + engine.TIMER
    c.require(reader.node(link) == engine.ABSENT, "Unexpected enable link")
    changes[link] = {
        "before": engine.ABSENT,
        "after": {"kind": "symlink", "target": engine.SYSTEM + engine.TIMER},
    }
    entries = {
        n: {"kind": "file", "source": str(CANDIDATE / "work" / n), "sha256": d, "mode": 0o644}
        for n, d in manifest["source_sha256"].items()
    }
    for name, item in json.loads((CANDIDATE / "environment-files.json").read_bytes()).items():
        value = {"source": str(CANDIDATE / "work/.venv" / name)}
        value.update(
            {"kind": "symlink", "target": item["link"]}
            if "link" in item
            else {
                "kind": "file",
                "sha256": item["sha256"],
                "mode": 0o755 if name.startswith("bin/") else 0o644,
            }
        )
        entries[".venv/" + name] = value
    binding = json.loads(Path(host.CONFIG + "clock-binding.json").read_bytes())
    bootstrap = json.loads(Path(binding["bootstrap_profile"]).read_bytes())
    protected = {
        str(p) for p in Path(host.CONFIG).iterdir() if p.is_file() and str(p) not in changes
    }
    protected |= set(bootstrap["guard_files"]) | {
        binding["bootstrap_profile"],
        bootstrap["runtime_profile"],
        bootstrap["rtc_confirmation"],
        "/etc/chrony/marketdata.conf",
        "/usr/bin/python3",
        str(Path("/usr/bin/python3").resolve()),
        "/var/lib/marketdata-clock/boot-state.json",
        host.CONFIG + "units/" + engine.TIMER,
        engine.SYSTEM + engine.AUTO + "-preview.service",
        "/home/yoonhc/.config/marketdata/restic-archive.toml",
    }
    for suffix in (".service", ".timer"):
        protected.add(host.USER + engine.AUTO + suffix)
    records = {}
    root = Path(host.STATE)
    for pattern in (
        "evidence/objects/*.json",
        "evidence/registration-receipts/*.json",
        "retention/plans/*.json",
        "retention/journals/*.json",
        "runs/*/run.json",
    ):
        for path in root.glob(pattern):
            records[str(path)] = host.fingerprint(path)
    verification = [
        "manifest.json",
        "environment-files.json",
        "source.tar.gz",
        "validation.json",
        "reproduction.json",
        "unit-validation.json",
        "system-preflight-review.json",
        report.name,
        "deployment-files.json",
        "projected-recurring.disabled.json",
        "preflight-inputs.json",
    ]
    plan = {
        "schema": 1,
        "purpose": "t05-recurring-install-enable-stop-rollback",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "boot_id": c.boot(),
        "release_id": manifest["release_id"],
        "release_path": "/opt/marketdata/releases/" + manifest["release_id"],
        "prior_release_path": "/opt/marketdata/releases/t05-completion-evidence-20261011-c9d413cfe11ec0e7",
        "candidate_source": str(CANDIDATE / "work"),
        "release_entries": entries,
        "tool_id": tool_id,
        "tool_files": tool_files,
        "changes": changes,
        "configuration_order": order,
        "enable_link": link,
        "disabled_proposal": proposal,
        "active_grant_rule": {
            "base": "disabled_proposal",
            "enabled": True,
            "approved_at": "first accepted explicit --approve-operation UTC timestamp, immutable on resume",
            "approval_reference": "/var/lib/marketdata-clock/t05-recurring-operations/OPERATION_ID/authority.json",
            "other_fields": "exactly equal; no scope substitution",
            "exact_active_bytes_and_hash": "persisted before grant write; not fabricated before approval",
        },
        "protected_files": {p: host.fingerprint(p) for p in sorted(protected)},
        "preserved_records": records,
        "normal_evidence_additions": "allowed; existing objects unchanged",
        "protected_processes": host.protected_processes(manager),
        "verification_inputs": {
            str(CANDIDATE / n): c.sha((CANDIDATE / n).read_bytes()) for n in verification
        },
        "schedule": {"kst": "15:40:00", "utc": "06:40:00", "persistent": False, "restart": "no"},
        "scope": "system automation only; user service/timer masked throughout",
        "rollback": "timer stop -> natural idle -> grant disabled -> system masks -> exact old config; preserve all data/evidence",
        "drain_seconds": 600,
        "operating_approval": "not yet granted",
    }
    c.require(not (Path(plan["release_path"]).exists()), "Candidate already installed; re-review")
    plan["operation_plan_id"] = engine.operation_id(plan)
    (HERE / "operation-plan.json").write_bytes(c.encode(plan))
    print(
        json.dumps(
            {
                "operation_plan_id": plan["operation_plan_id"],
                "tool_id": tool_id,
                "changes": len(changes),
                "release_objects": len(entries),
                "protected_files": len(protected),
                "preserved_records": len(records),
                "operating_writes": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
