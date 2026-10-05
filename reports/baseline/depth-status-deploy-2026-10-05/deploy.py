"""Deploy the approved fixed release, stopping and starting only the depth unit."""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
BUNDLE = ROOT / "reports/baseline/depth-status-release-2026-10-05"
UNIT = "marketdata-depth.service"
UNITS = [
    UNIT,
    "marketdata-backup.service",
    "marketdata-pack.service",
    "marketdata-restic-archive.service",
    "marketdata-daily.service",
    "marketdata-aggtrade.service",
    "marketdata-oi.service",
]
PROPERTIES = (
    "Id,ActiveState,SubState,MainPID,ExecMainStartTimestamp,ExecMainExitTimestamp,"
    "ExecMainCode,ExecMainStatus,Result,NRestarts,ExecStart,WorkingDirectory,"
    "KillSignal,TimeoutStopUSec,Restart,RestartUSec,FragmentPath,DropInPaths"
)


def stamp():
    return datetime.now().astimezone().isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    path = OUT / name
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temp.replace(path)


def command(argv, timeout=30, check=True, env=None):
    return subprocess.run(
        argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout, check=check
    )


def units():
    result = command(["systemctl", "--user", "show", *UNITS, "--property=" + PROPERTIES])
    result = [
        dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        for block in result.stdout.strip().split("\n\n")
    ]
    return {block["Id"]: block for block in result}


def collection(identity=None):
    with sqlite3.connect((ROOT / "state/catalog.sqlite").as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT state,report FROM event_collections WHERE dataset='book_depth' "
            + ("AND id=?" if identity else "ORDER BY started_at DESC LIMIT 1"),
            (identity,) if identity else (),
        ).fetchone()
    assert row is not None
    return {"state": row[0], "report": json.loads(row[1])}


def files_match(release, kind):
    for item in release["changed_files"]:
        path, expected = ROOT / item["path"], item[kind + "_sha256"]
        assert (sha(path) == expected) if expected else not path.exists(), item["path"]


