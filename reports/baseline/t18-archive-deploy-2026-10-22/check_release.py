"""Inspect the source-only release with the exact retained Python environment."""
# ruff: noqa: E402 -- inspect imports after selecting the fixed source tree

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
plan = json.loads((HERE / "prepared-plan.json").read_bytes())
release = HERE / "runtime"
sys.path[:0] = [str(release / "preserve/packages/marketdata/src"), str(release / "preserve")]
import marketdata

from scripts import archive_backups, archive_output, restic_schedule

spec = importlib.util.spec_from_file_location("new_run_job", release / "policy/run_job.py")
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)
assert wrapper.ROOT == release
assert sys.executable == plan["python"]
modules = {
    m.__name__: str(Path(m.__file__).resolve())
    for m in (marketdata, archive_backups, archive_output, restic_schedule)
}
assert all(path.startswith(str(release)) for path in modules.values())
print(
    json.dumps(
        {
            "python": sys.executable,
            "version": sys.version,
            "wrapper_root": str(wrapper.ROOT),
            "modules": modules,
            "operating_command_invoked": False,
        },
        indent=2,
    )
)
