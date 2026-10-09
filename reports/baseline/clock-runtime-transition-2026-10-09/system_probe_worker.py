"""Isolated service child/provider fixtures around the unchanged candidate runtime."""

import argparse
import importlib.util
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path


def atomic(path, obj):
    blob = (json.dumps(obj, sort_keys=True) + "\n").encode()
    tmp = path.with_suffix(".pending")
    with tmp.open("wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--scenario", choices=["graceful", "kill"], required=True)
    ap.add_argument("--child", action="store_true")
    a = ap.parse_args()
    state = a.root / a.scenario
    control = state / "control"
    control.mkdir(exist_ok=True)
    from marketdata import depth
    from marketdata.store import Store

    if a.child:
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        store = Store(state / "store")
        report = depth.start_collection(store, {"complete": False, "fixture": True})
        index = len(list(control.glob("child-*-started.json"))) + 1
        prefix = control / f"child-{index}"
        atomic(
            prefix.with_name(prefix.name + "-started.json"),
            {
                "pid": os.getpid(),
                "id": report["id"],
                "time": time.monotonic(),
                "context": json.loads(os.environ["MARKETDATA_CLOCK_CONTEXT"]),
            },
        )
        if a.scenario == "graceful" and index == 1:
            stop.wait(1.2)
        else:
            stop.wait(240)
        # Represent final persistence with actual catalog/report writes/fsync.
        depth.save_collection(store, report, "partial")
        store.close()
        atomic(
            prefix.with_name(prefix.name + "-saved.json"),
            {"pid": os.getpid(), "id": report["id"], "time": time.monotonic()},
        )
        return 2
    release = Path(sys.prefix).parent
    spec = importlib.util.spec_from_file_location(
        "capture", release / "integrations/runtime/capture_runtime.py"
    )
    cr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cr)
    ce = importlib.import_module("marketdata.clock_evidence")
    p = json.loads((a.root / "profile.json").read_text())
    p.update(
        data_root=str(state / "store"),
        evidence_root=str(state / "evidence"),
        release_root=str(release),
    )
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    def environment(_):
        return cr.rt.environment_check(p)

    def checker(_):
        at = cr.rt.stamp()
        track = cr.rt.tracking(
            f"CB00710F,203.0.113.15,3,{at['wall_ns'] / 1e9 - 1},0.001,0,0,1,0,0.2,0.01,0.02,64,Normal"
        )
        return {
            **cr.rt.assess_clock(track, p["policy"], at["wall_ns"] / 1e9),
            "provider": {"pid": 42, "invocation_id": "isolated-fixture"},
            "sample": at,
        }

    original = cr.publish_chunk

    def publishing(store, path):
        if (control / "hold-publication").exists() and not (
            control / "release-publication"
        ).exists():
            atomic(
                control / "publisher-waiting.json", {"pid": os.getpid(), "time": time.monotonic()}
            )
            end = time.monotonic() + 75
            while not (control / "release-publication").exists():
                if time.monotonic() > end:
                    raise RuntimeError("Isolated driver failed to release publication barrier")
                time.sleep(0.02)
        return original(store, path)

    cr.publish_chunk = publishing
    cr.rt.collector_command = lambda *_: [
        sys.executable,
        "-B",
        "-I",
        str(Path(__file__)),
        "--root",
        str(a.root),
        "--scenario",
        a.scenario,
        "--child",
    ]
    invocation = os.environ.get("INVOCATION_ID", "missing")
    atomic(
        control / f"supervisor-{invocation}-started.json",
        {
            "pid": os.getpid(),
            "time": time.monotonic(),
            "prefix": sys.prefix,
            "imports": ce.__file__,
        },
    )
    e = cr.Evidence(
        p, "depth", journal_factory=lambda *args, **kw: ce.Journal(*args, **kw, min_free_bytes=0)
    )
    try:
        result = cr.supervise(
            p,
            "depth",
            e,
            stop,
            {"ready": True, "phase": "closed", "fixture": True},
            checker=checker,
            env_check=environment,
        )
    finally:
        e.close()
    atomic(
        control / f"supervisor-{invocation}-closed.json",
        {
            "pid": os.getpid(),
            "time": time.monotonic(),
            "worker_alive": e.publisher.thread.is_alive(),
            "pending": e.publisher.queue.unfinished_tasks,
            "open_spool": [q.name for q in e.journal.root.glob("*")],
            "result": result,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
