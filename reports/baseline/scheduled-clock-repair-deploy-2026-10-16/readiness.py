"""Read-only readiness adapter; distinguishes explicit clock deferral from faults."""
import importlib.util
import json
from pathlib import Path
import sys


def query(release, binding):
    root = Path(release)
    sys.path[:0] = [str(root / "base/packages/marketdata/src"), str(root / "base")]
    spec = importlib.util.spec_from_file_location("t16_readonly_entry", root / "policy/run_job.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from marketdata.job_clock import ClockDeferred
    sys.argv = [str(root / "policy/run_job.py"), "--job", "oi", "--binding", str(binding), "--check"]
    return evaluate(module.main, ClockDeferred)


def evaluate(main, deferred):
    try:
        return main()
    except deferred as exc:
        print(json.dumps({"ready": False, "deferred": str(exc)}))
        return 75


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("readiness.py RELEASE BINDING")
    raise SystemExit(query(sys.argv[1], sys.argv[2]))
