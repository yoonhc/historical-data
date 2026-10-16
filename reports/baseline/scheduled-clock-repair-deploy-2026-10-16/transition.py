"""T16-specific contract over the unchanged T15 transaction engine.

The command can inspect a plan or exercise an explicit isolated filesystem only.
It exposes no operating service/configuration/ledger mutation command.
"""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("t16_transaction_engine", HERE / "vendor/transaction_engine.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
PURPOSE = "scheduled-clock-policy-configuration-v1"
POLICY = ["scheduled-decisions-v1; eight jobs only"]
PROFILE, LEDGER = engine.PROFILE, engine.LEDGER
JOBS = engine.JOBS
SYSTEM = "/etc/systemd/system/"
BINDING = "/etc/marketdata/scheduled-clock-policy/clock-binding.json"
T05_BINDING = "/etc/marketdata/checkpoint-clock-retirement/clock-binding.json"
TOPOLOGY = "/etc/marketdata/scheduled-system/topology.json"
WAITER = SYSTEM + "marketdata-jobs-clock-ready.service"
sha, encode, canonical, Hold = engine.sha, engine.encode, engine.canonical, engine.Hold


def require(ok, message):
    if not ok:
        raise Hold(message)


def allowed_paths():
    return {PROFILE, BINDING, T05_BINDING, TOPOLOGY, WAITER} | {
        SYSTEM + "marketdata-" + job + ".service" for job in JOBS
    }


def validate(plan, approval):
    require(plan.get("schema") == 1 and plan.get("purpose") == PURPOSE
            and plan.get("policy_changes") == POLICY, "Wrong T16 scope")
    require(plan.get("plan_id") == sha(encode({k: v for k, v in plan.items() if k != "plan_id"})),
            "Plan hash differs")
    require(approval.get("enabled") is True and approval.get("purpose") == PURPOSE
            and approval.get("plan_id") == plan["plan_id"] and approval.get("approval_reference")
            and set(approval.get("directions", [])) == {"apply", "rollback"}, "Exact approval required")
    require(plan.get("ledger_path") == LEDGER and plan.get("bootstrap_path") == PROFILE,
            "Unexpected control paths")
    require(set(plan["operations"]) == allowed_paths(), "Unexpected write scope")
    frozen = json.loads((HERE / "candidate-inputs.json").read_text())
    require(plan["release_id"] == frozen["release_id"], "Release differs")
    for path, op in plan["operations"].items():
        require(set(op) == {"before", "after"}, "Unexpected operation")
        for side, node in op.items():
            expected = frozen[side].get(path)
            if expected is None:
                require(node == {"kind": "absent"}, "Unexpected existing entrypoint: " + path)
            else:
                require(node.get("kind") == "file" and node.get("mode") == 0o644
                        and node.get("sha256") == expected == sha(node["text"].encode()),
                        "Not the reviewed configuration: " + path)
    before, after = (json.loads(plan["operations"][PROFILE][s]["text"]) for s in ("before", "after"))
    require(set(before) == set(after), "Bootstrap schema differs")
    require(all(before[k] == after[k] for k in before if k != "guard_files"),
            "Boot preparation or inventory policy changed")
    permitted_guards = allowed_paths() - {PROFILE, BINDING, T05_BINDING}
    for path, digest in before["guard_files"].items():
        require(path in permitted_guards or after["guard_files"].get(path) == digest,
                "Unrelated guard changed")
    for path in permitted_guards:
        require(after["guard_files"].get(path) == plan["operations"][path]["after"]["sha256"],
                "Target entrypoint not pinned")
    expected_binding = {"bootstrap_profile": PROFILE,
                        "bootstrap_profile_sha256": plan["operations"][PROFILE]["after"]["sha256"]}
    for path in (BINDING, T05_BINDING):
        require(json.loads(plan["operations"][path]["after"]["text"]) == expected_binding,
                "Binding mismatch")
    for job in JOBS:
        timer = SYSTEM + "marketdata-" + job + ".timer"
        require(plan["protected"].get(timer) == {"kind": "digest", "sha256": frozen["before"][timer]},
                "Timer protection missing")
    ledger = plan["ledger_before"]
    require(ledger.get("schema") == 1 and ledger.get("phase") == "closed"
            and ledger.get("boot_id") == plan["boot_id"]
            and ledger.get("profile_sha256") == canonical(before)
            and ledger.get("closed_observation", {}).get("ready"), "Same-boot closed preparation required")


# Only this private module object is adapted. The vendored engine stays byte-identical.
engine.validate = validate
engine.PURPOSE = PURPOSE
Transaction, Files = engine.Transaction, engine.Files


def pending_requests(root):
    """Read-only inventory, used only after start barriers and drain, under job locks.

    A nonterminal request blocks a different configuration. Rollback with such a
    request needs an explicit preserved-work reconciliation; this function never
    clears a request or authorizes reissuing its body.
    """
    import fcntl
    import os
    result = {}
    for job in JOBS:
        directory = Path(root) / job
        request = directory / "request.json"
        if not directory.exists():
            continue
        require(not directory.is_symlink() and not request.is_symlink(), "Unsafe request path")
        lock = directory / "request.lock"
        if not request.exists():
            continue
        require(lock.is_file() and not lock.is_symlink(), "Existing request lock missing")
        fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raw = request.read_bytes()
            value = json.loads(raw)
            result[job] = {"sha256": sha(raw), "state": value.get("state"),
                           "identity": value.get("identity"), "pins": value.get("pins"),
                           "requires_reconciliation": value.get("state") != "complete"}
        finally:
            os.close(fd)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, default=HERE / "configuration-plan.json")
    args = p.parse_args()
    plan = json.loads(args.plan.read_text())
    validate(plan, {"enabled": True, "purpose": PURPOSE, "plan_id": plan["plan_id"],
                    "approval_reference": "inspection only", "directions": ["apply", "rollback"]})
    print(json.dumps({"plan_id": plan["plan_id"], "configuration_paths": len(plan["operations"]),
                      "inspection_only": True, "production_approval": False}))
