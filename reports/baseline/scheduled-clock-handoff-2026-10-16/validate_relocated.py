"""Non-root rehearsal of the exact copied environments and administrator driver."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
C = HERE.parent / "scheduled-clock-policy-storage-2026-10-16"


def main():
    payload = json.loads((HERE / "payload.json").read_bytes())
    root = Path(tempfile.mkdtemp(prefix="t16-relocated-"))
    trial, state = root / "release", root / "state"
    trial.mkdir(); state.mkdir()
    for rel, spec in payload.items():
        p = trial / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if spec["kind"] == "symlink":
            p.symlink_to(spec["target"])
        else:
            shutil.copyfile(C / rel, p)
            p.chmod(spec["mode"])
    shutil.copyfile(HERE / "trial_worker.py", trial / "trial_worker.py")
    records = []
    for job in ("oi", "oi-probe", "oi-quality", "daily", "pack", "backup", "restic-archive", "network-observer"):
        variant = "daily" if job == "daily" else "preserve" if job in ("backup", "restic-archive") else "base"
        directory = state / job; directory.mkdir()
        argv = [str(trial / "candidate" / variant / ".venv/bin/python"), "-B", "-I",
                str(trial / "trial_worker.py"), "--trial", str(trial), "--state", str(directory),
                "--job", job, "--local-check"]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=40)
        (directory / "stdout.txt").write_text(result.stdout)
        (directory / "stderr.txt").write_text(result.stderr)
        record = {"job": job, "returncode": result.returncode, "argv": argv}
        if (directory / "result.json").exists():
            record["worker"] = json.loads((directory / "result.json").read_bytes())
            record["junit"] = (directory / "tests.xml").read_text()
        records.append(record)
        print(json.dumps({"job": job, "returncode": result.returncode}), flush=True)
    result = {"trial": str(root), "actual_systemd": False, "passed": all(r["returncode"] == 0 for r in records),
              "records": records, "temporary_copy_retained_until_review": True}
    (HERE / "relocated-validation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result["passed"]


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
