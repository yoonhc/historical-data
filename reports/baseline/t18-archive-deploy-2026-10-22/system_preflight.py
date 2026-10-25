"""Only nonce-scoped trial units/files; never deploy or resume operating timers.

The after test overlays proposed files privately inside its mount namespace.
Operating files, clock control, receipts and ledgers are read-only throughout.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("preflight_common", HERE / "common.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def command(argv, check=True):
    done = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=45)
    c.require(
        not check or done.returncode == 0,
        "Command failed: " + repr(argv) + ": " + done.stderr[-1500:],
    )
    return done


def checked():
    seal = json.loads((HERE / "preflight-seal.json").read_bytes())
    for name, digest in seal["files"].items():
        c.require(c.file_sha(HERE / name) == digest, "Preflight input changed: " + name)
    plan = json.loads((HERE / "prepared-plan.json").read_bytes())
    c.require(
        plan["preparation_id"]
        == c.sha(c.canonical({k: v for k, v in plan.items() if k != "preparation_id"})),
        "Preparation digest differs",
    )
    c.require(
        set(plan["changes"]) == {c.SERVICE_PATH, c.TOPOLOGY, c.PROFILE, c.BINDING, c.T05_BINDING},
        "Unexpected proposed configuration paths",
    )
    for name in plan["release_entries"]:
        c.require(
            not Path(name).is_absolute() and ".." not in Path(name).parts, "Unsafe release entry"
        )
    c.require(
        Path("/proc/sys/kernel/random/boot_id").read_text().strip() == plan["boot_id"],
        "Boot changed",
    )
    for path, value in plan["external_runtime"].items():
        c.require(c.node(path) == value, "External interpreter/dependency changed: " + path)
    return plan, seal


def show(*units):
    done = command(
        [
            "/usr/bin/systemctl",
            "show",
            *units,
            "--property=Id,LoadState,ActiveState,SubState,MainPID,ControlPID,InvocationID,Job,DropInPaths,ProtectClock,ProtectSystem,ProtectHome,ReadWritePaths,ReadOnlyPaths,BindReadOnlyPaths,User,UMask",
        ],
        check=False,
    )
    rows = c.parse_show(done.stdout)
    c.require(set(rows) == set(units), "Missing manager properties")
    c.require(
        done.returncode == 0 or all(r["LoadState"] == "not-found" for r in rows.values()),
        "Cannot query manager",
    )
    return rows


def snapshot(plan):
    values = {}
    selected = {
        **plan["protected"],
        **{
            p: {k: v for k, v in n["before"].items() if k != "text"}
            for p, n in plan["changes"].items()
        },
        c.GRANT: {k: v for k, v in plan["recurring_before"].items() if k != "text"},
    }
    for path, expected in selected.items():
        c.require(c.node(path) == expected, "Operating input changed: " + path)
        if expected["kind"] != "absent":
            st = Path(path).lstat()
            expected_owner = plan["expected_host_uids"][path]
            c.require(st.st_uid == expected_owner, "Host ownership differs: " + path)
            values[path] = {**expected, "uid": st.st_uid, "gid": st.st_gid}
    ledger = Path(c.LEDGER).read_bytes()
    c.require(json.loads(ledger) == plan["ledger_before"], "Boot ledger changed")
    units = show(*c.PROCESS_UNITS, c.SERVICE, c.TIMER, c.T05_TIMER)
    for name in (c.SERVICE, c.TIMER):
        row = units[name]
        c.require(
            row["ActiveState"] == "inactive"
            and not row.get("Job")
            and row.get("MainPID", "0") == "0"
            and row.get("ControlPID", "0") == "0",
            "Archiver is not held and idle",
        )
    return {"files": values, "ledger_sha256": c.sha(ledger), "units": units}


def write_new(path, raw, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fchmod(f.fileno(), mode)
        os.fsync(f.fileno())
    c.sync_dir(path.parent)


def install_trial(plan, seal):
    nonce = uuid.uuid4().hex[:16]
    trial = Path("/opt") / ("md-t18-" + nonce)
    c.require(not trial.exists(), "Trial collision")
    trial.mkdir(mode=0o755)
    try:
        for name, node in plan["release_entries"].items():
            raw = Path(node["source"]).read_bytes()
            c.require(c.sha(raw) == node["sha256"], "Candidate changed: " + name)
            write_new(trial / "runtime" / name, raw, node["mode"])
        for name in (*seal["files"], "preflight-seal.json"):
            write_new(trial / "tools" / name, (HERE / name).read_bytes())
        write_new(
            trial / "tools" / "destination.json",
            c.encode({"report_directory": str(HERE), "nonce": nonce}),
        )
        for path in (trial, *trial.rglob("*")):
            c.require(
                path.lstat().st_uid == 0
                and not path.is_symlink()
                and not path.stat().st_mode & 0o022,
                "Unsealed trial",
            )
    except BaseException:
        shutil.rmtree(trial)
        raise
    os.execv(
        "/usr/bin/python3",
        ["/usr/bin/python3", "-B", "-I", str(trial / "tools/system_preflight.py"), "--sealed-run"],
    )


def unit(template, trial, state, mode, service, timer, plan):
    lines = []
    for line in template.splitlines():
        if line.startswith(("Requires=", "After=", "ExecStart=")):
            continue
        lines.append(line)
    lines += [
        "[Service]",
        f"ExecStart={plan['python']} -B -I {trial}/tools/trial_worker.py --release {trial}/runtime --state {state} --mode {mode} --service {service} --timer {timer}",
        f"ReadOnlyPaths={c.ROOT} /etc/marketdata /var/lib/marketdata-clock /var/lib/marketdata-checkpoint-retirement /mnt/backup",
        f"ReadWritePaths={state}",
        "TimeoutStartSec=90s",
    ]
    if mode == "after":
        for i, (path, node) in enumerate(plan["changes"].items()):
            shadow = trial / "shadow" / str(i)
            write_new(shadow, node["after"]["text"].encode())
            lines.append(f"BindReadOnlyPaths={shadow}:{path}")
        shadow = trial / "shadow/grant"
        write_new(shadow, c.encode(plan["recurring_proposal_after"]))
        lines.append(f"BindReadOnlyPaths={shadow}:{c.GRANT}")
        shadow = trial / "shadow/ledger"
        ledger = {
            **plan["ledger_before"],
            "profile_sha256": c.sha(
                json.dumps(
                    json.loads(plan["changes"][c.PROFILE]["after"]["text"]), sort_keys=True
                ).encode()
            ),
        }
        write_new(shadow, c.encode(ledger))
        lines.append(f"BindReadOnlyPaths={shadow}:{c.LEDGER}")
    return "\n".join(lines) + "\n"


def sealed_run(plan):
    trial = HERE.parent
    c.require(
        trial.parent == Path("/opt") and re.fullmatch(r"md-t18-[0-9a-f]{16}", trial.name),
        "Unexpected sealed trial path",
    )
    dest = json.loads((HERE / "destination.json").read_bytes())
    nonce = dest["nonce"]
    c.require(trial.name == "md-t18-" + nonce, "Nonce differs")
    state = c.ROOT / "reports" / ("t18-output-trial-" + nonce)
    names = [
        trial.name + "-before.service",
        trial.name + "-after.service",
        trial.name + "-before.timer",
    ]
    created = []
    report = {
        "passed": False,
        "preparation_id": plan["preparation_id"],
        "started_at": datetime.now(UTC).isoformat(),
        "operating_applied": False,
        "timer_resumed": False,
        "checkpoint_deletion": False,
        "clock_control": False,
        "cleanup": {"errors": []},
    }
    before = None
    try:
        before = snapshot(plan)
        report["before"] = before
        c.require(not state.exists() and not state.is_symlink(), "Trial state collision")
        state.mkdir(mode=0o700)
        os.chown(state, 1000, 1000)
        template = plan["changes"][c.SERVICE_PATH]["before"]["text"]
        print(
            "checking sealed source imports, actual timer and before/after private namespaces",
            flush=True,
        )
        for mode, name in zip(("before", "after"), names):
            target = Path("/run/systemd/system") / name
            write_new(target, unit(template, trial, state, mode, name, names[2], plan).encode())
            created.append(target)
        timer = Path("/run/systemd/system") / names[2]
        write_new(
            timer,
            f"[Unit]\nDescription=T18 disposable output trial\n[Timer]\nOnActiveSec=1s\nAccuracySec=100ms\nPersistent=false\nUnit={names[0]}\n".encode(),
        )
        created.append(timer)
        command(["/usr/bin/systemd-analyze", "verify", *[str(p) for p in created]])
        command(["/usr/bin/systemctl", "daemon-reload"])
        for mode, start in (("before", names[2]), ("after", names[1])):
            command(["/usr/bin/systemctl", "start", "--no-block", start])
            deadline = time.monotonic() + 100
            result_path = state / (mode + ".json")
            name = names[0 if mode == "before" else 1]
            while True:
                row = show(name)[name]
                if (
                    result_path.exists()
                    and row["ActiveState"] == "inactive"
                    and row.get("MainPID", "0") == "0"
                ):
                    break
                if row["ActiveState"] == "failed":
                    raise RuntimeError(
                        "Trial service failed: "
                        + name
                        + "; "
                        + command(
                            ["/usr/bin/journalctl", "-u", name, "--no-pager", "-n", "30"],
                            check=False,
                        ).stdout[-5000:]
                    )
                c.require(time.monotonic() < deadline, "Trial timeout")
                time.sleep(0.25)
            value = json.loads(result_path.read_bytes())
            c.require(
                value["passed"] and value["context"]["service_verified"],
                "Trial did not verify invocation",
            )
            c.require(
                value["context"]["scheduled"] == (mode == "before"),
                "Timer/manual attribution differs",
            )
            report[mode + "_trial"] = value
            report[mode + "_effective"] = row
        report["passed"] = True
    except Exception as exc:
        report.update(error=type(exc).__name__ + ": " + str(exc), traceback=traceback.format_exc())
    finally:
        for name in reversed(names):
            try:
                command(["/usr/bin/systemctl", "stop", name], check=False)
                command(["/usr/bin/systemctl", "reset-failed", name], check=False)
            except Exception as exc:
                report["cleanup"]["errors"].append(str(exc))
        for path in created:
            try:
                path.unlink()
            except Exception as exc:
                report["cleanup"]["errors"].append(str(exc))
        try:
            command(["/usr/bin/systemctl", "daemon-reload"])
            report["cleanup"]["units"] = show(*names)
            c.require(
                all(v["LoadState"] == "not-found" for v in report["cleanup"]["units"].values()),
                "Trial unit remains loaded",
            )
        except Exception as exc:
            report["cleanup"]["errors"].append(str(exc))
        try:
            after = snapshot(plan)
            report["after"] = after
            report["operating_unchanged"] = before is not None and before == after
            c.require(report["operating_unchanged"], "Operating state changed during trial")
        except Exception as exc:
            report["passed"] = False
            report["cleanup"]["errors"].append(str(exc))
        try:
            # Do not remove a running trial's input/output when manager cleanup is uncertain.
            c.require(not report["cleanup"]["errors"], "Trial cleanup requires review")
            if state.exists():
                c.require(
                    state.parent == c.ROOT / "reports"
                    and state.name == "t18-output-trial-" + nonce,
                    "Unsafe trial cleanup",
                )
                shutil.rmtree(state)
            shutil.rmtree(trial)
            report["cleanup"]["paths_absent"] = not state.exists() and not trial.exists()
        except Exception as exc:
            report["passed"] = False
            report["cleanup"]["errors"].append(str(exc))
        report["passed"] = report["passed"] and not report["cleanup"]["errors"]
        report["finished_at"] = datetime.now(UTC).isoformat()
        target = Path(dest["report_directory"]) / ("system-preflight-result-" + nonce + ".json")
        # Save as the owning account; never create root files through a user's symlink.
        save = "import os,sys; fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644); f=os.fdopen(fd,'wb'); f.write(sys.stdin.buffer.read()); f.flush(); os.fsync(f.fileno()); f.close()"
        done = subprocess.run(
            [
                "/usr/sbin/runuser",
                "-u",
                "yoonhc",
                "--",
                "/usr/bin/python3",
                "-B",
                "-I",
                "-c",
                save,
                str(target),
            ],
            input=c.encode(report),
            capture_output=True,
            timeout=30,
        )
        c.require(done.returncode == 0, "Cannot save preflight result: " + done.stderr.decode())
        print(
            json.dumps(
                {
                    "passed": report["passed"],
                    "report": str(target),
                    "operating_unchanged": report.get("operating_unchanged"),
                    "cleanup": report["cleanup"],
                },
                indent=2,
            )
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--sealed-run", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    plan, seal = checked()
    if args.run or args.sealed_run:
        c.require(os.geteuid() == 0, "sudo required for disposable system units")
        if args.sealed_run:
            raise SystemExit(sealed_run(plan))
        snapshot(plan)
        install_trial(plan, seal)
    else:
        print(
            json.dumps(
                {
                    "preparation_id": plan["preparation_id"],
                    "release_id": plan["release_id"],
                    "production_applied": False,
                    "scope": "disposable system timer and two private namespace trials only",
                },
                indent=2,
            )
        )
