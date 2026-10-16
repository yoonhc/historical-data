"""Run only isolated repair/handoff tests; no operating mutations."""

import sys
from pathlib import Path

here = Path(__file__).resolve().parent
prior = here.with_name("scheduled-clock-policy-storage-2026-10-16")
sys.path[:0] = [
    str(here),
    str(here / "tests"),
    str(prior / "candidate/base/packages/marketdata/src"),
    str(prior / "candidate/base"),
    str(prior / "tests"),
]
import pytest

raise SystemExit(pytest.main(sys.argv[1:]))
