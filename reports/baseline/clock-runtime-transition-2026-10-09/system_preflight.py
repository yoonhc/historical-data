"""Bounded administrator-only TEST service lifecycle check. No operating changes.

Writes only nonce-owned /opt/md-clock-probe-* and /var/lib/md-clock-probe-*.
Starts/stops/kills only its two transient md-clock-probe-* TEST units.
No operating service control, clock control, RTC, packages or approval writes.
"""

import argparse
import hashlib
import json
import os
import pwd
import shutil
import stat
import subprocess
import tarfile
import time
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = []


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(argv, required=True, timeout=30, text=None):
    p = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        input=text,
        timeout=timeout,
        env={**os.environ, "LC_ALL": "C", "SYSTEMD_COLORS": "0"},
        cwd="/",
    )
    item = {"argv": argv, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    LOG.append(item)
    if required and p.returncode:
        raise RuntimeError(item)
    return item


def show(unit):
    return dict(
        line.split("=", 1)
        for line in command(["/usr/bin/systemctl", "show", unit, "--all", "--no-pager"])[
            "stdout"
        ].splitlines()
        if "=" in line
    )


def wait_for(check, seconds=65):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.25)
    raise RuntimeError("Isolated test condition did not complete within deadline")


def record(path):
    p = Path(path)
    if p.is_symlink():
        return {"link": os.readlink(p)}
    if not p.exists():
        return {"missing": True}
    st = p.stat()
    return {"sha256": sha(p), "uid": st.st_uid, "mode": stat.S_IMODE(st.st_mode)}


def snapshot(inputs):
    return {
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "files": {p: record(p) for p in inputs["operating_files"]},
        "chrony": {
            k: show("chrony.service").get(k) for k in ("MainPID", "InvocationID", "ActiveState")
        },
        "collector_units": {
            s: {
                k: show("marketdata-collector@" + s + ".service").get(k)
                for k in ("FragmentPath", "DropInPaths", "ExecStart", "ExecStartPre")
            }
            for s in ("depth", "aggtrade")
        },
    }


def inputs_check():
    p = json.loads((HERE / "system-preflight-inputs.json").read_text())
    for path, expected in p["files"].items():
        if sha(path) != expected:
            raise RuntimeError("Changed reviewed test input: " + path)
    assert p["release_id"] == "clock-evidence-policy-20261009-65296619a374c325"
    assert pwd.getpwnam("yoonhc").pw_uid == 1000
    return p


def save_as_user(name, value):
    path = HERE / name
    code = "import os,sys; p=sys.argv[1]; f=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); b=sys.stdin.buffer.read(); s=os.fdopen(f,'wb'); s.write(b); s.flush(); os.fsync(s.fileno()); s.close()"
    command(
        [
            "/usr/sbin/runuser",
            "-u",
            "yoonhc",
            "--",
            "/usr/bin/python3",
            "-I",
            "-c",
            code,
            str(path),
        ],
        text=json.dumps(value, indent=2) + "\n",
    )


def unpack(inputs, dest):
    archive = Path(inputs["archive"])
    manifest = json.loads(Path(inputs["inventory"]).read_text())
    seen = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            name = member.name
            rel = Path(name)
            assert not rel.is_absolute() and ".." not in rel.parts and name not in seen
            seen.add(name)
            expected = manifest[name]
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            if member.issym():
                assert member.linkname == expected["link"]
                assert (name, member.linkname) in {
                    (".venv/bin/python", "/usr/bin/python3"),
                    (".venv/bin/python3", "python"),
                    (".venv/bin/python3.12", "python"),
                    (".venv/lib64", "lib"),
                }
                target.symlink_to(member.linkname)
            else:
                assert member.isfile() and member.size == expected["bytes"]
                content = tar.extractfile(member).read()
                assert hashlib.sha256(content).hexdigest() == expected["sha256"]
                with target.open("xb") as f:
                    f.write(content)
                target.chmod(expected["mode"] & 0o755)
    assert seen == set(manifest)


def safe_cleanup(path, nonce):
    if not path.exists():
        return
    assert (
        path.parent in (Path("/opt"), Path("/var/lib")) and path.name == "md-clock-probe-" + nonce
    )
    assert not path.is_symlink() and path.stat().st_uid == 0
    assert json.loads((path / ".owner.json").read_text())["nonce"] == nonce
    assert shutil.rmtree.avoids_symlink_attacks
    shutil.rmtree(path)


