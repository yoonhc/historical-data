"""Limited root permission preflight. Never deploys or changes clock/RTC/services.

Only uniquely named transient TEST services are created/stopped. No package,
configuration, enrollment, clock command, operating service control, or reboot.
Run --check as a normal user; --run requires an ordinary administrator sudo.
"""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import time
import traceback
import uuid

HERE = Path(__file__).resolve().parent
ROOT = Path("/home/yoonhc/projects/algoTrade/historical_data")
CANDIDATE = ROOT / "reports/baseline/system-runtime-bootstrap-revision-2026-10-07"
PROTECTION = ("UMask", "NoNewPrivileges", "CapabilityBoundingSet", "ProtectClock", "ProtectSystem",
              "ProtectHome", "PrivateTmp", "RestrictAddressFamilies", "StateDirectoryMode")
COMMANDS = []


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(argv, timeout=20, required=True, input=None):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout,
                            input=input, cwd="/", env={**os.environ, "LC_ALL": "C", "SYSTEMD_COLORS": "0"})
    record = {"argv": argv, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    COMMANDS.append(record)
    if required and result.returncode:
        raise RuntimeError(record)
    return record


def user_systemctl(*args):
    return ["/usr/sbin/runuser", "-u", "yoonhc", "--", "/usr/bin/env", "XDG_RUNTIME_DIR=/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus", "/usr/bin/systemctl", "--user", *args]


def file_record(path):
    p = Path(path)
    try:
        s = p.lstat()
    except FileNotFoundError:
        return {"exists": False}
    result = {"exists": True, "uid": s.st_uid, "gid": s.st_gid, "mode": oct(stat.S_IMODE(s.st_mode))}
    if p.is_symlink():
        result["link"] = os.readlink(p)
    if p.is_file():
        result["sha256"] = digest(p)
    if p.is_dir() and not p.is_symlink():
        result["children"] = {str(q.relative_to(p)): file_record(q) for q in sorted(p.iterdir())}
    return result


def prepare():
    inputs = json.loads((HERE / "inputs.json").read_text())
    errors = []
    for path, expected in inputs["pinned_files"].items():
        if digest(path) != expected:
            errors.append(path)
    if errors:
        raise RuntimeError({"changed_pinned_inputs": errors})
    assert pwd.getpwnam("yoonhc").pw_uid == 1000
    unit = configparser.ConfigParser(interpolation=None)
    unit.optionxform = str
    unit.read(CANDIDATE / "units/system/marketdata-clock-bootstrap.service")
    protection = {name: unit["Service"][name] for name in PROTECTION}
    assert unit["Service"]["ReadWritePaths"] == "/run/chrony"
    assert unit["Service"]["StateDirectory"] == "marketdata-clock"
    assert protection == inputs["protection"]
    assert set(protection["CapabilityBoundingSet"].split()) == {"CAP_DAC_OVERRIDE", "CAP_SETUID", "CAP_SETGID"}
    assert protection["ProtectClock"] == "yes" and protection["ProtectSystem"] == "strict"
    return inputs, protection


def snapshot(inputs):
    fixed_paths = set(inputs["pinned_files"]) | set(inputs["operating_files"]) | set(inputs["installed_user_units"])
    fixed_paths.update(inputs["host_paths"])
    result = {"wall_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
              "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
              "files": {p: file_record(p) for p in sorted(fixed_paths)}}
    properties = ["Id", "ActiveState", "SubState", "MainPID", "InvocationID", "UnitFileState", "FragmentPath", "DropInPaths"]
    prop_args = [f"--property={p}" for p in properties]
    result["system_services"] = command(["/usr/bin/systemctl", "show", "systemd-timesyncd.service", "chrony.service", *prop_args], required=False)
    result["user_services"] = command(user_systemctl("show", *sorted({Path(p).name for p in inputs["installed_user_units"]}), *prop_args), required=False)
    result["user_unit_files"] = command(user_systemctl("list-unit-files", "marketdata-*", "--no-pager", "--no-legend"), required=False)
    result["packages"] = command(["/usr/bin/dpkg-query", "-W", "-f=${Package} ${db:Status-Status} ${Version}\n", "chrony", "systemd-timesyncd"], required=False)
    return result


def show(unit):
    result = command(["/usr/bin/systemctl", "show", unit, "--no-pager"], required=False)
    return dict(line.split("=", 1) for line in result["stdout"].splitlines() if "=" in line)


def transient(name, properties, argv):
    assert name.startswith("md-root-preflight-") and name.endswith(".service")
    args = ["/usr/bin/systemd-run", f"--unit={name}", "--quiet"]
    for key, value in properties.items():
        args += ["--property", f"{key}={value}"]
    return command([*args, "--", *argv])


def selected_properties(info):
    keys = (*PROTECTION, "StateDirectory", "ReadWritePaths", "Type", "Restart", "TimeoutStartUSec", "User", "PrivateDevices", "PrivateNetwork", "ExecStart", "MainPID", "ActiveState", "SubState", "Result", "ExecMainStatus")
    return {k: info.get(k) for k in keys}


def service_map(text):
    result = {}
    for block in text.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if fields.get("Id"):
            result[fields["Id"]] = fields
    return result


def same_protection(info, protection, state_name, socket_dir):
    expected = {**protection, "StateDirectory": state_name, "ReadWritePaths": str(socket_dir)}
    result = {}
    for key, value in expected.items():
        actual = info.get(key)
        if key in {"CapabilityBoundingSet", "RestrictAddressFamilies"}:
            equal = set(value.lower().split()) == set((actual or "").lower().split())
        elif key in {"StateDirectoryMode", "UMask"}:
            equal = int(value, 8) == int(actual or "777", 8)
        else:
            equal = actual == value
        result[key] = {"expected": value, "actual": actual, "equal": equal}
    return result


def save_as_user(result, nonce):
    destination = HERE / f"result-{nonce}.json"
    # Output creation is unprivileged; root never follows a writable output symlink.
    code = "import os,sys; p=sys.argv[1]; fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644); data=sys.stdin.buffer.read(); f=os.fdopen(fd,'wb'); f.write(data); f.flush(); os.fsync(f.fileno()); f.close()"
    command(["/usr/sbin/runuser", "-u", "yoonhc", "--", "/usr/bin/python3", "-I", "-c", code, str(destination)],
            input=json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(f"Result: {destination}", flush=True)
    print(f"Passed: {result['passed']}; cleanup: {result.get('cleanup', {})}", flush=True)


def perform(inputs, protection):
    if os.getuid() != 0 or os.geteuid() != 0:
        raise SystemExit("Administrator authentication required. Run this reviewed script with sudo; never send a password in chat.")
    os.umask(0o022)
    nonce = uuid.uuid4().hex[:16]
    name = "md-root-preflight-" + nonce
    stage, state = Path("/run") / name, Path("/var/lib") / name
    sock = stage / "chrony"
    daemon_unit, probe_unit = name + "-daemon.service", name + "-probe.service"
    created, started = [], []
    report = {"schema": 1, "passed": False, "release_id": inputs["release_id"], "nonce": nonce,
              "scope": "isolated-root-permission-preflight-only", "operating_transition": False,
              "paths": {"stage": str(stage), "state": str(state), "socket_dir": str(sock)},
              "units": [daemon_unit, probe_unit], "commands": COMMANDS}

    def interrupted(signum, frame):
        raise InterruptedError(f"Signal {signum}; cleaning only this preflight")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        report["before"] = snapshot(inputs)
        packages = report["before"]["packages"]["stdout"].splitlines()
        assert not any(line.startswith("chrony installed ") for line in packages), "Chrony unexpectedly installed; review required"
        assert service_map(report["before"]["system_services"]["stdout"])["systemd-timesyncd.service"]["ActiveState"] == "active"
        for p in (stage, state):
            p.mkdir(mode=0o755)
            created.append(p)
            (p / ".preflight-owner.json").write_text(json.dumps({"nonce": nonce}))
        sock.mkdir(mode=0o700)
        for target, source in inputs["stage_sources"].items():
            data = Path(source).read_bytes()
            assert hashlib.sha256(data).hexdigest() == inputs["pinned_files"][source]
            with (stage / target).open("xb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            (stage / target).chmod(0o755 if target in {"chronyd", "chronyc"} else 0o444)
        config = f"pidfile {sock}/chronyd.pid\nport 0\ncmdport 0\nbindcmdaddress {sock}/chronyd.sock\n"
        config_path = stage / "chrony-test.conf"
        config_path.write_text(config)
        config_path.chmod(0o444)
        report["daemon_configuration"] = config
        report["daemon_config_parse"] = command([str(stage / "chronyd"), "-p", "-f", str(config_path)])
        daemon_properties = {
            "Type": "simple", "Restart": "no", "RuntimeMaxSec": "180s", "TimeoutStopSec": "10s",
            "NoNewPrivileges": "yes", "CapabilityBoundingSet": "", "ProtectClock": "yes",
            "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateTmp": "yes",
            "PrivateDevices": "yes", "PrivateNetwork": "yes", "ReadWritePaths": str(sock),
            "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6 AF_NETLINK", "UMask": "0077",
        }
        started.append(daemon_unit)
        transient(daemon_unit, daemon_properties, [str(stage / "chronyd"), "-x", "-n", "-u", "root", "-f", str(config_path)])
        deadline = time.monotonic() + 15
        while not (sock / "chronyd.sock").exists():
            if time.monotonic() >= deadline or show(daemon_unit).get("ActiveState") == "failed":
                raise RuntimeError("Isolated clock-disabled daemon did not create its socket")
            time.sleep(0.1)
        daemon_info = show(daemon_unit)
        report["daemon_unit"] = selected_properties(daemon_info)
        pid = int(daemon_info["MainPID"])
        status = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in line)
        report["daemon_process"] = {"pid": pid, "cmdline": Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0"),
                                    "capabilities": {k: status[k].strip() for k in ("CapEff", "CapPrm", "CapBnd", "NoNewPrivs", "Seccomp")},
                                    "rtc0_visible": Path(f"/proc/{pid}/root/dev/rtc0").exists()}
        assert "-x" in report["daemon_process"]["cmdline"]
        assert int(status["CapBnd"], 16) == 0 and int(status["CapEff"], 16) == 0
        assert not report["daemon_process"]["rtc0_visible"]
        probe_properties = {**protection, "Type": "oneshot", "RemainAfterExit": "yes", "Restart": "no",
                            "TimeoutStartSec": "150s", "TimeoutStopSec": "10s", "StateDirectory": name,
                            "ReadWritePaths": str(sock)}
        started.append(probe_unit)
        transient(probe_unit, probe_properties,
                  ["/usr/bin/python3", "-B", "-I", str(stage / "probe.py"), "root-probe", str(state), str(sock)])
        deadline = time.monotonic() + 90
        while True:
            info = show(probe_unit)
            if info.get("SubState") == "exited" or info.get("ActiveState") in {"failed", "inactive"}:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Bounded permission probe exceeded 90 seconds")
            time.sleep(0.2)
        report["probe_unit"] = selected_properties(info)
        report["protection_comparison"] = same_protection(info, protection, name, sock)
        if (state / "probe-result.json").exists():
            report["probe"] = json.loads((state / "probe-result.json").read_text())
        assert report.get("probe", {}).get("passed") is True, "Permission probe failed; protection is not weakened"
        assert all(p["equal"] for p in report["protection_comparison"].values())
        assert info.get("ExecMainStatus") == "0" and info.get("Result") == "success"
        report["passed"] = True
    except BaseException as exc:
        report.update(passed=False, error=repr(exc), traceback=traceback.format_exc())
    finally:
        report["cleanup"] = {"errors": []}
        for unit in reversed(started):
            try:
                report.setdefault("journals", {})[unit] = command(["/usr/bin/journalctl", "-u", unit, "--no-pager", "-n", "100", "-o", "short-monotonic"], required=False)
                command(["/usr/bin/systemctl", "stop", unit], required=False)
                command(["/usr/bin/systemctl", "reset-failed", unit], required=False)
                last = show(unit)
                if last.get("ActiveState") not in {"inactive", "failed"} or last.get("MainPID", "0") != "0":
                    raise RuntimeError(f"Test unit still active: {unit}")
            except BaseException as exc:
                report["cleanup"]["errors"].append(repr(exc))
        if not report["cleanup"]["errors"]:
            for p in reversed(created):
                try:
                    assert p.parent in {Path("/run"), Path("/var/lib")} and p.name == name
                    assert not p.is_symlink() and p.stat().st_uid == 0
                    assert json.loads((p / ".preflight-owner.json").read_text()) == {"nonce": nonce}
                    assert shutil.rmtree.avoids_symlink_attacks
                    shutil.rmtree(p)
                except BaseException as exc:
                    report["cleanup"]["errors"].append(repr(exc))
        report["cleanup"]["test_paths_absent"] = not stage.exists() and not state.exists()
        report["cleanup"]["unit_load_states"] = {u: show(u).get("LoadState") for u in started}
        try:
            report["after"] = snapshot(inputs)
            differences = [p for p, v in report["before"]["files"].items() if report["after"]["files"][p] != v]
            before_users = service_map(report["before"]["user_services"]["stdout"])
            after_users = service_map(report["after"]["user_services"]["stdout"])
            configuration = ("UnitFileState", "FragmentPath", "DropInPaths")
            user_changes = [u for u in set(before_users) | set(after_users)
                            if {k: before_users.get(u, {}).get(k) for k in configuration}
                            != {k: after_users.get(u, {}).get(k) for k in configuration}]
            report["preservation"] = {"changed_files": differences, "same_boot": report["before"]["boot_id"] == report["after"]["boot_id"],
                                       "user_service_configuration_unchanged": not user_changes,
                                       "package_inventory_unchanged": report["before"]["packages"]["stdout"] == report["after"]["packages"]["stdout"],
                                       "user_unit_files_unchanged": report["before"]["user_unit_files"]["stdout"] == report["after"]["user_unit_files"]["stdout"],
                                       "system_service_state_unchanged": report["before"]["system_services"]["stdout"] == report["after"]["system_services"]["stdout"]}
            report["collector_observation"] = {u: {"before": before_users.get(u), "after": after_users.get(u),
                                                   "same_invocation": before_users.get(u, {}).get("InvocationID") == after_users.get(u, {}).get("InvocationID")}
                                                for u in ("marketdata-depth.service", "marketdata-aggtrade.service")}
            report["passed"] = bool(report["passed"] and not differences and all(v for k, v in report["preservation"].items() if k != "changed_files")
                                    and not report["cleanup"]["errors"] and report["cleanup"]["test_paths_absent"]
                                    and all(v == "not-found" for v in report["cleanup"]["unit_load_states"].values()))
        except BaseException as exc:
            report["passed"] = False
            report["audit_error"] = repr(exc)
        report["commands"] = list(COMMANDS)
        save_as_user(report, nonce)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = p.parse_args()
    inputs, protection = prepare()
    if args.check:
        print(json.dumps({"pinned_inputs_valid": True, "release_id": inputs["release_id"], "pinned_files": len(inputs["pinned_files"]),
                          "protection": protection, "root_test_executed": False}, indent=2))
    else:
        raise SystemExit(perform(inputs, protection))
