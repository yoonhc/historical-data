"""Local candidate tests and relocated installation only; no operating controls."""

import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import installation  # noqa: E402
from vendor import handoff_common as c  # noqa: E402


def main():
    plan = json.loads((HERE / "operation-plan.json").read_bytes())
    report = {
        "operation_plan_id": plan["operation_plan_id"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tool_files": plan["tool_files"],
        "test_sha256": c.sha((HERE / "test_transition.py").read_bytes()),
        "passed": False,
        "operating_writes": 0,
    }
    for name, digest in plan["tool_files"].items():
        c.require(c.sha((HERE / name).read_bytes()) == digest, "Tool changed after plan")
    argv = [
        str(HERE.parents[2] / ".venv/bin/python"),
        "-B",
        "-m",
        "pytest",
        "-q",
        str(HERE / "test_transition.py"),
    ]
    test = subprocess.run(argv, capture_output=True, text=True, timeout=90)
    (HERE / "transition-tests.txt").write_text(test.stdout + test.stderr)
    c.require(test.returncode == 0, "Transition tests failed")
    report["tests"] = {
        "command": argv,
        "output_sha256": c.sha((HERE / "transition-tests.txt").read_bytes()),
        "result": test.stdout.strip().splitlines()[-1],
    }
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="t05-recurring-install-") as directory:
        target = Path(directory) / plan["release_id"]
        copied = installation.install(
            target, plan["release_entries"], plan["release_id"], live=False
        )
        checked = installation.verify(
            target, plan["release_entries"], plan["release_id"], live=False
        )
        command = [
            str(target / ".venv/bin/python"),
            "-B",
            "-I",
            str(HERE / "inspect_installed.py"),
            "--release",
            str(target),
            "--settings",
            "/etc/marketdata/checkpoint-clock-retirement/settings.toml",
            "--mode",
            "import",
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
        c.require(result.returncode == 0, "Relocated entrypoint failed: " + result.stderr)
        report["relocation"] = {
            "copy": copied,
            "verify": checked,
            "import": json.loads(result.stdout),
            "seconds": time.monotonic() - start,
            "limit": "Temporary UID 1000 path; final /opt root ownership/propose is an apply-time condition",
        }
    report["temporary_removed"] = not Path(directory).exists()
    report["reused_evidence"] = {
        "candidate_cases": 61,
        "rerun": False,
        "root_preflight": "system-preflight-result-fe42ff4321294b9a.json",
        "rerun_root": False,
        "manual_deletion": "f57f334be39e4137a4b7785836b6d2bd",
        "repeated": False,
    }
    report["limits"] = [
        "Manager commands are doubles in new transition tests; prior real-manager preflight reused",
        "Real SIGKILL covers local installs/transactions, not production managers",
        "No production install, recurring grant, timer activation or checkpoint deletion",
        "Final-path scope/guard and first scheduled/automatic deletion observations remain",
    ]
    report["passed"] = True
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (HERE / "validation.json").write_bytes(c.encode(report))
    print(
        json.dumps(
            {
                "passed": True,
                "tests": report["tests"]["result"],
                "installation_objects": copied["objects"],
                "temporary_removed": report["temporary_removed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
