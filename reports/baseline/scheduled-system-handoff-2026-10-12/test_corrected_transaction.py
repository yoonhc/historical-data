"""Reuse the existing tests on the corrected configuration plan, not extra cases."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.with_name('scheduled-system-migration-2026-10-12')))
import test_migration
test_migration.PLAN = json.loads((HERE / 'configuration-plan.json').read_bytes())
from test_migration import *  # noqa: F403
