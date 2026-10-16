"""Fresh process for real kill boundaries; only an isolated /tmp request."""

import json
import os
from pathlib import Path
import signal
import sys
import tempfile

here = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(here),
    str(here.with_name("scheduled-clock-policy-storage-2026-10-16") / "tests"),
]
import recover_request as r

plan = json.loads(Path(sys.argv[1]).read_bytes())
assert Path(plan["request_path"]).is_relative_to(Path(tempfile.gettempdir()))
wrapper, contract = r.modules(plan)
from marketdata import job_clock as jc
from test_clock_decisions import Samples, provider


def monitor(emit, guard):
    sample = Samples("2026-10-16T14:30:00Z")
    context = jc.Context(lambda: provider(sample), emit, sampler=sample, scope_guard=guard)
    try:
        context.start()
        context.point("explicit_stored_result_reconciliation")
    finally:
        stopped = context.close()
    return {**stopped, "ok": True}


def fault(point):
    if point == sys.argv[2]:
        os.kill(os.getpid(), signal.SIGKILL)


r.recover(
    plan,
    contract,
    lambda: None,
    monitor,
    fault=fault,
    approval={
        "enabled": True,
        "recovery_plan_id": plan["plan_id"],
        "host_plan_id": "isolated",
        "reference": "isolated-approval",
    },
)
