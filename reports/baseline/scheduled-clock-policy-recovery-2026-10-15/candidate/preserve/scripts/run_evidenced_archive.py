"""Fixed source entrypoint; preserve the existing scheduled archiver contract."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "packages/marketdata/src")]

from scripts.restic_schedule import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
