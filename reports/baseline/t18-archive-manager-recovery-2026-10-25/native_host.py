"""Exact T18 native adapter; only called by a sealed, approved operation.

Writes are restricted to five fixed configuration files, recurring authority,
the existing boot ledger, and this operation's root-owned records/release.
Service bodies, clocks, data, command receipts and request files are never written.
"""

import contextlib
import fcntl
import importlib.util
import json
import os
import re
import stat
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


op = load("native_operation", HERE / "operation.py")
c = op.c


def run(argv, timeout=45):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=timeout)
    op.require(
        result.returncode == 0, "Command failed: " + repr(argv) + ": " + result.stderr[-2000:]
    )
    return result.stdout


def safe(path, *, owner=0, exists=True):
    path = Path(path)
    op.require(path.is_absolute() and ".." not in path.parts, "Unsafe native path")
    for parent in (path, *path.parents):
        if not parent.exists():
            op.require(parent == path and not exists, "Missing path parent: " + str(parent))
            continue
        value = parent.lstat()
        op.require(not stat.S_ISLNK(value.st_mode), "Symlink native path: " + str(parent))
        op.require(
            value.st_uid == (owner if parent == path else 0) and not value.st_mode & 0o022,
            "Unsafe ownership/mode: " + str(parent),
        )
    return path


