"""Run from this frozen release, independently of editable marketdata imports."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packages/marketdata/src")]

from scripts.checkpoint_schedule import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
