"""Freeze the two-file repair, current configuration, and one exact recovery.

Read operating files only. All output stays beside this script. No approval.
"""

import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
PRIOR = HERE.with_name("scheduled-clock-deploy-2026-10-16")
FIX = HERE.with_name("scheduled-clock-result-fix-2026-10-16")
sys.path.insert(0, str(HERE))
import transition as t


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def save(name, value):
    (HERE / name).write_bytes(encode(value))


def node(path):
    p = Path(path)
    if p.is_symlink():
        return {"kind": "symlink", "target": str(p.readlink())}
    if not p.exists():
        return {"kind": "absent"}
    raw = p.read_bytes()
    return {"kind": "file", "mode": 0o644, "text": raw.decode(), "sha256": sha(raw)}


def build():
    old_plan = json.loads((PRIOR / "host-plan.json").read_bytes())
    old_manifest = json.loads((PRIOR / "candidate-inputs.json").read_bytes())
    fixed = json.loads((FIX / "manifest.json").read_bytes())
    old_release = "/opt/marketdata/releases/" + old_manifest["release_id"]
    new_release = "/opt/marketdata/releases/" + fixed["release_id"]
    expected_changed = {"policy/request_contract.py", "policy/run_job.py"}
    assert set(fixed["runtime_files"]) == set(old_manifest["runtime_files"])
    changed = {
        n
        for n, digest in fixed["runtime_files"].items()
        if old_manifest["runtime_files"][n] != digest
    }
    assert changed == expected_changed
    for name, digest in fixed["runtime_files"].items():
        assert sha((FIX / "candidate" / name).read_bytes()) == digest, name
        assert (
            sha((Path(old_release) / name).read_bytes()) == old_manifest["runtime_files"][name]
        ), name

    operations = {}
    before_hashes = {}
    for path in sorted(t.allowed_paths()):
        before = node(path)
        assert before == old_plan["inner"]["operations"][path]["after"], path
        operations[path] = {"before": before}
        before_hashes[path] = before["sha256"]
    for path, op in operations.items():
        if path.startswith(t.SYSTEM):
            assert old_release in op["before"]["text"], path
            raw = op["before"]["text"].replace(old_release, new_release).encode()
            op["after"] = {"kind": "file", "mode": 0o644, "sha256": sha(raw), "text": raw.decode()}
    topology = json.loads(
        operations[t.TOPOLOGY]["before"]["text"].replace(old_release, new_release)
    )
    raw = encode(topology)
    operations[t.TOPOLOGY]["after"] = {
        "kind": "file",
        "mode": 0o644,
        "sha256": sha(raw),
        "text": raw.decode(),
    }
    profile = json.loads(operations[t.PROFILE]["before"]["text"])
    for path, op in operations.items():
        if "after" in op:
            profile["guard_files"][path] = op["after"]["sha256"]
    raw = encode(profile)
    operations[t.PROFILE]["after"] = {
        "kind": "file",
        "mode": 0o644,
        "sha256": sha(raw),
        "text": raw.decode(),
    }
    binding = encode({"bootstrap_profile": t.PROFILE, "bootstrap_profile_sha256": sha(raw)})
    for path in (t.BINDING, t.T05_BINDING):
        operations[path]["after"] = {
            "kind": "file",
            "mode": 0o644,
            "sha256": sha(binding),
            "text": binding.decode(),
        }
    for job in t.JOBS:
        path = t.SYSTEM + "marketdata-" + job + ".timer"
        before_hashes[path] = sha(Path(path).read_bytes())
        assert before_hashes[path] == old_manifest["before"][path]
    frozen = {
        **fixed,
        "before": before_hashes,
        "after": {p: op["after"]["sha256"] for p, op in operations.items()},
        "prior_release_id": old_manifest["release_id"],
        "runtime_changes": sorted(changed),
    }
    save("candidate-inputs.json", frozen)
    inner = {
        **old_plan["inner"],
        "release_id": fixed["release_id"],
        "operations": operations,
        "ledger_before": json.loads(Path(t.LEDGER).read_bytes()),
    }
    inner.pop("plan_id")
    inner["plan_id"] = sha(encode(inner))
    save("configuration-plan.json", inner)
    t.validate(
        inner,
        {
            "purpose": t.PURPOSE,
            "enabled": True,
            "plan_id": inner["plan_id"],
            "approval_reference": "read-only preparation",
            "directions": ["apply", "rollback"],
        },
    )

    # Unchanged installed packages/interpreter links are the exact previously
    # tested payload; only the two source files have different bytes.
    payload = json.loads((PRIOR / "payload.json").read_bytes())
    for name, value in payload.items():
        source = Path(old_plan["payload_source"]) / name
        if name.removeprefix("candidate/") in changed:
            source = FIX / name
            value["sha256"] = sha(source.read_bytes())
        if value["kind"] == "file":
            assert sha(source.read_bytes()) == value["sha256"], name
            value["source"] = str(source)
        else:
            assert str(source.readlink()) == value["target"]
    save("payload.json", payload)

    review = json.loads((FIX / "recovery-review.json").read_bytes())
    original = Path(review["request_path"]).read_bytes()
    assert sha(original) == review["request_sha256"]
    request = json.loads(original)
    journal = Path(review["request_path"]).with_name("decisions.jsonl")
    journal_raw = journal.read_bytes()
    assert journal_raw.endswith(b"\n")
    report = Path(review["report_path"]).read_bytes()
    assert sha(report) == review["report_sha256"]
    recovery = {
        "purpose": "t16-exact-oi-result-recovery-v1",
        "schema": 1,
        "boot_id": inner["boot_id"],
        "job": "oi-quality",
        "release": old_release,
        "request_path": review["request_path"],
        "request_sha256": sha(original),
        "request_id": review["request_id"],
        "identity": review["identity"],
        "pins": request["pins"],
        "original_attempt_id": review["attempt_id"],
        "original_invocation_id": review["invocation_id"],
        "report_path": review["report_path"],
        "report_sha256": sha(report),
        "report_id": review["report_id"],
        "journal_path": str(journal),
        "journal_prefix_bytes": len(journal_raw),
        "journal_prefix_sha256": sha(journal_raw),
        "binding": t.BINDING,
        "scope": request["scope"],
        "arguments": request["arguments"],
        "original_process_exit_code": 1,
        "derived_report_exit_code": 2,
        "observed_work_exit_code": None,
        "work_invocations": 0,
        "preserve": [
            "original request and failure",
            "report bytes and quality findings",
            "identity and pins",
        ],
        "source_files": {
            n: old_manifest["runtime_files"][n]
            for n in (
                "policy/run_job.py",
                "policy/request_contract.py",
                "base/packages/marketdata/src/marketdata/job_clock.py",
                "policy/job_clock.py",
            )
        },
    }
    recovery["plan_id"] = sha(encode(recovery))
    save("recovery-plan.json", recovery)
    evidence = HERE / "recovery-inputs"
    evidence.mkdir(exist_ok=True)
    for name, raw in (
        ("request.json", original),
        ("report.json", report),
        ("decisions.jsonl", journal_raw),
    ):
        (evidence / name).write_bytes(raw)
    save(
        "preflight-reuse.json",
        {
            "purpose": "two-encoder-fix-only",
            "previous_host_plan_id": old_plan["plan_id"],
            "previous_release_id": old_manifest["release_id"],
            "previous_source_sha256": old_manifest["source_sha256"],
            "previous_configuration_plan_id": old_plan["inner"]["plan_id"],
            "previous_preflight": old_plan["preflight"],
            "new_release_id": fixed["release_id"],
            "runtime_changes": {
                n: {"before": old_manifest["runtime_files"][n], "after": fixed["runtime_files"][n]}
                for n in sorted(changed)
            },
            "scope": "Reuse prior real systemd access/protection/timer/monitor tests for unchanged paths and code. New repair and host transition tested separately; final /opt imports checked before timer hold.",
            "new_runtime_system_preflight_performed": False,
        },
    )
    print(
        json.dumps(
            {
                "release_id": fixed["release_id"],
                "configuration_plan_id": inner["plan_id"],
                "recovery_plan_id": recovery["plan_id"],
                "payload_objects": len(payload),
                "operating_writes": False,
            }
        )
    )


if __name__ == "__main__":
    build()
