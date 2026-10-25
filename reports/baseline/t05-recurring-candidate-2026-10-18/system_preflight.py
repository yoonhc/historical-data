"""Transient system timer/service inspection; production masks/grants stay disabled.

Root is needed only for sealed temporary /opt and /var/lib trees and two nonce
systemd-run units. No daemon-reload, operating unit control, live preview,
registration, backup, archive command or deletion of operating checkpoints.
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = Path("/etc/marketdata/checkpoint-clock-retirement")
BASE = HERE.parent / "t05-completion-evidence-2026-10-11"
AUTO = "marketdata-checkpoint-retirement"
PROCESSES = (
    "chrony.service",
    "marketdata-collector@depth.service",
    "marketdata-collector@aggtrade.service",
)
COMMANDS = []


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def command(argv, *, check=True, input=None, timeout=45):
    value = subprocess.run(
        list(map(str, argv)),
        capture_output=True,
        text=True,
        input=input,
        timeout=timeout,
        env={**os.environ, "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    COMMANDS.append(
        {
            "argv": list(map(str, argv)),
            "code": value.returncode,
            "stdout": value.stdout,
            "stderr": value.stderr,
        }
    )
    require(not check or value.returncode == 0, f"Command failed: {argv}: {value.stderr[-2000:]}")
    return value


def show(name, properties):
    raw = command(
        ["/usr/bin/systemctl", "--system", "show", name, "--property=" + properties], check=False
    ).stdout
    rows = [line.split("=", 1) for line in raw.splitlines()]
    require(len(rows) == len({r[0] for r in rows}), "Duplicate scalar property")
    return dict(rows)


def fingerprint(path):
    st = path.lstat()
    result = {"uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode)}
    if path.is_symlink():
        return {**result, "link": os.readlink(path)}
    require(stat.S_ISREG(st.st_mode), "Expected regular file: " + str(path))
    return {**result, "sha256": sha(path.read_bytes())}


def protected():
    paths = [
        *CONFIG.glob("*.json"),
        *CONFIG.glob("*.toml"),
        *CONFIG.glob("units/*"),
        Path("/etc/systemd/system/marketdata-checkpoint-retirement-apply@.service"),
        Path("/var/lib/marketdata-clock/boot-state.json"),
        Path("/etc/chrony/marketdata.conf"),
    ]
    binding = json.loads((CONFIG / "clock-binding.json").read_bytes())
    paths.append(Path(binding["bootstrap_profile"]))
    masks = [
        base / (AUTO + suffix)
        for base in (Path("/etc/systemd/system"), Path("/home/yoonhc/.config/systemd/user"))
        for suffix in (".service", ".timer")
    ]
    require(
        all(p.is_symlink() and os.readlink(p) == "/dev/null" for p in masks),
        "Operating mask differs",
    )
    for name in ("manual.json", "recurring.json"):
        grant = json.loads((CONFIG / name).read_bytes())
        require(
            grant["enabled"] is False
            and grant.get("approved_at") is None
            and grant.get("approval_reference") is None,
            "Operating authority is not disabled",
        )
    return {
        "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "files": {str(p): fingerprint(p) for p in sorted(set(paths))},
        "masks": {str(p): fingerprint(p) for p in masks},
        "processes": {n: show(n, "MainPID,InvocationID,ActiveState") for n in PROCESSES},
    }


def service_properties(text, state):
    """Carry the final service safeguards, replacing execution and writable paths."""
    omit = {"ExecStart", "WorkingDirectory", "ReadWritePaths", "StandardOutput", "StandardError"}
    section, result, environment = "", [], []
    for line in text.splitlines():
        if line.startswith("["):
            section = line
        elif section == "[Service]" and "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if key == "Environment":
                environment.append(value)
            elif key not in omit:
                result.append(key + "=" + value)
    require(
        "ProtectClock=yes" in result and "User=yoonhc" in result and "Restart=no" in result,
        "Missing final safeguards",
    )
    result += [
        "Environment=" + " ".join(environment),
        "WorkingDirectory=" + str(state),
        "ReadWritePaths=" + str(state),
        "StandardOutput=journal",
        "StandardError=journal",
    ]
    return result


def checked():
    seal = json.loads((HERE / "preflight-inputs.json").read_bytes())
    for name, digest in seal["files"].items():
        require(sha((HERE / name).read_bytes()) == digest, "Reviewed input differs: " + name)
    for path, digest in seal["operating_input_sha256"].items():
        require(sha(Path(path).read_bytes()) == digest, "Operating configuration differs: " + path)
    validation = json.loads((HERE / "validation.json").read_bytes())
    require(
        validation["passed"]
        and validation["candidate_cases"] == 46
        and validation["preflight_tool_cases"] == 15,
        "Required candidate verification incomplete",
    )
    require(
        json.loads((HERE / "reproduction.json").read_bytes())["passed"],
        "Relocated candidate not verified",
    )
    manifest = json.loads((HERE / "manifest.json").read_bytes())
    require(manifest["release_id"] == seal["release_id"], "Release differs")
    for name, digest in manifest["source_sha256"].items():
        require(sha((HERE / "work" / name).read_bytes()) == digest, "Source differs: " + name)
    environment = json.loads((HERE / "environment-files.json").read_bytes())
    for name, expected in environment.items():
        path = HERE / "work/.venv" / name
        actual = (
            {"link": os.readlink(path)} if path.is_symlink() else {"sha256": sha(path.read_bytes())}
        )
        require(actual == expected, "Environment differs: " + name)
    return seal, manifest, environment


def write_new(path, raw, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode), "wb"
    ) as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def install_trial(stage, manifest, environment):
    release = stage / "release"
    for name, digest in manifest["source_sha256"].items():
        raw = (HERE / "work" / name).read_bytes()
        require(sha(raw) == digest, "Source changed during copy")
        write_new(release / name, raw)
    for name, value in environment.items():
        target = release / ".venv" / name
        if "link" in value:
            require(
                name in ("bin/python", "bin/python3", "bin/python3.12", "lib64")
                and value["link"] in ("/usr/bin/python3", "python", "lib"),
                "Unreviewed environment link",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(value["link"])
        else:
            raw = (HERE / "work/.venv" / name).read_bytes()
            require(sha(raw) == value["sha256"], "Environment changed during copy")
            write_new(target, raw, 0o755 if name.startswith("bin/") else 0o644)
    write_new(stage / "trial_worker.py", (HERE / "trial_worker.py").read_bytes())
    return release


def wait_result(state, previous, service, timeout=55):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        files = set(state.glob("result-*.json")) - previous
        if files:
            require(len(files) == 1, "Unexpected extra invocation")
            return json.loads(next(iter(files)).read_bytes())
        status = show(service, "ActiveState,SubState,Result,ExecMainStatus")
        if status.get("ActiveState") == "failed":
            raise RuntimeError("Trial service failed: " + str(status))
        time.sleep(0.25)
    raise RuntimeError("Trial timed out")


def wait_idle(service):
    until = time.monotonic() + 15
    while time.monotonic() < until:
        status = show(
            service,
            "Id,LoadState,ActiveState,MainPID,ControlPID,Job,Result,ExecMainStatus,User,ProtectClock,ProtectSystem,ProtectHome,Restart,FragmentPath",
        )
        require(status.get("LoadState") == "loaded", "Trial unit disappeared before inspection")
        if (
            status.get("ActiveState") == "inactive"
            and status.get("MainPID") == "0"
            and not status.get("Job")
        ):
            return status
        time.sleep(0.2)
    raise RuntimeError("Trial did not become idle")


def timer_calendar(deadline):
    # systemd-run makes transient timers RemainAfterElapse=false. A single
    # dated occurrence unloads both units before the second invocation. Keep
    # a future daily occurrence until our explicit nonce-only cleanup.
    return deadline.strftime("*-*-* %H:%M:%S UTC")


def check_invocations(state, service, result):
    timed = wait_result(state, set(), service)
    result["timer"] = timed  # retain this evidence if the later stage fails
    result["first_unit"] = wait_idle(service)
    require(
        timed["actual"]["scheduled"] and timed["installed_guard_passed"] and timed["passed"],
        "Timer trial failed",
    )
    previous = set(state.glob("result-*.json"))
    command(["/usr/bin/systemctl", "--system", "start", "--no-block", service])
    manual = wait_result(state, previous, service)
    result["manual"] = manual
    result["last_unit"] = wait_idle(service)
    require(
        not manual["actual"]["scheduled"]
        and manual["actual"]["service_verified"]
        and manual["invocation"] != timed["invocation"]
        and manual["passed"],
        "Manual start misclassified",
    )


def save_user(path, result):
    # Root does not follow output paths under the writable workspace.
    code = (
        'import os,sys; f=os.fdopen(os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644),"w"); '
        "f.write(sys.stdin.read()); f.flush(); os.fsync(f.fileno()); f.close()"
    )
    command(
        [
            "/usr/sbin/runuser",
            "-u",
            "yoonhc",
            "--",
            "/usr/bin/python3",
            "-B",
            "-I",
            "-c",
            code,
            path,
        ],
        input=json.dumps(result, indent=2) + "\n",
    )


def perform():
    seal, manifest, environment = checked()
    require(
        os.geteuid() == 0, "Administrator authentication is required for the transient unit trial"
    )
    os.umask(0o022)
    nonce = uuid.uuid4().hex[:16]
    prefix = "md-t05-recurring-" + nonce
    stage, state = Path("/opt") / prefix, Path("/var/lib") / prefix
    service, timer = prefix + ".service", prefix + ".timer"
    before = protected()
    result = {
        "passed": False,
        "release_id": manifest["release_id"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "before": before,
        "automatic_retirement": "masked",
        "production_installation": False,
        "checkpoint_deletion": False,
        "daemon_reload": False,
        "cleanup": {"errors": []},
    }
    created = []
    units_requested = False
    try:
        for path in (stage, state):
            require(not path.exists() and not path.is_symlink(), "Trial path collision")
            path.mkdir(mode=0o755)
            created.append(path)
            write_new(path / ".owner", prefix.encode())
        os.chown(state, 1000, 1000)
        control = state / "control"
        control.mkdir(mode=0o700)
        os.chown(control, 1000, 1000)
        print("installing sealed temporary T05 candidate", flush=True)
        release = install_trial(stage, manifest, environment)
        cfg = stage / "config"
        settings = {
            "retention_policy": str(CONFIG / "policy.toml"),
            "authorization_file": str(cfg / "recurring.json"),
            "manual_authorization_file": str(CONFIG / "manual.json"),
            "clock_binding_file": str(CONFIG / "clock-binding.json"),
            "state_root": str(control),
            "unit_directory": str(cfg / "units"),
        }
        write_new(
            cfg / "settings.toml",
            "".join(f"{k} = {json.dumps(v)}\n" for k, v in settings.items()).encode(),
        )
        final_service = (HERE / "deployment" / (AUTO + ".service")).read_text()
        props = service_properties(final_service, state)
        result["requested_properties"] = props
        deadline = datetime.now(timezone.utc) + timedelta(seconds=35)
        argv = [
            "/usr/bin/systemd-run",
            "--unit=" + prefix,
            "--on-calendar=" + timer_calendar(deadline),
            "--timer-property=Persistent=false",
            "--timer-property=AccuracySec=1ms",
        ]
        for prop in props:
            argv += ["--property=" + prop]
        argv += [
            release / ".venv/bin/python",
            "-B",
            "-I",
            stage / "trial_worker.py",
            "--stage",
            stage,
            "--state",
            state,
            "--service",
            service,
            "--timer",
            timer,
        ]
        units_requested = True  # also clean a partially accepted transient request
        command(argv)
        # Keep the manager-created, root-owned fragment itself as the trial
        # canonical. No production fragment is unmasked or replaced.
        fragments = {}
        result["trial_fragments"] = fragments
        for name in (service, timer):
            path = Path(show(name, "FragmentPath")["FragmentPath"])
            require(
                path.parent == Path("/run/systemd/transient") and path.name == name,
                "Unexpected trial fragment",
            )
            meta = fingerprint(path)
            require(meta["uid"] == 0 and not meta["mode"] & 0o022, "Unsealed transient fragment")
            fragments[name] = {"path": str(path), "text": path.read_text(), "fingerprint": meta}
        for path in (HERE / "deployment").glob("*"):
            raw = path.read_bytes()
            if path.name == AUTO + ".service":
                raw = fragments[service]["text"].encode()
            if path.name == AUTO + ".timer":
                raw = fragments[timer]["text"].encode()
            write_new(cfg / "units" / path.name, raw)
        proposal = command(
            [
                "/usr/sbin/runuser",
                "-u",
                "yoonhc",
                "--",
                release / ".venv/bin/python",
                "-B",
                "-I",
                release / "scripts/run_clock_retirement.py",
                "--settings",
                cfg / "settings.toml",
                "propose",
            ]
        ).stdout
        require(json.loads(proposal)["enabled"] is False, "Trial grant must be disabled")
        write_new(cfg / "recurring.json", proposal.encode())
        print(
            "checking real system timer, fragment guard, read-only clock and disabled grant",
            flush=True,
        )
        check_invocations(state, service, result)
        result["passed"] = True
    except BaseException as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        if units_requested:
            result["journal"] = command(
                [
                    "/usr/bin/journalctl",
                    "--boot",
                    "-u",
                    service,
                    "--no-pager",
                    "-n",
                    "40",
                    "-o",
                    "cat",
                ],
                check=False,
            ).stdout
            for name in (timer, service):
                try:
                    command(["/usr/bin/systemctl", "--system", "stop", name], check=False)
                    command(["/usr/bin/systemctl", "--system", "reset-failed", name], check=False)
                except Exception as exc:
                    result["cleanup"]["errors"].append(str(exc))
            until = time.monotonic() + 10
            while True:
                states = {
                    name: show(name, "LoadState,MainPID,ControlPID,ActiveState,Job")
                    for name in (service, timer)
                }
                if (
                    all(v.get("LoadState") == "not-found" for v in states.values())
                    or time.monotonic() >= until
                ):
                    break
                time.sleep(0.2)
            result["cleanup"]["unit_states"] = states
            if any(
                v.get("MainPID", "0") != "0" or v.get("ControlPID", "0") != "0" or v.get("Job")
                for v in states.values()
            ):
                result["cleanup"]["errors"].append("Trial process or job remains")
            if any(v.get("LoadState") != "not-found" for v in states.values()):
                result["cleanup"]["errors"].append("Transient unit still loaded")
        if not result["cleanup"]["errors"]:
            for path in reversed(created):
                try:
                    require(
                        path.name == prefix
                        and path.parent in (Path("/opt"), Path("/var/lib"))
                        and not path.is_symlink()
                        and (path / ".owner").read_text() == prefix,
                        "Unsafe trial cleanup",
                    )
                    shutil.rmtree(path)
                except Exception as exc:
                    result["cleanup"]["errors"].append(str(exc))
        result["cleanup"]["paths_absent"] = not stage.exists() and not state.exists()
        try:
            result["after"] = protected()
            result["operating_unchanged"] = result["after"] == before
        except Exception as exc:
            result["operating_unchanged"] = False
            result["after_error"] = str(exc)
        result["passed"] = bool(
            result["passed"]
            and result["operating_unchanged"]
            and not result["cleanup"]["errors"]
            and result["cleanup"]["paths_absent"]
        )
        result["commands"] = COMMANDS.copy()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        output = HERE / ("system-preflight-result-" + nonce + ".json")
        save_user(output, result)
        print(
            json.dumps(
                {
                    "passed": result["passed"],
                    "report": str(output),
                    "operating_unchanged": result["operating_unchanged"],
                    "cleanup": result["cleanup"],
                },
                indent=2,
            )
        )
    return result["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.run:
        raise SystemExit(0 if perform() else 1)
    seal, manifest, environment = checked()
    print(
        json.dumps(
            {
                "checked": True,
                "release_id": manifest["release_id"],
                "temporary_units_only": True,
                "production_installation": False,
                "automatic_retirement": "masked",
            },
            indent=2,
        )
    )
