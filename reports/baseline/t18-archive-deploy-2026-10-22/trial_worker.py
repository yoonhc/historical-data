"""UID 1000 fixture output sharing plus read-only actual clock/proposal checks."""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--release", type=Path, required=True)
    p.add_argument("--mode", choices=("before", "after"), required=True)
    p.add_argument("--service", required=True)
    p.add_argument("--timer", required=True)
    args = p.parse_args()
    here = Path(__file__).resolve().parent
    c = load("trial_common", here / "common.py")
    plan = json.loads((here / "prepared-plan.json").read_bytes())
    c.require(os.getuid() == 1000, "Expected actual service UID")
    sys.path[:0] = [
        str(args.release / "preserve/packages/marketdata/src"),
        str(args.release / "preserve"),
    ]
    from marketdata.domain import json_bytes, sha256
    from marketdata.store import atomic_write

    from scripts import archive_backups, archive_output, restic_schedule

    for module, name in (
        (archive_backups, "archive_backups.py"),
        (archive_output, "archive_output.py"),
    ):
        c.require(Path(module.__file__) == args.release / "preserve/scripts" / name, "Wrong import")
        c.require(
            c.file_sha(module.__file__)
            == plan["release_entries"]["preserve/scripts/" + name]["sha256"],
            "Import digest differs",
        )
    command = [
        plan["python"],
        "-B",
        "-I",
        str(args.release / "policy/run_job.py"),
        "--job",
        "restic-archive",
        "--binding",
        c.BINDING,
        "--check",
    ]
    checked = subprocess.run(command, capture_output=True, text=True, timeout=30)
    c.require(checked.returncode == 0, "Read-only CLI failed: " + checked.stderr[-2000:])
    clock = json.loads(checked.stdout)
    c.require(clock["ready"] is True, "Clock check did not pass")
    proposal_command = [
        str(c.T05 / ".venv/bin/python"),
        "-B",
        "-I",
        str(c.T05 / "scripts/run_clock_retirement.py"),
        "--settings",
        "/etc/marketdata/checkpoint-clock-retirement/settings.toml",
        "propose",
    ]
    proposal = subprocess.run(proposal_command, capture_output=True, text=True, timeout=30)
    c.require(proposal.returncode == 0, "Read-only T05 proposal failed: " + proposal.stderr[-2000:])
    actual = json.loads(proposal.stdout)
    expected = (
        plan["recurring_proposal_after"]
        if args.mode == "after"
        else json.loads(plan["recurring_before"]["text"])
    )
    expected = {**expected, "enabled": False, "approved_at": None, "approval_reference": None}
    c.require(actual == expected, "Full T05 proposal differs")
    expected_binding = plan["changes"][c.BINDING][args.mode]["text"]
    c.require(Path(c.BINDING).read_text() == expected_binding, "Wrong namespace binding")
    result = {
        "uid": os.getuid(),
        "python": sys.executable,
        "mode": args.mode,
        "imports": {m.__name__: str(m.__file__) for m in (archive_backups, archive_output)},
        "clock": clock,
        "proposal": actual,
        "context": restic_schedule.service_context(args.service, args.timer, manager="system"),
        "writes_only_trial_output": True,
    }
    reports = args.state / "reports"
    reports.mkdir(exist_ok=True)
    raw = json_bytes({"manifest-fixture": "x" * (1024 * 512)})
    refs = []
    for _ in range(2):
        run = reports / uuid.uuid4().hex
        run.mkdir()
        out = run / (uuid.uuid4().hex + ".stdout")
        with out.open("xb") as f:
            f.write(raw)
        receipt = {
            "arguments": ["dump", "b" * 64, "/backup.json"],
            "operation": "dump",
            "returncode": 0,
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "stdout": str(out),
        }
        atomic_write(out.with_suffix(".json"), json_bytes(receipt))
        proof = archive_output.retain_verified_manifest(
            reports, "a" * 64, receipt, raw, sha256(raw)
        )
        body = Path(proof["body"])
        c.require(
            out.read_bytes() == raw and out.stat().st_ino == body.stat().st_ino,
            "Hardlink/replace failed",
        )
        refs.append(
            {
                "stdout": str(out),
                "receipt": str(out.with_suffix(".json")),
                "body": str(body),
                "sha256": sha256(raw),
                "receipt_sha256": c.file_sha(out.with_suffix(".json")),
                "inode": out.stat().st_ino,
                "device": out.stat().st_dev,
                "mode": out.stat().st_mode & 0o777,
            }
        )
    c.require(refs[0]["inode"] == refs[1]["inode"], "Repeated body was not shared")
    contract = load("new_contract", args.release / "policy/request_contract.py")
    sealed = contract.seal_result(
        {
            "job": "restic-archive",
            "result": {},
            "files": [r["stdout"] for r in refs] + [r["receipt"] for r in refs],
            "records": [],
        }
    )
    previous = args.state / "before.json"
    if args.mode == "after":
        old = json.loads(previous.read_bytes())
        load("old_contract", c.BASE / "policy/request_contract.py").verify_result(old["sealed"])
        c.require(
            old["outputs"][0]["inode"] == refs[0]["inode"], "Cross-invocation sharing differs"
        )
        result["prior_receipt_and_output_preserved"] = True
    result.update(
        outputs=refs, sealed=sealed, mountinfo=Path("/proc/self/mountinfo").read_text(), passed=True
    )
    atomic_write(args.state / (args.mode + ".json"), json_bytes(result))


if __name__ == "__main__":
    main()
