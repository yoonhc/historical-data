"""Read-only post-activation checks; writes only a new workspace observation."""

import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPAIR = HERE.parent / "t18-archive-manager-recovery-2026-10-25"
spec = importlib.util.spec_from_file_location("observed_repair", REPAIR / "repair_resume.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
n, c, op = r.n, r.c, r.op


def main():
    recovery, plan, original = r.checked()
    host = n.Host(plan, original)
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "operating_mutations": False,
              "operation_id": plan["operation_id"], "recovery_id": recovery["recovery_id"], "passed": False}
    try:
        host.immutable()
        host.verify_release()
        report["immutable_inputs_and_release_verified"] = True
        authority = host.read("authority.json")
        assert c.file_sha(original / "authority.json") == recovery["authority_sha256"]
        report["authority"] = authority
        report["authority_sha256"] = recovery["authority_sha256"]
        grant = json.loads(host.raw(c.GRANT))
        expected = {**plan["prepared"]["recurring_proposal_after"], "enabled": True,
                    "approved_at": authority["approved_at"], "approval_reference": str(original / "authority.json")}
        assert grant == expected
        report["grant"] = {k: grant[k] for k in ("enabled", "scope_id", "approved_at", "approval_reference")}
        report["grant_sha256"] = c.file_sha(c.GRANT)
        report["configuration"] = {}
        for path, sides in plan["prepared"]["changes"].items():
            assert host.raw(path) == sides["after"]["text"].encode()
            report["configuration"][path] = c.node(path)
        manual = json.loads(host.raw(c.MANUAL))
        assert manual["enabled"] is False
        report["manual_disabled"] = True
        before = plan["prepared"]["ledger_before"]
        ledger = json.loads(host.raw(c.LEDGER))
        receipt = host.read("apply-receipt.json")
        assert ledger == receipt["ledger_after"] and ledger["phase"] == "closed"
        assert {k: v for k, v in ledger.items() if k not in ("profile_sha256", "configuration_history")} == {
            k: v for k, v in before.items() if k not in ("profile_sha256", "configuration_history")}
        assert ledger["configuration_history"][:-1] == before["configuration_history"]
        assert ledger["configuration_history"][-1]["plan_id"] == plan["operation_id"]
        report["ledger"] = {"sha256": c.file_sha(c.LEDGER), "phase": ledger["phase"],
                            "boot_id": ledger["boot_id"], "profile_sha256": ledger["profile_sha256"],
                            "prior_fields_preserved": True, "history_added": ledger["configuration_history"][-1]}
        state = host.read("state.json")
        assert state["phase"] == "activated"
        assert set(state["apply_starts"]) == set(op.TIMERS)
        assert all(v.get("returned") for v in state["apply_starts"].values())
        report["timers"] = host.timers(op.TIMERS)
        assert all(v["ActiveState"] == "active" for v in report["timers"].values())
        report["services"] = host.show((*op.SERVICES, *c.PROCESS_UNITS))
        row = report["services"][c.SERVICE]
        assert str(host.release / "policy/run_job.py") in row["ExecStart"]
        assert row["User"] == "yoonhc" and row["ProtectClock"] == "yes"
        assert row["Restart"] == "no" and row["NeedDaemonReload"] == "no" and not row["DropInPaths"]
        assert not any(host.optional_commands(row).values())
        report["original_failure_hashes"] = {}
        for name, digest in recovery["initial_records"].items():
            if name.startswith("attempt-"):
                assert c.file_sha(original / name) == digest
                report["original_failure_hashes"][name] = digest
        v = os.statvfs(c.ROOT)
        report["home_available_bytes"] = v.f_bavail * v.f_frsize
        report["activation_receipt"] = str(original / "apply-receipt.json")
        report["recovery_attempt"] = "/var/lib/marketdata-clock/t18-output-recoveries/" + recovery["recovery_id"] + "/attempt-fe166c7a29ed48debc820fe398155cef.json"
        final = json.loads(Path(report["recovery_attempt"]).read_bytes())
        assert final["passed"] and final["original_authority_unchanged"] and final["protected_processes_unchanged"]
        report["recovery_attempt_sha256"] = c.file_sha(report["recovery_attempt"])
        report["passed"] = True
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        output = HERE / ("activation-observed-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
        with output.open("x") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"passed": report["passed"], "report": str(output),
                          "timer_states": {k: v["ActiveState"] for k, v in report.get("timers", {}).items()},
                          "ledger": report.get("ledger"), "grant": report.get("grant"),
                          "home_available_bytes": report.get("home_available_bytes")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
