"""Source identity for a reviewed checkpoint retirement plan.

This covers project sources and dependency declarations, not arbitrary external
process memory. Run retirement in a fresh interpreter and keep the release fixed
while applying a plan; existing checkpoint readers still need their own audit.
"""

import os
import subprocess
import sys
from pathlib import Path

from marketdata.domain import DataError, json_bytes, sha256

CODE_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_FILES = (
    "scripts/checkpoint_code.py",
    "scripts/retire_checkpoints.py",
    "scripts/archive_backups.py",
    "scripts/restic_trial.py",
    "scripts/checkpoint_schedule.py",
    "scripts/run_checkpoint_schedule.py",
    "scripts/restic_schedule.py",
    "scripts/clock_retirement.py",
    "scripts/retirement_clock.py",
    "scripts/run_clock_retirement.py",
    "scripts/render_retirement_units.py",
    "scripts/checkpoint_evidence.py",
    "scripts/run_evidenced_backup.py",
    "scripts/run_evidenced_archive.py",
    "integrations/systemd/marketdata-checkpoint-retirement.service",
    "integrations/systemd/marketdata-checkpoint-retirement.timer",
)
DECLARATIONS = ("pyproject.toml", "packages/marketdata/pyproject.toml", "uv.lock")


def describe_source(root=CODE_ROOT):
    """Hash all marketdata Python sources, the retirement helpers and lockfile."""
    root = Path(root).resolve()
    paths = sorted(
        {root / name for name in SCRIPT_FILES + DECLARATIONS}
        | set((root / "packages/marketdata/src/marketdata").rglob("*.py"))
    )
    files = {}
    for path in paths:
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise DataError(f"Symlink in retirement code: {path}")
        files[path.relative_to(root).as_posix()] = sha256(path.read_bytes())
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(root), *args], env=env, capture_output=True, timeout=15
        )

    # An exported tree can still have an exact source digest; don't invent a commit.
    commit, dirty = None, None
    try:
        top, head = git("rev-parse", "--show-toplevel"), git("rev-parse", "HEAD")
        if top.returncode == head.returncode == 0 and Path(top.stdout.decode().strip()) == root:
            commit = head.stdout.decode().strip()
            tracked = git("ls-files", "-z", "--", *files)
            if tracked.returncode != 0:
                raise DataError("Cannot inspect retirement source tracking")
            names = {p.decode() for p in tracked.stdout.split(b"\0") if p}
            diff = git("diff", "--quiet", "HEAD", "--", *files)
            if diff.returncode not in (0, 1):
                raise DataError("Cannot inspect retirement source changes")
            dirty = names != set(files) or diff.returncode == 1
    except FileNotFoundError:
        pass
    result = {
        "format": 1,
        "code_root": str(root),
        "git_commit": commit,
        "source_dirty": dirty,
        "source_files": files,
        "source_sha256": sha256(json_bytes(files)),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": sys.version,
    }
    result["code_id"] = sha256(json_bytes(result))
    return result


STARTUP_CODE = describe_source()


def current_code():
    result = describe_source()
    if result != STARTUP_CODE:
        raise DataError("Retirement code changed since import; start a fresh Python process")
    return result
