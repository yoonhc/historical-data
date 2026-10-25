"""Reconstruct frozen source/environment and inspect actual relocated imports."""

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((HERE / "manifest.json").read_bytes())
    assert sha(HERE / "source.tar.gz") == manifest["source_archive_sha256"]
    dest = HERE / "reproduced"
    dest.mkdir(exist_ok=False)
    with tarfile.open(HERE / "source.tar.gz") as archive:
        assert set(archive.getnames()) == set(manifest["source_sha256"])
        archive.extractall(dest, filter="data")
    assert all(sha(dest / n) == digest for n, digest in manifest["source_sha256"].items())
    environment = json.loads(
        (HERE.parent / "t05-completion-evidence-2026-10-11/environment-files.json").read_bytes()
    )
    for name, expected in environment.items():
        source = HERE / "work/.venv" / name
        actual = {"link": os.readlink(source)} if source.is_symlink() else {"sha256": sha(source)}
        assert actual == expected, name
    shutil.copytree(HERE / "work/.venv", dest / ".venv", symlinks=True)
    (HERE / "environment-files.json").write_text(json.dumps(environment, indent=2) + "\n")
    python = dest / ".venv/bin/python"
    help_run = subprocess.run(
        [python, "-B", "-I", dest / "scripts/run_clock_retirement.py", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    code = (
        "import sys,json,pathlib; r=pathlib.Path(sys.argv[1]); "
        'sys.path[:0]=[str(r),str(r/"packages/marketdata/src")]; '
        "from scripts import clock_retirement,retire_checkpoints,checkpoint_code; "
        "import marketdata.checkpoint_evidence; "
        'print(json.dumps({"executable":sys.executable,"prefix":sys.prefix,"code":checkpoint_code.current_code(),'
        '"imports":{m.__name__:m.__file__ for m in [clock_retirement,retire_checkpoints,marketdata.checkpoint_evidence]}}))'
    )
    imported = json.loads(
        subprocess.check_output([python, "-B", "-I", "-c", code, dest], text=True)
    )
    assert Path(imported["prefix"]) == dest / ".venv"
    assert all(Path(p).is_relative_to(dest) for p in imported["imports"].values())
    assert imported["code"]["code_root"] == str(dest)
    for path in imported["imports"].values():
        relative = Path(path).relative_to(dest).as_posix()
        assert sha(Path(path)) == manifest["source_sha256"][relative]
    result = {
        "passed": True,
        "release_id": manifest["release_id"],
        "source_files": len(manifest["source_sha256"]),
        "environment_files": len(environment),
        "help_exit_code": help_run.returncode,
        "installed_imports": imported,
        "operating_writes": False,
        "scope": "independent archive reconstruction and relocated launcher; no repeated whole-engine test",
    }
    (HERE / "reproduction.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "installed_imports"}, indent=2))


if __name__ == "__main__":
    main()
