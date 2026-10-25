"""Run two bounded public collectors together and retain process/collection telemetry."""

import argparse
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from marketdata.domain import json_bytes, now, sha256, utc
from marketdata.store import atomic_write


def process_metrics(pid):
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        memory = {
            line.split(":", 1)[0]: int(line.split()[1]) * 1024
            for line in status.splitlines()
            if line.startswith(("VmRSS:", "VmHWM:"))
        }
        return {**memory, "cpu_seconds": (int(stat[11]) + int(stat[12])) / os.sysconf("SC_CLK_TCK")}
    except (FileNotFoundError, ProcessLookupError):
        return None


def collection_metrics(root, started):
    db = sqlite3.connect(f"file:{root / 'state/catalog.sqlite'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    result = {}
    try:
        for dataset in ("agg_trades", "book_depth"):
            row = db.execute(
                "SELECT * FROM event_collections WHERE dataset=? AND kind='websocket' "
                "AND started_at>=? ORDER BY started_at DESC LIMIT 1",
                (dataset, started.isoformat()),
            ).fetchone()
            if row is None:
                result[dataset] = None
                continue
            report = json.loads(row["report"])
            received = report.get("last_received_at")
            result[dataset] = {
                "id": row["id"],
                "state": row["state"],
                **{
                    key: report.get(key)
                    for key in (
                        "messages",
                        "persisted_messages",
                        "connections",
                        "connection_errors",
                        "sync_losses",
                        "unrepaired_gaps",
                        "repaired_gaps",
                        "synchronized",
                    )
                },
                "receive_age_seconds": (now() - utc(received)).total_seconds()
                if received
                else None,
            }
        return result
    finally:
        db.close()


def run(root, output, seconds=3600, rotate_seconds=900, interval=30):
    if not 60 <= seconds <= 3600 or not 60 <= rotate_seconds <= 3600 or not 5 <= interval <= 60:
        raise ValueError("Soak seconds/rotation: 60..3600; sample interval: 5..60")
    root, output = root.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = now()
    common = [sys.executable, "-m", "marketdata.cli", "--root", str(root)]
    commands = {
        "aggtrade": [
            *common,
            "aggtrade-stream",
            "--fresh-start",
            "--seconds",
            str(seconds),
            "--rotate-seconds",
            str(rotate_seconds),
            "--report",
            str(output / "aggtrade.json"),
        ],
        "depth": [
            *common,
            "depth-stream",
            "--seconds",
            str(seconds),
            "--rotate-seconds",
            str(rotate_seconds),
            "--verify-seconds",
            "30",
            "--report",
            str(output / "depth.json"),
        ],
    }
    source_paths = [
        "packages/marketdata/src/marketdata/aggtrade_stream.py",
        "packages/marketdata/src/marketdata/aggtrades.py",
        "packages/marketdata/src/marketdata/depth.py",
        "packages/marketdata/src/marketdata/depth_stream.py",
        "packages/marketdata/src/marketdata/cli.py",
        "scripts/run_stream_soak.py",
    ]
    report = {
        "started_at": started,
        "seconds_requested": seconds,
        "rotate_seconds": rotate_seconds,
        "sample_interval": interval,
        "root": str(root),
        "commands": commands,
        "state": "running",
        "source_hashes": {p: sha256((root / p).read_bytes()) for p in source_paths},
        "samples": 0,
    }
    children, handles = {}, []

    def operations(label):
        with (output / f"operations-{label}.stdout").open("wb") as stdout:
            result = subprocess.run(
                [*common, "status", "--report", str(output / f"operations-{label}.json")],
                stdout=stdout,
                stderr=subprocess.STDOUT,
                timeout=45,
            )
        return {"at": now(), "label": label, "exit_code": result.returncode}

    try:
        report["operations_before"] = operations("before")
        for name, command in commands.items():
            handle = (output / f"{name}.log").open("wb")
            handles.append(handle)
            children[name] = subprocess.Popen(
                command, cwd=root, stdout=handle, stderr=subprocess.STDOUT
            )
        report["pids"] = {name: child.pid for name, child in children.items()}
        atomic_write(output / "run.json", json_bytes(report))
        print(
            json_bytes({"output": str(output), **report["pids"], "started_at": started}).decode(),
            flush=True,
        )
        began = last_operations = time.monotonic()
        with (output / "telemetry.jsonl").open("a") as telemetry:
            while True:
                sample = {
                    "at": now(),
                    "elapsed_seconds": time.monotonic() - began,
                    "processes": {
                        name: {
                            "pid": p.pid,
                            "exit_code": p.poll(),
                            "metrics": process_metrics(p.pid),
                        }
                        for name, p in children.items()
                    },
                    "collections": collection_metrics(root, started),
                    "free_bytes": shutil.disk_usage(root).free,
                }
                if time.monotonic() - last_operations >= 300:
                    sample["operations"] = operations(f"{report['samples']:04d}")
                    last_operations = time.monotonic()
                telemetry.write(json_bytes(sample).decode() + "\n")
                telemetry.flush()
                report["samples"] += 1
                atomic_write(output / "latest.json", json_bytes(sample))
                if all(p.poll() is not None for p in children.values()):
                    break
                if time.monotonic() - began > seconds + 120:
                    raise TimeoutError("Collector exceeded bounded soak deadline")
                time.sleep(interval)
        report["exit_codes"] = {name: p.returncode for name, p in children.items()}
        report["operations_after"] = operations("after")
        report["state"] = (
            "complete"
            if all(code == 0 for code in report["exit_codes"].values())
            else "needs_review"
        )
    except BaseException as exc:
        report.update(
            state="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        for child in children.values():
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
        for child in children.values():
            if child.poll() is None:
                try:
                    child.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for handle in handles:
            handle.close()
        report["finished_at"] = now()
        atomic_write(output / "run.json", json_bytes(report))
    print(
        json_bytes(
            {
                "state": report["state"],
                "samples": report["samples"],
                "exit_codes": report["exit_codes"],
            }
        ).decode(),
        flush=True,
    )
    return 0 if report["state"] == "complete" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=3600)
    parser.add_argument("--rotate-seconds", type=int, default=900)
    parser.add_argument("--sample-interval", type=int, default=30)
    args = parser.parse_args()
    return run(args.root, args.output, args.seconds, args.rotate_seconds, args.sample_interval)


if __name__ == "__main__":
    raise SystemExit(main())
