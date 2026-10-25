"""Isolated child used only by the abrupt-process-exit regression test."""

import os
import signal
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "packages/marketdata/src"), str(HERE.parent)]
from marketdata import job_clock as jc  # noqa: E402
from test_clock_decisions import Samples, load_runner, provider  # noqa: E402

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