def atomic(path, raw, *, mode=0o644):
    path = safe(path, exists=False)
    temp = path.with_name("." + path.name + ".t18-" + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fchmod(f.fileno(), mode)
        os.fsync(f.fileno())
    os.replace(temp, path)
    c.sync_dir(path.parent)


class Host:
    def __init__(self, plan, directory):
        self.plan, self.p = plan, plan["prepared"]
        self.directory = safe(directory)
        self.release = Path("/opt/marketdata/releases") / self.p["release_id"]
        self.locked = False

    def now(self):
        return datetime.now(UTC).isoformat()

    def record_path(self, name):
        op.require(re.fullmatch(r"[a-z][a-z0-9-]*\.json", name), "Unexpected record name")
        return str(self.directory / name)

    def read(self, name):
        path = safe(self.record_path(name), exists=False)
        return json.loads(path.read_bytes()) if path.exists() else None

    def save(self, name, value, *, immutable=False):
        path = Path(self.record_path(name))
        if immutable and path.exists():
            op.require(self.read(name) == value, "Immutable operation record differs")
            return
        atomic(path, c.encode(value))

    def raw(self, path):
        return safe(path).read_bytes()

    def write(self, path, raw):
        op.require(
            self.locked and path in {*self.p["changes"], c.GRANT, c.LEDGER},
            "Write outside locked configuration scope",
        )
        if self.raw(path) != raw:
            atomic(path, raw)

    def show(self, names):
        names = tuple(names)
        raw = run(
            [
                "/usr/bin/systemctl",
                "show",
                *names,
                "--property=Id,LoadState,ActiveState,SubState,MainPID,ControlPID,Job,InvocationID,FragmentPath,DropInPaths,NeedDaemonReload,ExecStart,ExecStartPre,ExecStartPost,ExecStop,ExecStopPost,ExecCondition,User,WorkingDirectory,ProtectClock,Restart,After,Requires,Unit,UnitFileState,NextElapseUSecRealtime,NextElapseUSecMonotonic,PartOf,BindsTo,PropagatesStopTo,BoundBy,ConsistsOf,RequiredBy,RequisiteOf",
            ]
        )
        values = c.parse_show(raw)
        op.require(set(values) == set(names), "Incomplete manager observation")
        return values

    def timers(self, names):
        return self.show(names)

    def immutable(self):
        op.require(
            Path("/proc/sys/kernel/random/boot_id").read_text().strip() == self.p["boot_id"],
            "Boot changed",
        )
        for path, expected in self.p["protected"].items():
            op.require(c.node(path) == expected, "Protected input differs: " + path)
            if expected["kind"] != "absent":
                op.require(
                    Path(path).lstat().st_uid == self.p["expected_host_uids"][path],
                    "Protected owner differs: " + path,
                )
        for path in (*self.p["changes"], c.GRANT, c.LEDGER):
            value = safe(path).stat()
            op.require(
                stat.S_ISREG(value.st_mode) and stat.S_IMODE(value.st_mode) == 0o644,
                "Configuration metadata differs",
            )
        for name, row in self.show((*op.SERVICES, *op.TIMERS)).items():
            op.require(
                row["LoadState"] == "loaded"
                and not row["DropInPaths"]
                and row["FragmentPath"] == "/etc/systemd/system/" + name,
                "Unexpected loaded entrypoint: " + name,
            )
        expected = self.plan["protected_processes"]
        actual = self.show(c.PROCESS_UNITS)
        for name, identity in expected.items():
            op.require(
                all(actual[name].get(k) == v for k, v in identity.items()),
                "Protected process changed: " + name,
            )

    def stop_timers(self, names):
        op.require(set(names) <= set(op.TIMERS), "Unexpected timer hold")
        # stop is repeatable; no disable, mask, enable-link or body operation.
        for name, row in self.timers(names).items():
            op.require(
                all(
                    key in row and not row[key]
                    for key in (
                        "PartOf",
                        "BindsTo",
                        "PropagatesStopTo",
                        "BoundBy",
                        "ConsistsOf",
                        "RequiredBy",
                        "RequisiteOf",
                    )
                ),
                "Timer stop dependencies differ: " + name,
            )
            if row["ActiveState"] != "inactive" or row.get("Job"):
                run(["/usr/bin/systemctl", "stop", name])
        op.require(
            all(
                v["ActiveState"] == "inactive" and not v["Job"] for v in self.timers(names).values()
            ),
            "Timer hold incomplete",
        )

    def start_timer(self, name):
        op.require(name in op.TIMERS, "Only reviewed timers can start")
        run(["/usr/bin/systemctl", "start", name])

    def idle(self, services, timers=()):
        for name, row in self.show((*services, *timers)).items():
            op.require(
                row["ActiveState"] in {"inactive", "failed"}
                and row.get("MainPID", "0") == "0"
                and row.get("ControlPID", "0") == "0"
                and not row["Job"],
                "Work/job is not idle: " + name,
            )
        # Other exact-plan T05 instances share the runner lock; also reject any
        # live service job before taking it. No instance is stopped or reset.
        if c.T05_SERVICE in services:
            raw_jobs = run(["/usr/bin/systemctl", "list-jobs", "--output=json", "--no-pager"])
            # This host emits an empty successful output when there are no jobs.
            # Non-zero command status still raises in run(); non-empty invalid
            # JSON and malformed rows must never be treated as an empty queue.
            jobs = json.loads(raw_jobs) if raw_jobs.strip() else []
            op.require(
                isinstance(jobs, list)
                and all(isinstance(j, dict) and isinstance(j.get("unit"), str) for j in jobs),
                "Unexpected system job response",
            )
            op.require(
                not any(
                    j.get("unit", "").startswith("marketdata-checkpoint-retirement") for j in jobs
                ),
                "T05 job is still pending",
            )

    def drain(self, services, seconds):
        op.require(not self.locked, "Coordinator must not cover drain")
        deadline = time.monotonic() + seconds
        while True:
            try:
                self.idle(services)
                return
            except op.Hold as exc:
                if time.monotonic() >= deadline:
                    raise op.Hold(
                        "Normal drain pending; timers held, no body forced: " + str(exc)
                    ) from exc
                print(json.dumps({"phase": "normal_drain", "reason": str(exc)}), flush=True)
                time.sleep(5)

    def transition_lock_paths(self):
        paths = [str(c.ROOT / "state/scheduled-clock-policy" / j / "request.lock") for j in c.JOBS]
        paths += [
            "/var/lib/marketdata-checkpoint-retirement/runner/runner.lock",
            "/var/lib/marketdata-clock/coordinator.lock",
        ]
        return paths

    @contextlib.contextmanager
    def transaction_locks(self):
        handles = []
        try:
            for path in self.transition_lock_paths():
                p = Path(path)
                op.require(
                    p.is_file() and not any(x.is_symlink() for x in (p, *p.parents)),
                    "Missing/unsafe existing lock",
                )
                fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
                handles.append(fd)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise op.Hold("Transition lock busy; no bypass: " + str(path)) from exc
            self.locked = True
            yield
        finally:
            self.locked = False
            for fd in reversed(handles):
                os.close(fd)

    def terminal_requests(self):
        op.require(self.locked, "Request inventory requires existing request locks")
        result = {}
        for job in c.JOBS:
            path = c.ROOT / "state/scheduled-clock-policy" / job / "request.json"
            raw = path.read_bytes()
            value = json.loads(raw)
            result[job] = {
                "sha256": c.sha(raw),
                "request_id": value.get("request_id"),
                "state": value.get("state"),
                "identity": value.get("identity"),
            }
            op.require(
                value.get("state") == "complete",
                "Unfinished request requires exact reconciliation: " + job,
            )
        self.save("requests-at-transition.json", result)
        proofs = self.request_proofs()
        op.require(
            set(proofs) == set(result)
            and all(proofs[j]["sha256"] == result[j]["sha256"] for j in result),
            "Request/proof changed during transition",
        )
        self.save("request-proofs-at-transition.json", proofs)

    def request_proofs(self):
        return json.loads(
            run(
                [
                    "/usr/sbin/runuser",
                    "-u",
                    "yoonhc",
                    "--",
                    str(c.PYTHON),
                    "-B",
                    "-I",
                    str(HERE / "verify_requests.py"),
                ]
            )
        )

    def install(self):
        self.immutable()
        self.idle((c.SERVICE,), (c.TIMER,))
        original_request = self.p["archive_request"]
        op.require(
            c.file_sha(original_request["path"]) == original_request["sha256"],
            "Held archiver request changed before install",
        )
        for path, expected in self.p["external_runtime"].items():
            op.require(c.node(path) == expected, "Retained Python/dependency changed: " + path)
        self.save(
            "install-intent.json",
            {"release": str(self.release), "files": self.p["release_entries"]},
            immutable=True,
        )
        if not self.release.exists():
            safe(self.release.parent)
            self.release.mkdir(mode=0o755)
            c.sync_dir(self.release.parent)
        safe(self.release)
        for name, item in self.p["release_entries"].items():
            target = self.release / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            if target.exists():
                safe(target)
                op.require(
                    c.file_sha(target) == item["sha256"]
                    and stat.S_IMODE(target.stat().st_mode) == item["mode"],
                    "Existing release file differs",
                )
            else:
                raw = (HERE / "payload" / name).read_bytes()
                op.require(c.sha(raw) == item["sha256"], "Sealed payload differs")
                atomic(target, raw, mode=item["mode"])
        actual = {
            str(p.relative_to(self.release)) for p in self.release.rglob("*") if not p.is_dir()
        }
        op.require(actual == set(self.p["release_entries"]), "Unexpected release entries")
        for directory in sorted(
            (p for p in self.release.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            c.sync_dir(directory)
        c.sync_dir(self.release)
        c.sync_dir(self.release.parent)
        self.readonly_check()

    def verify_release(self):
        safe(self.release)
        actual = {
            str(p.relative_to(self.release)) for p in self.release.rglob("*") if not p.is_dir()
        }
        op.require(actual == set(self.p["release_entries"]), "Installed release entries differ")
        for name, item in self.p["release_entries"].items():
            path = safe(self.release / name)
            op.require(
                c.file_sha(path) == item["sha256"]
                and stat.S_IMODE(path.stat().st_mode) == item["mode"],
                "Installed source differs: " + name,
            )
        for path, expected in self.p["external_runtime"].items():
            op.require(c.node(path) == expected, "Retained dependency differs: " + path)

    def readonly_check(self, side="after"):
        root = self.release if side == "after" else c.BASE
        if side == "after":
            self.verify_release()
        result = run(
            [
                "/usr/sbin/runuser",
                "-u",
                "yoonhc",
                "--",
                str(c.PYTHON),
                "-B",
                "-I",
                str(root / "policy/run_job.py"),
                "--job",
                "restic-archive",
                "--binding",
                c.BINDING,
                "--check",
            ]
        )
        op.require(json.loads(result)["ready"] is True, "Read-only entrypoint not ready")
        # Verify actual imports at the final path without running an archive.
        script = "import sys,json;from pathlib import Path;p=Path(sys.argv[1]);sys.path[:0]=[str(p/'preserve/packages/marketdata/src'),str(p/'preserve')];from scripts import archive_backups,archive_output;print(json.dumps({m.__name__:str(Path(m.__file__).resolve()) for m in (archive_backups,archive_output)}))"
        if side == "after":
            imports = json.loads(
                run(
                    [
                        "/usr/sbin/runuser",
                        "-u",
                        "yoonhc",
                        "--",
                        str(c.PYTHON),
                        "-B",
                        "-I",
                        "-c",
                        script,
                        str(root),
                    ]
                )
            )
            op.require(
                all(Path(v).is_relative_to(root) for v in imports.values()), "Wrong final import"
            )
            self.save(
                "installed-import.json", {"python": str(c.PYTHON), "imports": imports, "uid": 1000}
            )

    def optional_commands(self, row):
        keys = ("ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "ExecCondition")
        missing = [key for key in keys if key not in row]
        if not missing:
            return {key: row[key] for key in keys}
        # systemctl omits empty command arrays even with --all. Query their
        # actual typed D-Bus values; absence in text is not proof of absence.
        prefix = ["/usr/bin/busctl", "--system", "--json=short"]
        value = json.loads(run(prefix + ["call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "GetUnit", "s", c.SERVICE]))
        op.require(isinstance(value, dict) and value.get("type") == "o" and isinstance(value.get("data"), list) and len(value["data"]) == 1 and isinstance(value["data"][0], str) and value["data"][0].startswith("/org/freedesktop/systemd1/unit/"), "Invalid service object response")
        object_path = value["data"][0]
        result = {key: row[key] for key in keys if key in row}
        for key in missing:
            value = json.loads(run(prefix + ["get-property", "org.freedesktop.systemd1", object_path, "org.freedesktop.systemd1.Service", key]))
            op.require(isinstance(value, dict) and value.get("type") == "a(sasbttttuii)" and isinstance(value.get("data"), list), "Invalid command property: " + key)
            result[key] = value["data"]
        return result

    def reload_and_verify(self, side):
        op.require(self.locked, "Reload only within configuration transition")
        run(["/usr/bin/systemctl", "daemon-reload"])
        self.verify_loaded(side)

    def verify_loaded(self, side):
        row = self.show((c.SERVICE,))[c.SERVICE]
        topology = json.loads(self.p["changes"][c.TOPOLOGY][side]["text"])
        expected = topology["units"][c.SERVICE]
        commands = re.findall(r"argv\[\]=(.*?) ;", row["ExecStart"])
        op.require(commands == expected["ExecStart"], "Effective command differs")
        op.require(
            row["User"] == "yoonhc"
            and row["ProtectClock"] == "yes"
            and row["Restart"] == "no"
            and not row["DropInPaths"]
            and row["NeedDaemonReload"] == "no",
            "Effective protection differs",
        )
        op.require(
            not any(self.optional_commands(row).values()),
            "Unexpected additional command",
        )
        op.require(
            row["WorkingDirectory"] == expected["WorkingDirectory"], "Working directory differs"
        )
        for key in ("After", "Requires"):
            op.require(set(expected[key]).issubset(row[key].split()), "Dependency differs")
        self.idle(op.SERVICES, op.TIMERS)

    def propose(self):
        return json.loads(
            run(
                [
                    "/usr/sbin/runuser",
                    "-u",
                    "yoonhc",
                    "--",
                    str(c.T05 / ".venv/bin/python"),
                    "-B",
                    "-I",
                    str(c.T05 / "scripts/run_clock_retirement.py"),
                    "--settings",
                    "/etc/marketdata/checkpoint-clock-retirement/settings.toml",
                    "propose",
                ]
            )
        )

    def capacity(self):
        v = os.statvfs(c.ROOT)
        available = v.f_bavail * v.f_frsize
        needed = (
            self.p["capacity"]["required_collection_reserve_bytes"]
            + self.p["capacity"]["first_body_bytes"]
            + self.p["capacity"]["largest_temporary_stdout_bytes"]
            + self.plan["additional_resume_headroom_bytes"]
        )
        # Checkpoint content is not reread. New inventory needs a new capacity review.
        paths = sorted(
            str(p)
            for p in Path("/mnt/backup/marketdata-historical/automatic").glob("*/*/backup.json")
        )
        op.require(
            paths == self.plan["checkpoint_manifest_paths"],
            "Checkpoint inventory changed; refresh capacity review",
        )
        result = {
            "checked_at": self.now(),
            "available_bytes": available,
            "required_bytes": needed,
            "body_and_temporary_basis": self.p["capacity"],
            "passed": available > needed,
        }
        self.save("resume-capacity.json", result)
        op.require(result["passed"], "Insufficient operating filesystem headroom")

    def verify_activated(self, grant):
        self.immutable()
        self.verify_release()
        receipt = self.read("apply-receipt.json")
        op.require(
            receipt is not None and json.loads(self.raw(c.LEDGER)) == receipt["ledger_after"],
            "Activated ledger differs",
        )
        op.require(json.loads(self.raw(c.GRANT)) == grant, "Active recurring grant differs")
        op.require(
            all(
                self.raw(path) == sides["after"]["text"].encode()
                for path, sides in self.p["changes"].items()
            ),
            "Activated configuration differs",
        )
        timer = self.timers((c.TIMER,))[c.TIMER]
        op.require(timer["ActiveState"] == "active", "Archiver timer not active")
        self.save(
            "activation-observation.json",
            {"timer": timer, "observed_at": self.now(), "checkpoint_processing_complete": False},
        )
