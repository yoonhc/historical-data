"""Run the changed helper and existing real-restic connections in isolation."""
# ruff: noqa: E402 -- select the actual installed modules before importing them.

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREVIOUS = HERE.parent / "t18-capacity-recovery-2026-10-19"
PRESERVE = Path("/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11/preserve")
sys.path[:0] = [str(PRESERVE / "packages/marketdata/src"), str(PRESERVE),
                str(ROOT / "tests"), str(PRESERVE / "tests")]
import scripts

scripts.__path__ = [str(HERE / "candidate"), *scripts.__path__]
import marketdata

from scripts import archive_backups, archive_output

assert str(PRESERVE) in marketdata.__file__
assert str(HERE / "candidate") in archive_backups.__file__
assert str(HERE / "candidate") in archive_output.__file__
import pytest

raise SystemExit(pytest.main([
    "-q", "-p", "no:cacheprovider",
    str(ROOT / "tests/test_archive_output.py"),
    str(ROOT / "tests/test_restic_archive.py") +
    "::test_repeated_noop_still_dumps_each_manifest_without_duplicate_body_allocation",
    str(PREVIOUS / "test_installed_candidate.py"),
    "--junitxml=" + str(HERE / "installed-candidate-tests.xml"),
]))
