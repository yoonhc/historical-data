"""Isolated child used only by the abrupt-process-exit regression test."""

import os
from pathlib import Path
import signal
import sys

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "candidate/base/packages/marketdata/src")]
from marketdata import job_clock as jc
from test_clock_decisions import Samples, provider, load_runner

store, root = map(Path, sys.argv[1:])
runner = load_runner()
samples = Samples()
runner.query_factory = lambda _: lambda: provider(samples)
original = jc.Context
close = original.close


def killed_after_payload(context):
    close(context)
    os.kill(os.getpid(), signal.SIGKILL)


original.close = killed_after_payload
jc.Context = lambda *a, **kw: original(*a, sampler=samples, monitor_interval=0.01, **kw)
sys.argv = [
    "run_job",
    "--job",
    "oi-quality",
    "--binding",
    str(root / "binding.json"),
    "--state-root",
    str(root / "requests"),
    "--run",
    "--",
    "--root",
    str(store),
    "oi-quality",
    "--days",
    "7",
]
runner.main()
raise RuntimeError("Child did not terminate at the requested boundary")