def process(pid):
    root = Path("/proc") / str(pid)
    environment = dict(
        item.split("=", 1)
        for item in root.joinpath("environ").read_bytes().decode().split("\0")
        if "=" in item
    )
    return {
        "pid": pid,
        "cmdline": root.joinpath("cmdline").read_bytes().decode().split("\0"),
        "cwd": str(root.joinpath("cwd").resolve()),
        "python_environment": {
            k: environment[k]
            for k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "SESSION_SECONDS")
            if k in environment
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=900)
    args = parser.parse_args()
    release = json.loads((BUNDLE / "release.json").read_bytes())
    setup = json.loads((OUT / "setup.json").read_bytes())
    assert release["release_id"] == "depth-status-20261005-7be8e3da868f9308"
    assert sha(BUNDLE / "release.json") == setup["release_manifest_sha256"]
    assert sha(BUNDLE / "candidate.patch") == release["patch_sha256"]
    assert sha(BUNDLE / "baseline.tar.gz") == release["baseline_sha256"]
    assert json.loads((BUNDLE / "reproduction.json").read_bytes())["state"] == "passed"
    files_match(release, "before")
    command(["git", "apply", "--check", str(BUNDLE / "candidate.patch")])
    if not args.apply:
        print(
            json.dumps({"state": "preflight_passed", "release_id": release["release_id"]}),
            flush=True,
        )
        return
    assert not (OUT / "deployment.json").exists(), "Do not repeat an installation silently"
    record = {
        "release_id": release["release_id"],
        "started_at": stamp(),
        "state": "waiting",
        "steps": [],
        "service_mutations": [],
    }
    save("deployment.json", record)
    deadline = time.monotonic() + args.wait_seconds
    stopped = applied = False

    def step(phase, **values):
        record["steps"].append({"at": stamp(), "phase": phase, **values})
        record["state"] = phase
        save("deployment.json", record)
        print(json.dumps(record["steps"][-1]), flush=True)

    def control(action):
        record["service_mutations"].append({"at": stamp(), "action": action, "unit": UNIT})
        save("deployment.json", record)
        value = command(["systemctl", "--user", action, UNIT], timeout=130)
        return {"exit_code": value.returncode, "stdout": value.stdout, "stderr": value.stderr}

    try:
        while True:
            current = units()
            busy = [
                name
                for name in UNITS[1:5]
                if current[name]["ActiveState"] in {"active", "activating", "deactivating"}
            ]
            if not busy:
                break
            step("waiting", busy=busy)
            assert time.monotonic() < deadline, "Maintenance wait deadline reached; no changes made"
            time.sleep(20)
        save("services-before.json", current)
        running = current[UNIT]
        assert running["ActiveState"] == "active" and running["SubState"] == "running"
        assert running["WorkingDirectory"] == str(ROOT) and not running["DropInPaths"]
        assert running["KillSignal"] == "2" and running["TimeoutStopUSec"] == "2min"
        assert running["Restart"] == "always" and running["RestartUSec"] == "15s"
        installed = Path(running["FragmentPath"])
        assert sha(installed) == setup["protected_sha256"][str(installed)]
        before_process = process(int(running["MainPID"]))
        assert before_process["cwd"] == str(ROOT)
        assert not any(
            k in before_process["python_environment"] for k in ("PYTHONPATH", "PYTHONHOME")
        )
        assert before_process["python_environment"]["SESSION_SECONDS"] == "3600"
        save("process-before.json", before_process)
        before = collection()
        assert before["state"] == "running"
        save("collection-before.json", before)
        files_match(release, "before")
        step("stopping", old_pid=running["MainPID"], old_collection_id=before["report"]["id"])
        result = control("stop")
        stopped = True
        stopped_state = units()
        save("services-stopped.json", stopped_state)
        unit = stopped_state[UNIT]
        assert unit["MainPID"] == "0" and unit["ActiveState"] in {"inactive", "failed"}
        assert unit["ExecMainCode"] == "1" and unit["ExecMainStatus"] == "130"
        closed = collection(before["report"]["id"])
        save("collection-stopped.json", closed)
        r = closed["report"]
        assert closed["state"] == "interrupted" and not r["complete"] and not r["synchronized"]
        assert r["messages"] == r["persisted_messages"] and not r.get("flush_error")
        assert "KeyboardInterrupt" in r.get("error", "")
        step("stopped", **result, persisted_messages=r["persisted_messages"])
        files_match(release, "before")
        command(["git", "apply", "--check", str(BUNDLE / "candidate.patch")])
        command(["git", "apply", str(BUNDLE / "candidate.patch")])
        applied = True
        files_match(release, "after")
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        origins = json.loads(
            command(
                [
                    str(ROOT / ".venv/bin/python"),
                    "-c",
                    "import json; from marketdata import depth,depth_stream,depth_refresh; "
                    "print(json.dumps({m.__name__:m.__file__ for m in (depth,depth_stream,depth_refresh)}))",
                ],
                env=env,
            ).stdout
        )
        assert all(Path(p).resolve().is_relative_to(ROOT / "packages") for p in origins.values())
        save("import-origins.json", origins)
        step("applied", files=release["changed_files"])
        result = control("start")
        time.sleep(3)
        started = units()
        save("services-started.json", started)
        new_pid = int(started[UNIT]["MainPID"])
        assert started[UNIT]["ActiveState"] == "active" and new_pid not in (
            0,
            int(running["MainPID"]),
        )
        save("process-started.json", process(new_pid))
        files_match(release, "after")
        step("started", **result, new_pid=new_pid)
        stopped = False
        record["finished_at"] = stamp()
        save("deployment.json", record)
    except BaseException as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        if stopped:
            try:
                control("stop")
                if applied:
                    files_match(release, "after")
                    command(
                        ["git", "apply", "--reverse", "--check", str(BUNDLE / "candidate.patch")]
                    )
                    command(["git", "apply", "--reverse", str(BUNDLE / "candidate.patch")])
                files_match(release, "before")
                control("start")
                record["rollback"] = "baseline_restored_and_start_requested"
            except BaseException as rollback_error:
                record["rollback_error"] = f"{type(rollback_error).__name__}: {rollback_error}"
        record.update(state="aborted", finished_at=stamp())
        save("deployment.json", record)
        raise


if __name__ == "__main__":
    main()
