"""One reviewed cutover operation; no reboot, bootstrap attempt, or data deletion.

Run --validate without root, then --run with administrator authentication.
Any exception stops the operation and preserves its journal and partial state.
Do not rerun a partial installation without reviewing the recorded phase.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CANDIDATE = HERE.parent / "system-runtime-bootstrap-revision-2026-10-07"
RELEASE_ID = "system-bootstrap-20261007-f85eb8321a19c1ee"
RELEASE = Path("/opt/marketdata/releases") / RELEASE_ID
OPS = Path("/var/lib/marketdata-cutover-20261008")
STATE = Path("/var/lib/marketdata-clock")
USER_UNITS = Path("/home/yoonhc/.config/systemd/user")
GATE = [str(RELEASE / ".venv/bin/python"), "-B", "-I",
        str(RELEASE / "integrations/runtime/clock_gate.py")]
PROFILE = "/etc/marketdata/clock-bootstrap.json"
SYSTEM_MASKS = ["chrony.service", "chronyd.service", "chrony-wait.service"]
KEEP_MASKED = {"marketdata-depth.service", "marketdata-aggtrade.service",
               "marketdata-checkpoint-retirement.service", "marketdata-checkpoint-retirement.timer"}
USER_PREFIX = ["/usr/sbin/runuser", "-u", "yoonhc", "--", "/usr/bin/env",
               "XDG_RUNTIME_DIR=/run/user/1000", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"]
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LC_ALL": "C",
       "DEBIAN_FRONTEND": "noninteractive", "PYTHONDONTWRITEBYTECODE": "1"}
EVENTS = []
PHASE = "preflight"


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def event(kind, **fields):
    item = {"at": datetime.now().astimezone().isoformat(), "phase": PHASE,
            "kind": kind, **fields}
    EVENTS.append(item)
    if OPS.exists():
        with (OPS / "operations.jsonl").open("a") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


def run(args, *, allowed=(0,), timeout=60, cwd=None):
    args = [str(x) for x in args]
    event("command_intent", argv=args)
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                       cwd=cwd, env=ENV)
    event("command_result", argv=args, returncode=p.returncode,
          stdout=p.stdout, stderr=p.stderr)
    if p.returncode not in allowed:
        raise RuntimeError(f"Command failed ({p.returncode}): {args}\n{p.stderr[-3000:]}")
    return p


def properties(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def write_new(path, data, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(path, mode)


def validate():
    plan = json.loads((HERE / "install-inputs.json").read_text())
    for name, expected in plan["input_hashes"].items():
        if digest(name) != expected:
            raise RuntimeError(f"Input changed: {name}")
    release = json.loads((CANDIDATE / "release.json").read_text())
    deployment = json.loads((CANDIDATE / "deployment.json").read_text())
    if (release["release_id"] != RELEASE_ID or
        release["archive_sha256"] != "1960c8b743d377170e0683678dde4078895be2650958ead027f5fd7fe9a4dfa1" or
        deployment["plan_id"] != "1784221fd075bbf7e59e99b521ce038bae2ce99bbbee06dfe4684387a300d78e"):
        raise RuntimeError("Frozen candidate identity mismatch")
    if digest(CANDIDATE / "source.tar.gz") != release["archive_sha256"]:
        raise RuntimeError("Archive changed")
    for name, expected in deployment["files_sha256"].items():
        if digest(CANDIDATE / name) != expected:
            raise RuntimeError(f"Deployment file changed: {name}")
    before = json.loads((HERE / "before.json").read_text())
    for name, meta in before["config_hashes"].items():
        if name != "/etc/adjtime" and digest(name) != meta["sha256"]:
            raise RuntimeError(f"Existing operating configuration changed: {name}")
    if Path("/etc/adjtime").read_text().strip().splitlines()[-1] != "UTC":
        raise RuntimeError("RTC reverted")
    if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != plan["boot_id"]:
        raise RuntimeError("This operation is only for the checked installation boot")
    if len(before["units"]) != 20:
        raise RuntimeError("Unexpected original unit inventory")
    for name, meta in before["units"].items():
        live = USER_UNITS / name
        if not live.is_symlink() or os.readlink(live) != "/dev/null":
            raise RuntimeError(f"Required hold changed: {name}")
        if digest(HERE / "held-user-units" / name) != meta["sha256"]:
            raise RuntimeError(f"Saved unit changed: {name}")
    return plan, release, before


def root_preflight(plan, before):
    if os.geteuid() != 0:
        raise RuntimeError("Administrator authentication required")
    for p in [OPS, RELEASE, STATE, Path("/etc/marketdata")]:
        if p.exists() or p.is_symlink():
            raise RuntimeError(f"Refusing to overwrite existing installation: {p}")
    for name in SYSTEM_MASKS:
        for directory in ["/etc/systemd/system", "/run/systemd/system"]:
            p = Path(directory) / name
            if p.exists() or p.is_symlink():
                raise RuntimeError(f"Existing time-service override/mask requires review: {p}")
        if (Path("/var/lib/systemd/deb-systemd-helper-masked") / name).exists():
            raise RuntimeError("Package-owned mask metadata requires review")
    for p in (CANDIDATE / "units").rglob("*"):
        if p.is_file():
            target = Path("/etc/systemd") / p.relative_to(CANDIDATE / "units")
            if target.exists() or target.is_symlink():
                raise RuntimeError(f"Existing target needs review: {target}")
    out = run(USER_PREFIX + ["systemctl", "--user", "show", *sorted(before["units"]),
                           "-p", "Id", "-p", "LoadState", "-p", "ActiveState",
                           "-p", "MainPID", "-p", "ControlPID"]).stdout
    blocks = [properties(b) for b in out.strip().split("\n\n")]
    if len(blocks) != 20 or any(b.get("LoadState") != "masked" or
        b.get("ActiveState") not in {"inactive", "failed"} or
        b.get("MainPID", "0") != "0" or b.get("ControlPID", "0") != "0" for b in blocks):
        raise RuntimeError("Business jobs are not held")
    p = properties(run(["timedatectl", "show"]).stdout)
    if p.get("LocalRTC") != "no" or p.get("NTPSynchronized") != "yes":
        raise RuntimeError("Current UTC/NTP preconditions failed")
    for path, expected in plan["mounts"].items():
        if run(["findmnt", "-T", path, "-n", "-o", "UUID"]).stdout.strip() != expected:
            raise RuntimeError(f"Mount mismatch: {path}")
    if run(["systemctl", "list-units", "--all", "marketdata*", "--no-legend", "--no-pager"]).stdout.strip():
        raise RuntimeError("Unexpected existing system marketdata unit")
    pkg = run(["dpkg-query", "-W", "-f=${Version}", "systemd-timesyncd"]).stdout.strip()
    if pkg != "255.4-1ubuntu8.16":
        raise RuntimeError("timesyncd version changed")
    simulation = run(["apt-get", "-s", "install", plan["chrony_package"]]).stdout
    actions = [line for line in simulation.splitlines() if line.startswith(("Inst ", "Remv "))]
    if len(actions) != 2 or not any(x.startswith("Remv systemd-timesyncd ") for x in actions) or not any(
            x.startswith("Inst chrony (4.5-1ubuntu4.2 ") for x in actions):
        raise RuntimeError(f"Unexpected package changes: {actions}")


def install(plan, manifest, before):
    global PHASE
    OPS.mkdir(mode=0o755)
    event("operation_started", release_id=RELEASE_ID, boot_id=plan["boot_id"])
    write_new(OPS / "installer.py", Path(__file__).read_bytes())
    write_new(OPS / "inputs.json", (HERE / "install-inputs.json").read_bytes())
    PHASE = "fixed_release"
    print(PHASE, flush=True)
    RELEASE.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    RELEASE.mkdir(mode=0o755)
    with tarfile.open(CANDIDATE / "source.tar.gz") as tar:
        tar.extractall(RELEASE, filter="data")
    for name, expected in manifest["source_files_sha256"].items():
        if digest(RELEASE / name) != expected:
            raise RuntimeError(f"Extracted source differs: {name}")
    write_new(OPS / "uv", Path(plan["uv"]).read_bytes(), 0o755)
    # A private cache copy avoids root modifying the developer's cache.
    shutil.copytree(plan["uv_cache"], OPS / "uv-cache", symlinks=True)
    run([OPS / "uv", "sync", "--frozen", "--no-editable", "--no-dev", "--link-mode", "copy",
         "--offline", "--python", "/usr/bin/python3", "--cache-dir", OPS / "uv-cache"],
        timeout=300, cwd=RELEASE)
    run([RELEASE / ".venv/bin/python", "-B", "-I", RELEASE / "integrations/runtime/seal_runtime.py"])
    receipt = json.loads((RELEASE / "runtime-manifest.json").read_text())
    expected = json.loads((CANDIDATE / "installation-receipt.json").read_text())
    for key in ("python", "distributions", "runtime_files_sha256", "editable", "release_id"):
        if receipt[key] != expected[key]:
            raise RuntimeError(f"Installed receipt differs: {key}")
    for p in [RELEASE, *RELEASE.rglob("*")]:
        if p.is_symlink():
            os.lchown(p, 0, 0)
        else:
            os.chown(p, 0, 0)
            os.chmod(p, stat.S_IMODE(p.stat().st_mode) & ~0o022)
    write_new(OPS / "runtime-manifest.json", (RELEASE / "runtime-manifest.json").read_bytes())
    PHASE = "configuration_and_package"
    print(PHASE, flush=True)
    for source, target in [
        (CANDIDATE / "machine-profile.json", "/etc/marketdata/runtime.json"),
        (CANDIDATE / "boot-profile.json", PROFILE),
        (HERE / "rtc-utc-confirmation.json", "/etc/marketdata/rtc-utc-confirmation.json"),
        (RELEASE / "integrations/runtime/chrony.conf", "/etc/chrony/marketdata.conf")]:
        write_new(target, source.read_bytes())
    for p in (CANDIDATE / "units").rglob("*"):
        if p.is_file():
            write_new(Path("/etc/systemd") / p.relative_to(CANDIDATE / "units"), p.read_bytes())
    run(["systemctl", "mask", *SYSTEM_MASKS])
    run(["systemctl", "daemon-reload"])
    for name in SYSTEM_MASKS:
        if os.readlink(Path("/etc/systemd/system") / name) != "/dev/null":
            raise RuntimeError("Chrony install mask absent")
    for name in ["chrony_package", "timesyncd_rollback_package"]:
        src = Path(plan[name])
        write_new(OPS / src.name, src.read_bytes())
    run(["apt-get", "-y", "--no-download", "-o", "Dpkg::Options::=--force-confold", "install",
         OPS / Path(plan["chrony_package"]).name], timeout=300)
    for name in SYSTEM_MASKS:
        if os.readlink(Path("/etc/systemd/system") / name) != "/dev/null":
            raise RuntimeError("Package altered the temporary mask")
    for name in ("chrony.service", "systemd-timesyncd.service"):
        p = properties(run(["systemctl", "show", name, "-p", "ActiveState", "-p", "MainPID"]).stdout)
        if p.get("ActiveState") not in {"inactive", "failed"} or p.get("MainPID", "0") != "0":
            raise RuntimeError(f"Time daemon unexpectedly running: {name}")
    if run(["dpkg-query", "-W", "-f=${Version}", "chrony"]).stdout.strip() != "4.5-1ubuntu4.2":
        raise RuntimeError("Wrong installed Chrony version")
    for name, expected_digest in plan["chrony_binaries"].items():
        if digest(name) != expected_digest:
            raise RuntimeError(f"Chrony binary changed: {name}")
    run(["/usr/sbin/chronyd", "-p", "-f", "/etc/chrony/marketdata.conf"])
    PHASE = "installation_hold"
    print(PHASE, flush=True)
    STATE.mkdir(mode=0o755)
    run(GATE + ["enroll", "--profile", PROFILE])
    state = json.loads((STATE / "boot-state.json").read_text())
    if state["phase"] != "installation_hold" or state["boot_id"] != plan["boot_id"] or state["step_attempts"] != 0:
        raise RuntimeError("Installation boot was not held")
    run(GATE + ["rtc-check", "--profile", PROFILE])
    # Restore exactly the 8 job/timer pairs; old collectors and retirement stay masked.
    for name, meta in before["units"].items():
        if name in KEEP_MASKED:
            continue
        live = USER_UNITS / name
        if not live.is_symlink() or os.readlink(live) != "/dev/null":
            raise RuntimeError(f"Mask changed during install: {name}")
        tmp = USER_UNITS / (name + ".cutover-new")
        if tmp.exists() or tmp.is_symlink():
            raise RuntimeError(f"Unexpected restore temporary path: {tmp}")
        if meta.get("symlink"):
            os.symlink(meta["symlink"], tmp)
            os.lchown(tmp, meta["uid"], meta["gid"])
        else:
            write_new(tmp, (HERE / "held-user-units" / name).read_bytes(), meta["mode"])
            os.chown(tmp, meta["uid"], meta["gid"])
        os.replace(tmp, live)
        event("user_unit_restored", name=name, sha256=digest(live))
    run(USER_PREFIX + ["systemctl", "--user", "daemon-reload"])
    denied = run(USER_PREFIX + GATE + ["check", "--profile", PROFILE], allowed=(75,))
    if "This boot has not closed initial clock preparation" not in denied.stderr:
        raise RuntimeError("Expected installation hold, not an unrelated gate error")
    run(["systemctl", "unmask", *SYSTEM_MASKS])
    run(["systemctl", "daemon-reload"])
    run(["systemd-analyze", "verify", "chrony.service", "chrony-wait.service",
         "marketdata-clock-bootstrap.service", "marketdata-collector@depth.service",
         "marketdata-collector@aggtrade.service"])
    run(USER_PREFIX + ["systemd-analyze", "--user", "verify", "marketdata-clock-ready.service",
                     *sorted(n for n in before["units"] if n.endswith(".service") and n not in KEEP_MASKED)])
    # Start only the reviewed steady daemon. No bootstrap/makestep in this boot.
    run(["systemctl", "enable", "chrony.service"])
    run(["systemctl", "start", "chrony.service"])
    PHASE = "effective_access_checks"
    print(PHASE, flush=True)
    for name in ("root-access-probe.py", "consumer-access-probe.py"):
        write_new(OPS / name, (HERE / name).read_bytes())
    common = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect",
              "-p", "NoNewPrivileges=yes", "-p", "ProtectClock=yes",
              "-p", "ProtectSystem=strict", "-p", "ProtectHome=read-only", "-p", "PrivateTmp=yes",
              "-p", "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK"]
    run(common + ["--unit=md-cutover-root-access-20261008",
         "-p", "CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_SETUID CAP_SETGID",
         "-p", "ReadWritePaths=/run/chrony", "-p", "StateDirectory=marketdata-clock",
         "-p", "StateDirectoryMode=0755", RELEASE / ".venv/bin/python", "-B", "-I",
         OPS / "root-access-probe.py"])
    evidence = ROOT / "state/runtime-clock"
    if not evidence.exists():
        evidence.mkdir(mode=0o700)
        os.chown(evidence, 1000, 1000)
    run(common + ["--unit=md-cutover-consumer-access-20261008", "-p", "User=yoonhc",
         "-p", "CapabilityBoundingSet=", "-p", f"WorkingDirectory={RELEASE}",
         "-p", f"ReadWritePaths={ROOT} {evidence}", RELEASE / ".venv/bin/python", "-B", "-I",
         OPS / "consumer-access-probe.py"])
    PHASE = "arm_next_boot"
    print(PHASE, flush=True)
    run(["systemctl", "enable", "marketdata-clock-bootstrap.service", "marketdata-collector@depth.service",
         "marketdata-collector@aggtrade.service", "chrony-wait.service"])
    timers = sorted(p.name for p in (HERE / "saved-user-units/timers.target.wants").iterdir()
                    if p.name in before["units"] and p.name not in KEEP_MASKED)
    if len(timers) != 8:
        raise RuntimeError(f"Original enabled timers differ: {timers}")
    run(USER_PREFIX + ["systemctl", "--user", "enable", *timers])
    run(USER_PREFIX + ["systemctl", "--user", "start", *timers])
    run(USER_PREFIX + ["systemctl", "--user", "list-jobs", "--no-pager"])
    run(USER_PREFIX + ["systemctl", "--user", "show", *sorted(before["units"]),
        "marketdata-clock-ready.service", "-p", "Id", "-p", "LoadState", "-p", "ActiveState",
        "-p", "MainPID", "-p", "ControlPID", "-p", "ExecStartPre", "-p", "Requires", "-p", "After"])
    # The same protected audit also recognizes a read-only waiter as safe.
    run(common + ["--unit=md-cutover-root-final-20261008",
         "-p", "CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_SETUID CAP_SETGID",
         "-p", "ReadWritePaths=/run/chrony", "-p", "StateDirectory=marketdata-clock",
         "-p", "StateDirectoryMode=0755", RELEASE / ".venv/bin/python", "-B", "-I",
         OPS / "root-access-probe.py"])
    for name in KEEP_MASKED:
        if not (USER_UNITS / name).is_symlink() or os.readlink(USER_UNITS / name) != "/dev/null":
            raise RuntimeError(f"Persistent exclusion mask lost: {name}")
    for name, meta in before["config_hashes"].items():
        if name != "/etc/adjtime" and digest(name) != meta["sha256"]:
            raise RuntimeError(f"Existing configuration changed: {name}")
    state = json.loads((STATE / "boot-state.json").read_text())
    if state["phase"] != "installation_hold" or state["step_attempts"] != 0:
        raise RuntimeError("Unexpected bootstrap execution during installation")
    run(["systemctl", "show", "chrony.service", "chrony-wait.service", "marketdata-clock-bootstrap.service",
         "marketdata-collector@depth.service", "marketdata-collector@aggtrade.service",
         "-p", "Id", "-p", "ActiveState", "-p", "UnitFileState", "-p", "MainPID", "-p", "ExecStart"])
    run(["/usr/bin/chronyc", "-n", "-c", "-h", "127.0.0.1", "tracking"])
    PHASE = "installed_pending_review_and_final_reboot"


def main():
    args = sys.argv[1:]
    if args not in (["--validate"], ["--run"]):
        raise SystemExit("Use --validate or --run")
    plan, manifest, before = validate()
    if args == ["--validate"]:
        print(json.dumps({"valid": True, "release_id": RELEASE_ID, "inputs": len(plan["input_hashes"]),
                          "reboot": False, "data_deletion": False, "bootstrap_in_installation_boot": False}))
        return
    os.umask(0o022)
    result = {"release_id": RELEASE_ID, "boot_id": plan["boot_id"], "passed": False}
    try:
        root_preflight(plan, before)
        install(plan, manifest, before)
        result.update(passed=True, next="Review installed state, then approved final Linux reboot")
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        event("operation_stopped", error=result["error"])
    result.update(phase=PHASE, finished_at=datetime.now().astimezone().isoformat(),
                  reboot_performed=False, automatic_checkpoint_retirement="held")
    if OPS.exists():
        write_new(OPS / "result.json", (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
