"""Use one isolated variant, including its real source import ordering."""

import sys
from pathlib import Path

here = Path(__file__).resolve().parent
variant = here / "candidate" / sys.argv[1]
sys.path[:0] = [
    str(variant / "packages/marketdata/src"),
    str(variant),
    str(variant / "tests"),
    str(here / "tests"),
]
import pytest

raise SystemExit(pytest.main(sys.argv[2:]))
