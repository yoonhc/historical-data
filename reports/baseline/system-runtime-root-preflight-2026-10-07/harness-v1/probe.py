"""Permission probe only. No bootstrap/check/step/RTC/operating-ledger calls."""
import ctypes
import errno
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import traceback


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("frozen_clock_gate", HERE / "clock_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def process_status():
    values = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    return {k: values[k].strip() for k in ("Uid", "Gid", "CapEff", "CapPrm", "CapBnd", "NoNewPrivs", "Seccomp")}


def run(argv):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=15, env={**os.environ, "LC_ALL": "C"})
    return {"argv": argv, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def as_user(*args):
    return ["/usr/sbin/runuser", "-u", "yoonhc", "--", *args]


def user_lock(state, expected):
    assert os.getuid() == os.geteuid() == 1000
    outcome = {"process": process_status(), "expected": expected}
    ledger = gate.Ledger(state)
    try:
        with ledger.lock(shared=True):
            outcome.update(acquired=True, value=ledger.read())
    except gate.Hold:
        outcome["acquired"] = False
    assert outcome["acquired"] == (expected == "acquired"), outcome
    denied = {}
    for name in ("boot-state.json", "coordinator.lock"):
        try:
            fd = os.open(state / name, os.O_WRONLY | os.O_NOFOLLOW)
        except OSError as exc:
            denied[name] = exc.errno == errno.EACCES
        else:
            os.close(fd)
            denied[name] = False
    assert all(denied.values()), denied
    outcome["write_open_denied"] = denied
    print(json.dumps(outcome, sort_keys=True))


def socket_probe(socket_dir, report):
    lib = ctypes.CDLL(None, use_errno=True)
    lib.inotify_init1.argtypes = [ctypes.c_int]
    lib.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    fd = lib.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1")
    try:
        if lib.inotify_add_watch(fd, os.fsencode(socket_dir), 0x100 | 0x200) < 0:
            raise OSError(ctypes.get_errno(), "inotify_add_watch")
        command = [str(HERE / "chronyc"), "-n", "-h", str(socket_dir / "chronyd.sock"), "-m", "retries 0", "tracking"]
        p = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, "LC_ALL": "C"})
        try:
            stdout, stderr = p.communicate(timeout=15)
        except BaseException:
            p.kill()
            p.communicate()
            raise
        events = []
        while True:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                break
            offset = 0
            while offset < len(data):
                _, mask, _, length = struct.unpack_from("iIII", data, offset)
                name = data[offset + 16:offset + 16 + length].split(b"\0", 1)[0].decode()
                events.append({"name": name, "mask": mask})
                offset += 16 + length
        expected = f"chronyc.{p.pid}.sock"
        result = {"argv": command, "pid": p.pid, "returncode": p.returncode, "stdout": stdout, "stderr": stderr, "inotify": events,
                  "client_reply_created": any(e["name"] == expected and e["mask"] & 0x100 for e in events),
                  "client_reply_deleted": any(e["name"] == expected and e["mask"] & 0x200 for e in events),
                  "client_reply_absent_after": not (socket_dir / expected).exists(),
                  "remaining_clients": [p.name for p in socket_dir.glob("chronyc.*.sock")],
                  "time_synchronization_test": False}
        report["chrony_socket"] = result
        assert result["returncode"] == 0 and "Reference ID" in stdout and "Leap status" in stdout, result
        assert result["client_reply_created"] and result["client_reply_deleted"] and result["client_reply_absent_after"] and not result["remaining_clients"], result
    finally:
        os.close(fd)


def state_probe(state, report):
    ledger = gate.Ledger(state)
    calls = []
    real_fsync = os.fsync

    def observed_fsync(fd):
        kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        result = real_fsync(fd)
        calls.append({"kind": kind, "target": os.readlink(f"/proc/self/fd/{fd}"), "returned": True})
        return result

    value = {"schema": 1, "phase": "installation_hold", "boot_id": "permission-probe-only",
             "profile_sha256": "not-an-operating-profile", "first_boottime": 0, "step_attempts": 0}
    user_command = as_user("/usr/bin/python3", "-B", "-I", str(HERE / "probe.py"), "user-lock", str(state))
    with ledger.lock():
        gate.rt.os.fsync = observed_fsync
        try:
            ledger.save(value)
        finally:
            gate.rt.os.fsync = real_fsync
        blocked = run([*user_command, "blocked"])
    with ledger.lock(shared=True):
        acquired = run([*user_command, "acquired"])
    report["state_and_lock"] = {"real_fsync_calls": calls, "root_exclusive_user_shared": blocked,
                                "root_shared_user_shared": acquired,
                                "files": {p.name: {"uid": p.stat().st_uid, "gid": p.stat().st_gid, "mode": oct(stat.S_IMODE(p.stat().st_mode))}
                                          for p in (ledger.path, state / "coordinator.lock")}}
    assert {c["kind"] for c in calls} == {"file", "directory"}, calls
    assert blocked["returncode"] == acquired["returncode"] == 0, report["state_and_lock"]
    assert json.loads(blocked["stdout"])["acquired"] is False
    assert json.loads(acquired["stdout"])["acquired"] is True
    assert ledger.read() == value
    assert all(v == {"uid": 0, "gid": 0, "mode": "0o644"} for v in report["state_and_lock"]["files"].values())


def root_probe(state, socket_dir):
    report = {"schema": 1, "passed": False, "process": process_status(), "checks": {}}
    try:
        assert os.getuid() == os.geteuid() == 0
        assert report["process"]["NoNewPrivs"] == "1"
        expected_caps = (1 << 1) | (1 << 6) | (1 << 7)
        assert int(report["process"]["CapBnd"], 16) == expected_caps, report["process"]
        assert not int(report["process"]["CapEff"], 16) & (1 << 25)
        assert Path(os.environ["STATE_DIRECTORY"]) == state
        gate.trusted(state, directory=True)
        for name, check in (
            ("chrony_socket", lambda: socket_probe(socket_dir, report)),
            ("user_manager", lambda: manager_probe(report)),
            ("state_and_lock", lambda: state_probe(state, report)),
        ):
            try:
                check()
                report["checks"][name] = {"passed": True}
            except Exception as exc:
                report["checks"][name] = {"passed": False, "error": repr(exc), "traceback": traceback.format_exc()}
        report["passed"] = all(v["passed"] for v in report["checks"].values())
    except Exception as exc:
        report["fatal_error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
    finally:
        gate.rt.atomic_json(state / "probe-result.json", report)
        print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["passed"] else 1


def manager_probe(report):
    host = gate.Host({"user": "yoonhc", "uid": 1000}, {}, runner=gate.rt.command)
    # Exactly the frozen candidate's runuser/environment path, read-only verbs.
    report["user_manager"] = {
        "show": host.user_command("show", "--property=Version", "--property=SystemState"),
        "jobs": host.user_command("list-jobs", "--no-pager", "--no-legend"),
        "collector": host.user_command("show", "marketdata-depth.service", "--property=Id", "--property=LoadState", "--property=MainPID", "--property=InvocationID"),
    }
    assert "Version=" in report["user_manager"]["show"]
    assert "LoadState=loaded" in report["user_manager"]["collector"]


if __name__ == "__main__":
    if sys.argv[1] == "user-lock":
        user_lock(Path(sys.argv[2]), sys.argv[3])
    elif sys.argv[1] == "root-probe":
        raise SystemExit(root_probe(Path(sys.argv[2]), Path(sys.argv[3])))
    else:
        raise SystemExit("Unknown permission probe mode")
