"""Production boundary: exact paths, read-only probes and timer-only control."""

import contextlib
import fcntl
import json
import os
import stat
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import engine
import installation
from vendor import handoff_common as c

CONFIG = "/etc/marketdata/checkpoint-clock-retirement/"
STATE = "/var/lib/marketdata-checkpoint-retirement"
USER = "/home/yoonhc/.config/systemd/user/"
PROCESSES = c.IDENTITIES


def fingerprint(path):
    path = Path(path)
    st = path.lstat()
    value = {"uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode)}
    if stat.S_ISLNK(st.st_mode):
        return {**value, "kind": "symlink", "target": os.readlink(path)}
    c.require(
        stat.S_ISREG(st.st_mode) and st.st_nlink == 1, "Not a single regular file: " + str(path)
    )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        c.require(
            (os.fstat(f.fileno()).st_ino, os.fstat(f.fileno()).st_dev) == (st.st_ino, st.st_dev),
            "File changed",
        )
        digest = c.sha(f.read())
    return {**value, "kind": "file", "sha256": digest}


def protected_processes(manager):
    return {
        unit: {k: value[k] for k in ("MainPID", "InvocationID", "ActiveState")}
        for unit in PROCESSES
        for value in [manager.show("system", unit)]
    }


class Host:
    def __init__(self, plan, files, tool_dir, *, drain_seconds=600):
        self.p, self.f, self.tool_dir = plan, files, Path(tool_dir)
        self.root = (
            "/var/lib/marketdata-clock/t05-recurring-operations/" + plan["operation_plan_id"]
        )
        self.m = c.Managers(allowed={("system", engine.TIMER)}, record=self.event)
        self.drain_seconds = drain_seconds

    def event(self, value):
        self.f.save(
            self.root + "/events/" + uuid.uuid4().hex + ".json",
            {"at": datetime.now(timezone.utc).isoformat(), **value},
            immutable=True,
        )

    def permissions(self, path):
        try:
            st = Path(path).lstat()
        except FileNotFoundError:
            return
        c.require(st.st_uid == st.st_gid == 0, "Root ownership differs: " + path)
        if not stat.S_ISLNK(st.st_mode):
            c.require(
                stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o644,
                "Mode/type differs: " + path,
            )

    def invariants(self, *, activation):
        # Applying this approval after a different boot/configuration requires
        # a new review. Stopping still accepts the current boot, but changes no
        # files outside the exact recognised operation nodes.
        if activation:
            c.require(c.boot() == self.p["boot_id"], "Boot differs from activation review")
            for path, expected in self.p["protected_files"].items():
                c.require(fingerprint(path) == expected, "Protected configuration differs: " + path)
            c.require(
                protected_processes(self.m) == self.p["protected_processes"],
                "Protected processes changed",
            )
        for path, expected in self.p["preserved_records"].items():
            c.require(fingerprint(path) == expected, "Existing evidence/history changed: " + path)
        for suffix in (".service", ".timer"):
            path = USER + engine.AUTO + suffix
            c.require(
                Path(path).is_symlink() and os.readlink(path) == "/dev/null",
                "User automation unmasked",
            )
            value = self.m.show("user", engine.AUTO + suffix)
            c.require(
                value.get("LoadState") == "masked"
                and value.get("ActiveState") == "inactive"
                and not value.get("Job"),
                "User automation is not idle/masked",
            )
        manual = json.loads(Path(CONFIG + "manual.json").read_bytes())
        c.require(
            manual.get("enabled") is False
            and manual.get("approved_at") is None
            and manual.get("approval_reference") is None,
            "Manual authority changed",
        )

    def current_retirement_units(self):
        rows = self.m.command(
            "system",
            "list-units",
            "--all",
            "--plain",
            "--no-pager",
            "--no-legend",
            engine.AUTO + "*.service",
        ).stdout
        names = {engine.SERVICE, engine.AUTO + "-preview.service"}
        for row in rows.splitlines():
            fields = row.split()
            if fields and fields[0] == "●":
                fields = fields[1:]
            c.require(
                fields and fields[0].startswith(engine.AUTO) and fields[0].endswith(".service"),
                "Unexpected retirement unit row",
            )
            names.add(fields[0])
        jobs = self.m.jobs("system")
        names.update(
            j["unit"]
            for j in jobs
            if j["unit"].startswith(engine.AUTO) and j["unit"].endswith(".service")
        )
        return {n: c.classify(self.m.show("system", n), jobs) for n in sorted(names)}

    def require_idle(self):
        states = self.current_retirement_units()
        c.require(
            all(v == "idle" for v in states.values()),
            "Retirement service/job not idle: " + repr(states),
        )

    def require_timer_idle(self):
        p = self.m.show("system", engine.TIMER)
        c.require(c.classify(p, self.m.jobs("system")) == "idle", "Timer is not idle")

    def drain(self):
        deadline = time.monotonic() + self.drain_seconds
        while True:
            states = self.current_retirement_units()
            if all(v == "idle" for v in states.values()):
                return
            c.require(
                time.monotonic() < deadline,
                "Drain timeout; timer stopped, executing work untouched; resume later",
            )
            print(
                json.dumps({"phase": "waiting_for_retirement_idle", "states": states}), flush=True
            )
            time.sleep(min(10, max(0, deadline - time.monotonic())))

    @contextlib.contextmanager
    def configuration_lock(self):
        # Shared coordinator permits normal backup completion. Neither this
        # nor runner EX is held while waiting for a service to finish.
        paths = self.lock_metadata()
        with acquire_locks(paths):
            yield

    def lock_metadata(self):
        paths = [
            ("/var/lib/marketdata-clock/coordinator.lock", fcntl.LOCK_SH),
            (CONFIG.rstrip("/"), fcntl.LOCK_EX),
            (STATE + "/runner/runner.lock", fcntl.LOCK_EX),
        ]
        for index, (path, _) in enumerate(paths):
            st = os.stat(path, follow_symlinks=False)
            c.require(
                st.st_uid == (1000 if index == 2 else 0)
                and not st.st_mode & 0o022
                and (
                    stat.S_ISDIR(st.st_mode)
                    if index == 1
                    else stat.S_ISREG(st.st_mode) and st.st_nlink == 1
                ),
                "Lock ownership/type differs: " + path,
            )
        return paths

    def install(self):
        return installation.install(
            self.p["release_path"], self.p["release_entries"], self.p["release_id"]
        )

    def verify_release(self):
        return installation.verify(
            self.p["release_path"], self.p["release_entries"], self.p["release_id"]
        )

    def inspect(self, mode):
        release = Path(self.p["release_path"])
        # Partial/new installations cannot be imported. The old root-owned
        # release can conservatively inspect the pre-installation history.
        state_path = self.root + "/state.json"
        completed = (
            self.f.node(state_path)["kind"] != "absent"
            and "install" in self.f.json(state_path)["done"]
        )
        if mode == "unfinished" and not completed:
            release = Path(self.p["prior_release_path"])
        argv = c.user_command(
            [
                str(release / ".venv/bin/python"),
                "-B",
                "-I",
                str(self.tool_dir / "inspect_installed.py"),
                "--release",
                str(release),
                "--settings",
                CONFIG + "settings.toml",
                "--mode",
                mode,
            ]
        )
        r = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            timeout=45,
            env={**os.environ, "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        c.require(r.returncode == 0, "UID 1000 inspection failed: " + r.stderr[-4000:])
        value = json.loads(r.stdout)
        c.require(
            value["uid"] == 1000
            and value["prefix"] == str(release / ".venv")
            and value["executable"] == str(release / ".venv/bin/python"),
            "Final interpreter differs",
        )
        return value

    def import_check(self):
        return self.inspect("import")

    def unfinished(self, review_only=False):
        value = self.inspect("unfinished")["unfinished"]
        c.require(
            review_only or not value,
            "Unfinished retirement; explicit review required: " + repr(value),
        )
        return value

    def effective(self):
        service, timer = (self.m.show("system", n) for n in (engine.SERVICE, engine.TIMER))
        for unit, p in ((engine.SERVICE, service), (engine.TIMER, timer)):
            c.require(
                p.get("FragmentPath") == engine.SYSTEM + unit
                and p.get("DropInPaths") == ""
                and p.get("NeedDaemonReload") == "no",
                "Effective fragment differs",
            )
        c.require(
            service.get("User") == service.get("Group") == "yoonhc"
            and service.get("Restart") == "no"
            and service.get("ProtectClock") == "yes",
            "Service safeguards differ",
        )
        c.require(
            timer.get("Unit") == engine.SERVICE
            and timer.get("Persistent") == "no"
            and not timer.get("TimersMonotonic")
            and timer.get("AccuracyUSec") == "1s",
            "Timer semantics differ",
        )
        return {"service": service, "timer": timer}

    def probe(self):
        self.effective()
        return self.inspect("probe")

    def reload(self):
        self.m.command("system", "daemon-reload")

    def start_timer(self):
        self.require_timer_idle()
        self.m.command("system", "start", engine.TIMER)
        return self.verify_active()

    def verify_active(self):
        value = self.effective()
        c.require(
            value["timer"]["ActiveState"] == "active"
            and value["timer"]["SubState"] == "waiting"
            and not value["timer"].get("Job"),
            "Timer start outcome unknown; do not repeat start",
        )
        # Numeric D-Bus value avoids locale-dependent systemctl timestamps.
        object_path = "/org/freedesktop/systemd1/unit/" + "".join(
            c if c.isalnum() else "_" + format(ord(c), "02x") for c in engine.TIMER
        )
        r = subprocess.run(
            [
                "/usr/bin/busctl",
                "--system",
                "--json=short",
                "get-property",
                "org.freedesktop.systemd1",
                object_path,
                "org.freedesktop.systemd1.Timer",
                "NextElapseUSecRealtime",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        raw = json.loads(r.stdout)
        c.require(raw["type"] == "t" and type(raw["data"]) is int, "Unexpected timer timestamp")
        usec = raw["data"]
        stamp = datetime.fromtimestamp(usec / 1e6, timezone.utc)
        c.require((stamp.hour, stamp.minute, stamp.second) == (6, 40, 0), "Next calendar differs")
        c.require(0 < usec / 1e6 - time.time() <= 86460, "Next calendar is not in the coming day")
        return {
            "next_elapse_usec": usec,
            "next_utc": stamp.isoformat(),
            "timer_state": value["timer"]["ActiveState"],
        }

    def stop_timer(self):
        p = self.m.show("system", engine.TIMER)
        if c.classify(p, self.m.jobs("system")) != "idle":
            self.m.command("system", "stop", engine.TIMER)
        self.require_timer_idle()

    def verify_closed(self):
        self.require_timer_idle()
        self.require_idle()
        for unit in (engine.SERVICE, engine.TIMER):
            c.require(
                self.f.node(engine.SYSTEM + unit) == engine.MASK
                and self.m.show("system", unit)["LoadState"] == "masked",
                "System mask not restored",
            )
        grant = json.loads(Path(engine.GRANT).read_bytes())
        c.require(
            grant["enabled"] is False
            and grant.get("approved_at") is None
            and grant.get("approval_reference") is None,
            "Grant not revoked",
        )


@contextlib.contextmanager
def acquire_locks(paths):
    with contextlib.ExitStack() as stack:
        held = []
        for path, mode in paths:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            stack.callback(os.close, fd)
            st = os.fstat(fd)
            c.require(stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode), "Unsafe lock node")
            try:
                fcntl.flock(fd, mode | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise c.Hold("Lock contended; no override: " + str(path)) from exc
            held.append((path, st.st_dev, st.st_ino))
        yield
        for path, dev, ino in held:
            current = os.stat(path, follow_symlinks=False)
            c.require((current.st_dev, current.st_ino) == (dev, ino), "Lock inode changed")


@contextlib.contextmanager
def operation_lock():
    path = Path("/var/lib/marketdata-clock/t05-recurring-operation.lock")
    reader = c.SafeFiles("/", (), {})
    with reader.parent(str(path)) as (parent, leaf):
        fd = os.open(leaf, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        st = os.fstat(fd)
        c.require(
            stat.S_ISREG(st.st_mode)
            and st.st_nlink == 1
            and st.st_uid == st.st_gid == 0
            and stat.S_IMODE(st.st_mode) == 0o600,
            "Unsafe operation lock",
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise c.Hold("Another recurring operation is active") from exc
        yield
    finally:
        os.close(fd)