def run(inputs, scenarios=("graceful", "kill")):
    assert os.geteuid() == 0
    nonce = uuid.uuid4().hex[:16]
    release = Path("/opt") / ("md-clock-probe-" + nonce)
    state = Path("/var/lib") / ("md-clock-probe-" + nonce)
    names = ["md-clock-probe-" + nonce + "-" + s + ".service" for s in scenarios]
    result = {
        "nonce": nonce,
        "passed": False,
        "release_id": inputs["release_id"],
        "scope": "isolated transient system services; synthetic clock/collector payloads",
        "started_at": time.time(),
        "cleanup": {"errors": []},
        "scenarios": {},
    }
    before = snapshot(inputs)
    result["before"] = before
    try:
        for root in (release, state):
            root.mkdir(mode=0o755)
            (root / ".owner.json").write_text(json.dumps({"nonce": nonce}))
        unpack(inputs, release)
        shutil.copyfile(HERE / "system_probe_worker.py", release / "system_probe_worker.py")
        (release / "system_probe_worker.py").chmod(0o644)
        profile = json.loads(Path("/etc/marketdata/runtime.json").read_text())
        profile["release_root"] = str(release)
        profile["dns_hosts"] = ["localhost"]
        profile["mount_uuid"] = command(
            ["/usr/bin/findmnt", "--target", str(state), "--noheadings", "--output", "UUID"]
        )["stdout"].strip()
        assert profile["mount_uuid"]
        (state / "profile.json").write_text(json.dumps(profile))
        for scenario, unit in zip(scenarios, names):
            root = state / scenario
            root.mkdir(mode=0o700)
            os.chown(root, 1000, 1000)
            for folder in ("store", "evidence", "control"):
                p = root / folder
                p.mkdir(mode=0o700)
                os.chown(p, 1000, 1000)
            data_directory = root / "store/data"
            data_directory.mkdir(mode=0o700)
            os.chown(data_directory, 1000, 1000)
            # The same sandbox/lifetime settings; fixture executable, directories
            # and source of clock/bootstrap readiness are intentionally isolated.
            props = {
                **inputs["properties"],
                "User": "yoonhc",
                "WorkingDirectory": str(release),
                "ReadWritePaths": f"{root}/store {root}/evidence {root}/control",
                "Environment": "PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1",
                "StandardOutput": "journal",
                "StandardError": "journal",
            }
            argv = ["/usr/bin/systemd-run", "--quiet", "--unit=" + unit]
            for k, v in props.items():
                argv += ["--property", k + "=" + v]
            argv += [
                "--",
                str(release / ".venv/bin/python"),
                "-B",
                "-I",
                str(release / "system_probe_worker.py"),
                "--root",
                str(state),
                "--scenario",
                scenario,
            ]
            command(argv)
            control = root / "control"
            wait_for(lambda: list(control.glob("child-1-started.json")))
            initial = show(unit)
            expected = {
                "User": "yoonhc",
                "RuntimeMaxUSec": "infinity",
                "Restart": "always",
                "KillMode": "mixed",
                "KillSignal": "2",
                "TimeoutStopUSec": "5min 30s",
                "NoNewPrivileges": "yes",
                "ProtectClock": "yes",
                "ProtectSystem": "strict",
                "ProtectHome": "read-only",
                "CapabilityBoundingSet": "",
                "PrivateTmp": "yes",
            }
            assert all(initial.get(k) == v for k, v in expected.items()), {
                k: initial.get(k) for k in expected
            }
            assert set(initial["RestrictAddressFamilies"].split()) == {
                "AF_INET",
                "AF_INET6",
                "AF_UNIX",
                "AF_NETLINK",
            }
            assert set(initial["ReadWritePaths"].split()) == {
                str(root / p) for p in ("store", "evidence", "control")
            }
            details = {
                "effective": {k: initial.get(k) for k in expected},
                "first_invocation": initial["InvocationID"],
            }
            result["scenarios"][scenario] = details
            if scenario == "graceful":
                wait_for(lambda: (control / "child-2-started.json").exists())
                assert show(unit)["MainPID"] == initial["MainPID"]
                (control / "hold-publication").touch()
                command(["/usr/bin/systemctl", "--no-block", "stop", unit])
                wait_for(lambda: (control / "publisher-waiting.json").exists())
                assert (control / "child-2-saved.json").exists()
                during = show(unit)
                assert (
                    during["ActiveState"] == "deactivating"
                    and during["MainPID"] == initial["MainPID"]
                )
                details["child_saved_before_worker_drain"] = True
                details["supervisor_alive_while_publication_blocked"] = True
                (control / "release-publication").touch()
            else:
                wait_for(
                    lambda: (
                        any((root / "evidence/v2/depth/spool").glob("*.open.jsonl"))
                        or any((root / "evidence/v2/depth/spool").glob("*.jsonl"))
                    )
                )
                before_kill = {
                    str(p.relative_to(root)): sha(p) for p in (root / "evidence").rglob("*.jsonl")
                }
                killed = command(
                    ["/usr/bin/systemctl", "kill", "--kill-whom=all", "--signal=SIGKILL", unit],
                    required=False,
                )
                details["kill_command_result"] = killed
                # systemctl may report an auxiliary-cgroup signal error after
                # already killing main/child. Do not infer success from its RC:
                # require the actual old processes to disappear and a new
                # invocation to start a child, or fail at the bounded deadline.
                old_child = json.loads((control / "child-1-started.json").read_text())["pid"]
                wait_for(
                    lambda: (
                        not Path("/proc/" + initial["MainPID"]).exists()
                        and not Path("/proc/" + str(old_child)).exists()
                    )
                )
                details["old_main_and_child_absent"] = True
                wait_for(lambda: (control / "child-2-started.json").exists(), seconds=85)
                restarted = show(unit)
                assert restarted["InvocationID"] != initial["InvocationID"]
                details["killed_spool"] = before_kill
                details["new_invocation"] = restarted["InvocationID"]
                command(["/usr/bin/systemctl", "--no-block", "stop", unit])
            wait_for(lambda: show(unit)["ActiveState"] == "inactive", seconds=65)
            finished = list(control.glob("supervisor-*-closed.json"))
            assert len(finished) == 1
            closed = json.loads(finished[0].read_text())
            assert (
                not closed["worker_alive"] and closed["pending"] == 0 and not closed["open_spool"]
            ), closed
            assert show(unit)["MainPID"] == "0"
            details["closed"] = closed
            details["control"] = {p.name: json.loads(p.read_text()) for p in control.glob("*.json")}
            # Query every collection using the candidate inside its copied environment.
            code = "import json,sys; from marketdata.store import Store; from marketdata.clock_evidence import query; s=Store(sys.argv[1]); rows=list(s.db.execute('SELECT id,report,state FROM event_collections')); q=[query(s,r['id']) for r in rows]; a=list(s.db.execute(\"SELECT id,path FROM artifacts WHERE kind='runtime_clock_v2'\")); print(json.dumps({'collections':[dict(r) for r in rows],'queries':q,'artifacts':[dict(r) for r in a],'valid':s.validate()})); s.close()"
            check = command(
                [
                    "/usr/sbin/runuser",
                    "-u",
                    "yoonhc",
                    "--",
                    str(release / ".venv/bin/python"),
                    "-B",
                    "-I",
                    "-c",
                    code,
                    str(root / "store"),
                ]
            )
            data = json.loads(check["stdout"])
            assert data["valid"]["ok"]
            assert data["queries"][-1]["evidence_state"] == "available"
            details["data"] = data
            latest = json.loads((root / "evidence/v2/depth/latest.json").read_text())
            assert latest["state"] == "finished"
            assert latest["decision"]["current_estimate_usable"]
            assert not latest["history"]["hard_fault_seen"]
            details["final_quality"] = latest
        result["passed"] = True
    except BaseException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    finally:
        for unit in names:
            try:
                # Explicitly restricted to the two names generated above.
                assert unit.startswith("md-clock-probe-" + nonce + "-")
                result.setdefault("journals", {})[unit] = command(
                    ["/usr/bin/journalctl", "-u", unit, "--no-pager", "-o", "short-monotonic"],
                    required=False,
                )["stdout"]
                command(["/usr/bin/systemctl", "stop", unit], required=False, timeout=360)
                command(["/usr/bin/systemctl", "reset-failed", unit], required=False)
                info = show(unit)
                assert info.get("MainPID", "0") == "0" and info.get("ControlPID", "0") == "0"
            except BaseException as exc:
                result["cleanup"]["errors"].append(str(exc))
        # Preserve fixture diagnostics before removing our test directories,
        # including readiness failures which may never reach the child marker.
        if state.exists():
            result["fixture_diagnostics"] = {
                str(p.relative_to(state)): p.read_text()[: 2 * 1024 * 1024]
                for p in state.rglob("*")
                if p.is_file() and not p.is_symlink() and p.suffix in {".json", ".jsonl"}
            }
        if not result["cleanup"]["errors"]:
            for path in (state, release):
                try:
                    safe_cleanup(path, nonce)
                except BaseException as exc:
                    result["cleanup"]["errors"].append(str(exc))
        result["cleanup"]["paths_absent"] = not state.exists() and not release.exists()
        result["cleanup"]["units"] = {n: show(n).get("LoadState") for n in names}
        after = snapshot(inputs)
        result["after"] = after
        result["operating_unchanged"] = before == after
        result["passed"] &= (
            result["operating_unchanged"]
            and not result["cleanup"]["errors"]
            and result["cleanup"]["paths_absent"]
        )
        result["finished_at"] = time.time()
        result["commands"] = LOG.copy()
        name = "system-preflight-result-" + nonce + ".json"
        save_as_user(name, result)
        print(
            json.dumps(
                {
                    "passed": result["passed"],
                    "report": str(HERE / name),
                    "cleanup": result["cleanup"],
                    "operating_unchanged": result["operating_unchanged"],
                },
                indent=2,
            ),
            flush=True,
        )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--scenario", choices=("all", "graceful", "kill"), default="all")
    args = parser.parse_args()
    inputs = inputs_check()
    if args.run:
        raise SystemExit(
            run(inputs, ("graceful", "kill") if args.scenario == "all" else (args.scenario,))
        )
    print(
        json.dumps(
            {
                "inputs_verified": True,
                "release_id": inputs["release_id"],
                "operating_changes": False,
            }
        )
    )
