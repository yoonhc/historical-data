"""Package the isolated helper and reproduce tests from a clean extraction."""

import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    names = ("clock_config_transition.py", "clock_gate.py", "marketdata_runtime.py")
    hashes = {n: digest(ROOT / "work" / n) for n in names}
    identity = (
        "clock-config-20261009-"
        + hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]
    )
    release = ROOT / "release-final"
    release.mkdir(exist_ok=False)
    for n in names:
        shutil.copyfile(ROOT / "work" / n, release / n)
        (release / n).chmod(0o444)
    shutil.copyfile(ROOT / "test_transition.py", release / "test_transition.py")
    (release / "work").mkdir()
    for n in names:
        shutil.copyfile(release / n, release / "work" / n)
    manifest = {
        "release_id": identity,
        "runtime_files_sha256": hashes,
        "tests_sha256": digest(release / "test_transition.py"),
        "operating_release_unchanged": "system-bootstrap-20261007-f85eb8321a19c1ee",
        "operating_deployed": False,
        "source_configuration_qualified": False,
        "scope": "Standalone configuration transition; original clock gate/runtime byte-identical",
    }
    (release / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    archive = ROOT / (identity + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(release, arcname=identity)
    clean = Path(tempfile.mkdtemp(prefix="md-clock-config-final-"))
    with tarfile.open(archive) as tar:
        tar.extractall(clean, filter="data")
    extracted = clean / identity
    for n, expected in hashes.items():
        assert digest(extracted / n) == expected
        assert digest(extracted / "work" / n) == expected
    argv = [
        str(PROJECT / ".venv/bin/python"),
        "-m",
        "pytest",
        "-q",
        str(extracted / "test_transition.py"),
        "--junitxml=" + str(ROOT / "clean-transition-tests-final.xml"),
    ]
    run = subprocess.run(argv, cwd=clean, capture_output=True, text=True)
    (ROOT / "clean-tests-final.stdout").write_text(run.stdout)
    (ROOT / "clean-tests-final.stderr").write_text(run.stderr)
    manifest.update(
        archive=str(archive),
        archive_sha256=digest(archive),
        clean_directory=str(extracted),
        test_command=argv,
        test_returncode=run.returncode,
        clean_hashes_match=True,
        root_application_tested=False,
        test_scope="Real files/flock/SIGKILL and unchanged gate; service/provider calls use explicit doubles",
    )
    (ROOT / "release-final.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    print(run.stdout)
    if run.returncode:
        raise SystemExit(run.stderr)


if __name__ == "__main__":
    main()
