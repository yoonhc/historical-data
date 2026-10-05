"""Apply, test and reverse-check a frozen release in a new /tmp directory only."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    bundle, destination = Path(__file__).resolve().parent, args.destination.resolve()
    assert destination.is_relative_to(Path("/tmp")) and destination != Path("/tmp")
    assert not destination.exists(), "A fresh directory is required; nothing is overwritten"
    result_path = args.results.resolve()
    assert not result_path.exists()
    release = json.loads((bundle / "release.json").read_bytes())
    assert sha(bundle / "baseline.tar.gz") == release["baseline_sha256"]
    assert sha(bundle / "candidate.patch") == release["patch_sha256"]
    assert sha(Path(__file__)) == release["support_files_sha256"]["reproduce.py"]
    record = {
        "release_id": release["release_id"],
        "destination": str(destination),
        "started_at": datetime.now().astimezone().isoformat(),
        "steps": [],
        "state": "running",
    }
    result_path.write_text(json.dumps(record, indent=2) + "\n")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [
                str(destination / "packages/marketdata/src"),
                str(destination / "packages/marketdata_freqtrade/src"),
                str(destination),
            ]
        ),
        "PYTHONNOUSERSITE": "1",
    }

    def run(name, command):
        result = subprocess.run(
            command, cwd=destination, env=env, capture_output=True, text=True, timeout=120
        )
        record["steps"].append(
            {
                "name": name,
                "command": command,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        assert result.returncode == 0, (name, result.stdout, result.stderr)
        return result.stdout

    def verify(hashes):
        assert all(sha(destination / name) == value for name, value in hashes.items())

    try:
        destination.mkdir()
        with tarfile.open(bundle / "baseline.tar.gz") as archive:
            members = archive.getmembers()
            assert {m.name for m in members} == set(release["baseline_files_sha256"])
            assert all(m.isfile() for m in members)
            archive.extractall(destination, filter="data")
        verify(release["baseline_files_sha256"])
        run("apply_check", ["git", "apply", "--check", str(bundle / "candidate.patch")])
        run("apply", ["git", "apply", str(bundle / "candidate.patch")])
        verify(release["final_files_sha256"])
        origins = json.loads(
            run(
                "import_origins",
                [
                    sys.executable,
                    "-c",
                    "import json; from marketdata import depth,depth_stream,depth_refresh; "
                    "print(json.dumps({m.__name__:m.__file__ for m in (depth,depth_stream,depth_refresh)}))",
                ],
            )
        )
        assert all(Path(p).is_relative_to(destination) for p in origins.values())
        record["module_origins"] = origins
        changed = [c["path"] for c in release["changed_files"]]
        run("ruff_check", [sys.executable, "-m", "ruff", "check", *changed])
        run("ruff_format", [sys.executable, "-m", "ruff", "format", "--check", *changed])
        xml = result_path.with_suffix(".xml")
        run(
            "depth_regression",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                *[
                    str(p.relative_to(destination))
                    for p in sorted((destination / "tests").glob("test_depth*.py"))
                ],
                "--junitxml=" + str(xml),
            ],
        )
        tests = ET.parse(xml).getroot().find("testsuite").attrib
        assert int(tests["tests"]) == release["expected_depth_tests"]
        assert all(tests[k] == "0" for k in ("errors", "failures", "skipped"))
        record["tests"] = tests
        run(
            "rollback_check",
            ["git", "apply", "--reverse", "--check", str(bundle / "candidate.patch")],
        )
        run("rollback", ["git", "apply", "--reverse", str(bundle / "candidate.patch")])
        verify(release["baseline_files_sha256"])
        assert all(
            not (destination / c["path"]).exists()
            for c in release["changed_files"]
            if c["before_sha256"] is None
        )
        run("reapply", ["git", "apply", str(bundle / "candidate.patch")])
        verify(release["final_files_sha256"])
        record.update(state="passed", final_hashes_match=True, rollback_baseline_match=True)
    except BaseException as exc:
        record.update(state="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        record["finished_at"] = datetime.now().astimezone().isoformat()
        result_path.write_text(json.dumps(record, indent=2) + "\n")
    print(
        json.dumps(
            {
                "release_id": record["release_id"],
                "state": record["state"],
                "tests": record["tests"],
                "rollback_baseline_match": True,
            }
        )
    )


if __name__ == "__main__":
    main()
