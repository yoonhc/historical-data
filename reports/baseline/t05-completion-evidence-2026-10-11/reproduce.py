"""Rebuild the frozen candidate and test only isolated stores; never install it."""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = HERE / "reproduced"
BASE = HERE.parent / "t05-clock-retirement-2026-10-11"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((HERE / "manifest.json").read_text())
    assert digest(HERE / "source.tar.gz") == manifest["source_archive_sha256"]
    DEST.mkdir(exist_ok=False)
    with tarfile.open(HERE / "source.tar.gz") as archive:
        assert sorted(archive.getnames()) == sorted(manifest["source_sha256"])
        archive.extractall(DEST, filter="data")
    assert all(digest(DEST / n) == h for n, h in manifest["source_sha256"].items())
    env = {**os.environ, "UV_PYTHON_DOWNLOADS": "never"}
    with (HERE / "reproduced-install.log").open("w") as output:
        subprocess.run([
            shutil.which("uv"), "sync", "--offline", "--frozen", "--no-editable",
            "--link-mode", "copy", "--cache-dir", str(BASE / "uv-cache"),
        ], cwd=DEST, env=env, stdout=output, stderr=subprocess.STDOUT, check=True)
    restic = DEST / ".cache/restic/0.19.1/restic"
    restic.parent.mkdir(parents=True)
    shutil.copy2(HERE / "work/.cache/restic/0.19.1/restic", restic)
    python = str(DEST / ".venv/bin/python")
    code = """
import json, pathlib, sys
import marketdata.backup, marketdata.checkpoint_clock, marketdata.checkpoint_evidence
print(json.dumps({'python': sys.version, 'executable': sys.executable,
 'imports': {m.__name__: str(pathlib.Path(m.__file__).resolve()) for m in
 (marketdata.backup, marketdata.checkpoint_clock, marketdata.checkpoint_evidence)}}))
"""
    installed = json.loads(subprocess.check_output([python, "-B", "-I", "-c", code], cwd=DEST))
    for name, path in installed["imports"].items():
        assert str(DEST / ".venv") in path
        original = "packages/marketdata/src/" + name.replace(".", "/") + ".py"
        assert digest(Path(path)) == manifest["source_sha256"][original]
    installed.update(release_id=manifest["release_id"], source_files=len(manifest["source_sha256"]),
                     restic_sha256=digest(restic), no_editable=True)
    (HERE / "reproduced-environment.json").write_text(json.dumps(installed, indent=2) + "\n")
    env.update(PYTHONPATH="packages/marketdata/src:packages/marketdata_freqtrade/src:.",
               T05_PREVIOUS_SOURCE=str(BASE / "work"))
    with (HERE / "reproduced-tests.log").open("w") as output:
        result = subprocess.run([
            python, "-B", "-m", "pytest", "-q", "tests/test_retirement_clock.py",
            "tests/test_checkpoint_evidence.py", "tests/test_checkpoint_registration.py",
            "tests/test_clock_retirement.py::test_manual_approval_deletes_only_proven_candidates_and_preserves_restic",
            "--tb=short", "--junitxml=../reproduced-tests.xml",
        ], cwd=DEST, env=env, stdout=output, stderr=subprocess.STDOUT)
    assert all(digest(DEST / n) == h for n, h in manifest["source_sha256"].items())
    (HERE / "reproduction-result.json").write_text(json.dumps({
        "release_id": manifest["release_id"], "passed": result.returncode == 0,
        "source_files_match": True, "operating_changes": False,
    }, indent=2) + "\n")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
