"""Read current configuration and seal trial inputs; writes only this report."""
import hashlib
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
C = HERE.parent / "scheduled-clock-policy-storage-2026-10-16"


def encode(v):
    return (json.dumps(v, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def node(path):
    p = Path(path)
    if p.is_symlink():
        return {"kind": "symlink", "target": os.readlink(p)}
    if not p.exists():
        return {"kind": "absent"}
    raw = p.read_bytes()
    return {"kind": "file", "text": raw.decode(), "mode": p.stat().st_mode & 0o777,
            "sha256": sha(raw)}


def build():
    m = json.loads((C / "manifest.json").read_bytes())
    for path, digest in m["runtime_files"].items():
        assert sha((C / "candidate" / path).read_bytes()) == digest, path
    (HERE / "candidate-inputs.json").write_bytes(encode(m))
    import transition as t
    operations = {}
    for path in t.allowed_paths():
        before = node(path)
        if path in m["before"]:
            assert before["kind"] == "file" and before["sha256"] == m["before"][path], path
        else:
            assert before == {"kind": "absent"}, path
        if path.startswith(t.SYSTEM):
            src = C / "rendered/units" / Path(path).name
        elif path in (t.BINDING, t.T05_BINDING):
            src = C / "rendered/clock-binding.json"
        else:
            src = C / "rendered" / Path(path).name
        after = node(src)
        after["mode"] = 0o644  # deployed configuration, not workspace group-write mode
        assert after["sha256"] == m["after"][path]
        operations[path] = {"before": before, "after": after}
    profile = json.loads(operations[t.PROFILE]["before"]["text"])
    protected = {p: {"kind": "digest", "sha256": digest}
                 for p, digest in profile["guard_files"].items() if p not in operations}
    for job in t.JOBS:
        for ext in ("service", "timer"):
            p = "/home/yoonhc/.config/systemd/user/marketdata-" + job + "." + ext
            assert node(p) == {"kind": "symlink", "target": "/dev/null"}, p
            protected[p] = node(p)
        p = t.SYSTEM + "timers.target.wants/marketdata-" + job + ".timer"
        protected[p] = node(p)
    masks = {manager + ":marketdata-checkpoint-retirement." + ext: {"kind": "symlink", "target": "/dev/null"}
             for manager in ("user", "system") for ext in ("service", "timer")}
    for key, expected in masks.items():
        manager, name = key.split(":")
        p = (t.SYSTEM if manager == "system" else "/home/yoonhc/.config/systemd/user/") + name
        assert node(p) == expected
    plan = {"schema": 1, "purpose": t.PURPOSE, "policy_changes": t.POLICY,
            "release_id": m["release_id"], "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "ledger_path": t.LEDGER, "bootstrap_path": t.PROFILE,
            "ledger_before": json.loads(Path(t.LEDGER).read_bytes()), "operations": operations,
            "protected": protected, "retirement_masks": masks}
    plan["plan_id"] = sha(encode(plan))
    approval = {"enabled": False, "purpose": t.PURPOSE, "plan_id": plan["plan_id"],
                "approval_reference": "", "directions": ["apply", "rollback"]}
    t.validate(plan, {**approval, "enabled": True, "approval_reference": "isolated validation only"})
    (HERE / "configuration-plan.json").write_bytes(encode(plan))
    (HERE / "approval.template.json").write_bytes(encode(approval))
    # All Python support, fixture tests, installed packages and symlinks are sealed.
    payload = {}
    for p in (C / "candidate").rglob("*"):
        rel = p.relative_to(C)
        if any(x in {"__pycache__", ".pytest_cache", ".cache"} for x in rel.parts):
            continue
        if p.is_symlink():
            target = os.readlink(p)
            assert target in {"/usr/bin/python3", "python", "python3", "lib"}, (p, target)
            payload[str(rel)] = {"kind": "symlink", "target": target}
        elif p.is_file():
            payload[str(rel)] = {"kind": "file", "sha256": sha(p.read_bytes()),
                                 "mode": 0o755 if p.stat().st_mode & 0o111 else 0o644}
    for p in (C / "tests").glob("*.py"):
        payload[str(p.relative_to(C))] = {"kind": "file", "sha256": sha(p.read_bytes()), "mode": 0o644}
    payload["parents.json"] = {"kind": "file", "sha256": sha((C / "parents.json").read_bytes()), "mode": 0o644}
    (HERE / "payload.json").write_bytes(encode(payload))
    files = {str(p.relative_to(HERE)): sha(p.read_bytes())
             for p in HERE.rglob("*.py") if "__pycache__" not in p.parts}
    for name in ("candidate-inputs.json", "configuration-plan.json", "payload.json"):
        files[name] = sha((HERE / name).read_bytes())
    seal = {"release_id": m["release_id"], "source_sha256": m["source_sha256"],
            "files": files, "payload_source": str(C), "plan_id": plan["plan_id"]}
    seal["tool_id"] = "scheduled-clock-preflight-20261016-" + sha(encode(seal))[:16]
    (HERE / "tool-seal.json").write_bytes(encode(seal))
    print(json.dumps({"plan_id": plan["plan_id"], "tool_id": seal["tool_id"],
                      "payload_objects": len(payload), "configuration_paths": len(operations),
                      "operating_approval": False}))


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(HERE))
    build()
