"""Limited administrator test: fixed code, real read-only inputs, no deletion.

Creates only md-t05-preview-<nonce> paths and one temporary system unit. Never
installs production units, enables timers, changes grants/ledger/time, or stops
operating services. --check is unprivileged; --run needs sudo authentication.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import stat
import subprocess
import time
import tomllib
import traceback
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMANDS = []
UID = 1000


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def run(argv, *, required=True, timeout=30, input=None):
    p = subprocess.run(
        [str(x) for x in argv],
        capture_output=True,
        text=True,
        input=input,
        timeout=timeout,
        cwd="/",
        env={**os.environ, "LC_ALL": "C"},
    )
    value = {
        "argv": list(map(str, argv)),
        "code": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
    }
    COMMANDS.append(value)
    if required and p.returncode:
        raise RuntimeError(value)
    return value


def write(path, blob, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())


def record(path):
    p = Path(path)
    if p.is_symlink():
        return {"link": os.readlink(p)}
    if not p.exists():
        return {"absent": True}
    s = p.stat()
    return {"sha256": sha(p.read_bytes()), "mode": oct(stat.S_IMODE(s.st_mode)), "uid": s.st_uid}


def show(unit):
    raw = run(["/usr/bin/systemctl", "--system", "show", unit], required=False)["stdout"]
    # Preserve repeated values rather than overwriting them.
    fields = {}
    for line in raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            fields.setdefault(k, []).append(v)
    return fields


def one(fields, key, default=""):
    values = fields.get(key, [default])
    if len(values) != 1:
        raise RuntimeError("Duplicate scalar property: " + key)
    return values[0]


def snapshot(inputs):
    return {
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "files": {p: record(p) for p in inputs["operating_files"]},
        "services": {
            u: {k: one(showed, k) for k in ("MainPID", "InvocationID", "ActiveState")}
            for u in inputs["operating_services"]
            for showed in [show(u)]
        },
    }


def validate():
    inputs = json.loads((HERE / "preflight-inputs.json").read_text())
    for relative, digest in inputs["files"].items():
        if sha((HERE / relative).read_bytes()) != digest:
            raise RuntimeError("Changed reviewed input: " + relative)
    for name, count in (("candidate-final-tests.xml", 108), ("reproduced-clock-tests.xml", 39)):
        suite = list(ET.parse(HERE / name).getroot().iter("testsuite"))
        if (
            len(suite) != 1
            or int(suite[0].get("tests")) != count
            or any(int(suite[0].get(k, "0")) for k in ("failures", "errors", "skipped"))
        ):
            raise RuntimeError("Required fixed-candidate validation is incomplete")
    manifest = json.loads((HERE / "manifest.json").read_text())
    for name, digest in manifest["source_sha256"].items():
        if sha((HERE / "reproduced" / name).read_bytes()) != digest:
            raise RuntimeError("Changed reproduced source: " + name)
    environment = json.loads((HERE / "environment-files.json").read_text())
    for name, value in environment.items():
        p = HERE / "reproduced/.venv" / name
        actual = {"link": os.readlink(p)} if p.is_symlink() else {"sha256": sha(p.read_bytes())}
        if actual != value:
            raise RuntimeError("Changed installed environment: " + name)
    before = json.loads((HERE / "host-before.json").read_text())
    for path, expected in before["files"].items():
        if record(path) != expected:
            raise RuntimeError("Operating configuration changed: " + path)
    return inputs, manifest, environment


def save_user(path, blob):
    # Root never follows a writable report-output path.
    code = "import os,sys; fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644); f=os.fdopen(fd,'w'); f.write(sys.stdin.read()); f.flush(); os.fsync(f.fileno()); f.close()"
    run(
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
        input=blob,
    )


def perform(inputs, manifest, environment):
    if os.geteuid() != 0:
        raise RuntimeError("Administrator authentication is required for --run")
    os.umask(0o022)
    nonce = uuid.uuid4().hex[:16]
    name = "md-t05-preview-" + nonce
    stage, state = Path("/opt") / name, Path("/var/lib") / name
    unit = name + ".service"
    unit_path = Path("/run/systemd/system") / unit
    created = []
    unit_created = False
    result = {
        "release_id": manifest["release_id"],
        "passed": False,
        "access_passed": False,
        "preview_completed": False,
        "nonce": nonce,
        "unit": unit,
        "deletion_performed": False,
        "production_installation": False,
        "automatic_retirement": "masked",
        "clock_control": False,
    }

    def interrupted(signum, frame):
        raise InterruptedError(f"Signal {signum}; clean this test only")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        result["before"] = snapshot(inputs)
        for base in (stage, state):
            base.mkdir(mode=0o755)
            created.append(base)
            write(base / ".owner.json", json.dumps({"nonce": nonce}).encode())
        assert not os.statvfs(stage).f_flag & os.ST_NOEXEC
        release, cfg = stage / "release", stage / "config"
        for relative, digest in manifest["source_sha256"].items():
            blob = (HERE / "reproduced" / relative).read_bytes()
            assert sha(blob) == digest
            write(release / relative, blob)
        for relative, value in environment.items():
            target = release / ".venv" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if "link" in value:
                assert relative in {"bin/python", "bin/python3", "bin/python3.12", "lib64"}
                assert value["link"] in {"/usr/bin/python3", "python", "lib"}
                target.symlink_to(value["link"])
            else:
                blob = (HERE / "reproduced/.venv" / relative).read_bytes()
                assert sha(blob) == value["sha256"]
                write(target, blob, 0o755 if relative.startswith("bin/") else 0o644)
        write(stage / "probe.py", (HERE / "probe.py").read_bytes())
        for p in (state / "control", state / "cache"):
            p.mkdir(mode=0o700)
            os.chown(p, UID, UID)
        profile = tomllib.loads(Path(inputs["archive_profile"]).read_text())
        profile["cache_root"] = str(state / "cache")
        write(
            cfg / "archive.toml",
            "".join(f"{k} = {json.dumps(v)}\n" for k, v in profile.items()).encode(),
        )
        policy = tomllib.loads((HERE / "deployment/policy.toml").read_text())
        policy["archive_profile"] = str(cfg / "archive.toml")
        write(
            cfg / "policy.toml",
            "".join(f"{k} = {json.dumps(v)}\n" for k, v in policy.items()).encode(),
        )
        write(cfg / "clock-binding.json", (HERE / "deployment/clock-binding.json").read_bytes())
        settings = {
            "retention_policy": str(cfg / "policy.toml"),
            "authorization_file": str(cfg / "recurring.json"),
            "manual_authorization_file": str(cfg / "manual.json"),
            "clock_binding_file": str(cfg / "clock-binding.json"),
            "state_root": str(state / "control"),
            "unit_directory": str(cfg / "units"),
        }
        write(
            cfg / "settings.toml",
            "".join(f"{k} = {json.dumps(v)}\n" for k, v in settings.items()).encode(),
        )
        spec = importlib.util.spec_from_file_location(
            "pinned_renderer", release / "scripts/render_retirement_units.py"
        )
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        rendered = renderer.render(
            str(release), str(cfg / "settings.toml"), str(state / "control"), profile
        )
        for filename, content in rendered.items():
            write(cfg / "units" / filename, content.encode())
        python = release / ".venv/bin/python"
        proposal = run(
            [
                "/usr/sbin/runuser",
                "-u",
                "yoonhc",
                "--",
                python,
                "-B",
                "-I",
                release / "scripts/run_clock_retirement.py",
                "--settings",
                cfg / "settings.toml",
                "propose",
            ]
        )["stdout"]
        assert json.loads(proposal)["enabled"] is False
        write(cfg / "recurring.json", proposal.encode())
        preview = rendered["marketdata-checkpoint-retirement-preview.service"]
        preview = (
            preview[: preview.index("ExecStart=")]
            + f"ExecStart={python} -B -I {stage}/probe.py {release} {cfg}/settings.toml {unit}\n"
        )
        write(unit_path, preview.encode())
        unit_created = True
        result["rendered_test_unit"] = preview
        run(["/usr/bin/systemd-analyze", "verify", str(unit_path)])
        run(["/usr/bin/systemctl", "daemon-reload"])
        print("starting read-only system preview", flush=True)
        run(["/usr/bin/systemctl", "start", "--no-block", unit])
        deadline = time.monotonic() + 1800
        while True:
            fields = show(unit)
            if one(fields, "ActiveState") in {"inactive", "failed"} and one(fields, "Job") in {
                "",
                "0",
            }:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Preview exceeded bounded test window")
            time.sleep(1)
        result["effective_unit"] = fields
        expected = {
            "User": "yoonhc",
            "Group": "yoonhc",
            "ProtectClock": "yes",
            "NoNewPrivileges": "yes",
            "ProtectSystem": "strict",
            "ProtectHome": "read-only",
            "CapabilityBoundingSet": "",
            "AmbientCapabilities": "",
            "PrivateDevices": "yes",
            "PrivateTmp": "yes",
            "KillMode": "mixed",
            "Restart": "no",
            "Type": "oneshot",
            "ProtectKernelTunables": "yes",
            "ProtectKernelModules": "yes",
            "ProtectControlGroups": "yes",
            "RestrictSUIDSGID": "yes",
            "RestrictRealtime": "yes",
            "LockPersonality": "yes",
            "DropInPaths": "",
        }
        result["protections_match"] = {k: one(fields, k) == v for k, v in expected.items()}
        probe = state / "control/probe-result.json"
        if probe.exists():
            result["probe"] = json.loads(probe.read_text())
            result["access_passed"] = result["probe"]["access_passed"] and all(
                result["protections_match"].values()
            )
            result["preview_completed"] = result["probe"]["preview_completed"]
        result["passed"] = bool(result["access_passed"] and result["preview_completed"])
    except BaseException as exc:
        result.update(error=repr(exc), traceback=traceback.format_exc())
    finally:
        errors = []
        try:
            result["journal"] = run(
                [
                    "/usr/bin/journalctl",
                    "-u",
                    unit,
                    "-n",
                    "100",
                    "--no-pager",
                    "-o",
                    "short-monotonic",
                ],
                required=False,
            )
            if unit_created:
                run(["/usr/bin/systemctl", "stop", unit], required=False)
                fields = show(unit)
                assert one(fields, "MainPID", "0") == "0" and one(fields, "ActiveState") in {
                    "inactive",
                    "failed",
                }
                run(["/usr/bin/systemctl", "reset-failed", unit], required=False)
                unit_path.unlink()
                run(["/usr/bin/systemctl", "daemon-reload"])
            result["test_state"] = {}
            for p in (state / "control").rglob("*"):
                if p.is_file() and not p.is_symlink() and p.suffix in {".json", ".jsonl"}:
                    result["test_state"][str(p.relative_to(state))] = p.read_text()
            for p in reversed(created):
                assert p.name == name and p.parent in {Path("/opt"), Path("/var/lib")}
                assert p.stat().st_uid == 0 and not p.is_symlink()
                assert json.loads((p / ".owner.json").read_text()) == {"nonce": nonce}
                assert shutil.rmtree.avoids_symlink_attacks
                shutil.rmtree(p)
        except BaseException as exc:
            errors.append(repr(exc))
        result["cleanup"] = {
            "errors": errors,
            "paths_absent": all(not p.exists() for p in (stage, state, unit_path)),
            "unit_load_state": one(show(unit), "LoadState"),
        }
        result["after"] = snapshot(inputs)
        result["operating_unchanged"] = result.get("before") == result["after"]
        result["passed"] = bool(
            result["passed"]
            and result["operating_unchanged"]
            and not errors
            and result["cleanup"]["paths_absent"]
            and result["cleanup"]["unit_load_state"] == "not-found"
        )
        result["commands"] = list(COMMANDS)
        output = HERE / f"system-preflight-result-{nonce}.json"
        save_user(output, json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "passed",
                        "access_passed",
                        "preview_completed",
                        "cleanup",
                        "operating_unchanged",
                    )
                }
                | {"report": str(output)},
                indent=2,
            ),
            flush=True,
        )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args()
    inputs, manifest, environment = validate()
    if args.check:
        print(
            json.dumps(
                {
                    "inputs_valid": True,
                    "release_id": manifest["release_id"],
                    "environment_files": len(environment),
                    "root_test_executed": False,
                },
                indent=2,
            )
        )
    else:
        raise SystemExit(perform(inputs, manifest, environment))
