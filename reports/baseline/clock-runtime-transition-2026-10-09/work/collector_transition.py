"""Explicit collector runtime/profile/unit promotion. The collector archive stays frozen.

No service control, step, RTC write, enrollment, or deletion command exists here.
The unchanged deployed gate rejects schema 2 while a transaction is pending.
The original closed clock state is preserved inside that envelope. Only after
activation verification is a schema-1 state committed with a linked receipt.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import time
import uuid
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "clock_gate", Path(__file__).with_name("clock_gate.py")
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
rt = gate.rt
Hold = gate.Hold
ROLES = ("collector_unit", "runtime", "bootstrap")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"


def atomic(path, blob, mode=0o644):
    path = Path(path)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as out:
            out.write(blob)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
        sync_dir(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def immutable(path, value):
    blob = encode(value)
    path = Path(path)
    if path.exists():
        if path.read_bytes() != blob:
            raise Hold(f"Immutable transition evidence differs: {path}")
    else:
        atomic(path, blob)


def tool_hashes():
    return {
        n: rt.digest(Path(__file__).with_name(n))
        for n in (
            "collector_transition.py",
            "clock_gate.py",
            "marketdata_runtime.py",
            "render_units.py",
            "release-pins.json",
        )
    }


def pins():
    return json.loads(Path(__file__).with_name("release-pins.json").read_text())


def expected_unit(before, old_root, new_root, boot_profile):
    old_run = (
        f"{old_root}/.venv/bin/python -I {old_root}/integrations/runtime/marketdata_runtime.py run"
    )
    new_run = f"{new_root}/.venv/bin/python -I {new_root}/integrations/runtime/capture_runtime.py"
    # Exact reviewed delta, retaining every other protection and dependency.
    if before.count("TimeoutStopSec=150s\n") != 1 or before.count("ExecStart=" + old_run) != 1:
        raise Hold("Old collector unit no longer matches the reviewed boundary")
    new = before.replace(old_root, new_root).replace(
        "TimeoutStopSec=150s\n", "TimeoutStopSec=330s\n"
    )
    replaced = old_run.replace(old_root, new_root)
    new = new.replace("ExecStart=" + replaced, "ExecStart=" + new_run)
    lines = new.splitlines(True)
    return "".join(
        line.rstrip("\n") + " --bootstrap-profile " + boot_profile + "\n"
        if line.startswith("ExecStart=")
        else line
        for line in lines
    )


def validate_plan(plan):
    if plan.get("schema") != 1 or plan.get("kind") != "collector_runtime_transition":
        raise Hold("Unknown plan schema/scope; source-change approvals are not applicable")
    if plan.get("recovery_policy") != "same_boot_only_other_boot_hold_for_review":
        raise Hold("Explicit same-boot recovery boundary required")
    if plan.get("plan_id") != gate.canonical_hash(
        {k: v for k, v in plan.items() if k != "plan_id"}
    ):
        raise Hold("Plan content/ID mismatch")
    if plan["tools_sha256"] != tool_hashes() or plan["release_pins"] != pins():
        raise Hold("Plan belongs to different tools/releases")
    if set(plan["files"]) != set(ROLES):
        raise Hold("Unexpected transition file set")
    for f in plan["files"].values():
        if not re.fullmatch(r"/[A-Za-z0-9_./@-]+", f["path"]) or ".." in Path(f["path"]).parts:
            raise Hold("Unsafe target path")
        if f["mode"] != 0o644 or f["uid"] != 0:
            raise Hold("Root-owned 0644 files required")
        for side in ("before", "after"):
            if sha(f[side].encode()) != f[side + "_sha256"]:
                raise Hold("File content/hash mismatch")
    files = plan["files"]
    before_r = json.loads(files["runtime"]["before"])
    after_r = json.loads(files["runtime"]["after"])
    new_root = "/opt/marketdata/releases/" + pins()["after"]["release_id"]
    old_root = "/opt/marketdata/releases/" + pins()["before"]["release_id"]
    if before_r["release_root"] != old_root or after_r != {**before_r, "release_root": new_root}:
        raise Hold("Only the pinned collector release root may change")
    if files["collector_unit"]["after"] != expected_unit(
        files["collector_unit"]["before"], old_root, new_root, files["bootstrap"]["path"]
    ):
        raise Hold("Collector unit delta exceeds the reviewed scope")
    before_b = json.loads(files["bootstrap"]["before"])
    after_b = json.loads(files["bootstrap"]["after"])
    expected_b = json.loads(json.dumps(before_b))
    expected_b["runtime_profile_sha256"] = files["runtime"]["after_sha256"]
    unit = files["collector_unit"]["path"]
    if before_b["guard_files"].get(unit) != files["collector_unit"]["before_sha256"]:
        raise Hold("Old unit guard binding mismatch")
    expected_b["guard_files"][unit] = files["collector_unit"]["after_sha256"]
    if (
        after_b != expected_b
        or before_b["runtime_profile_sha256"] != files["runtime"]["before_sha256"]
    ):
        raise Hold("Bootstrap delta exceeds unit/runtime binding")
    if (
        before_b["runtime_profile"] != files["runtime"]["path"]
        or before_b["state_dir"] != plan["state_dir"]
    ):
        raise Hold("File targets and profile paths differ")
    original = plan["ledger_before"]
    if original.get("schema") != 1 or original.get("phase") != "closed":
        raise Hold("Completed closed boot required")
    if original.get("boot_id") != plan["boot_id"] or original.get(
        "profile_sha256"
    ) != gate.canonical_hash(before_b):
        raise Hold("Old ledger/profile/boot mismatch")
    if not original.get("closed_observation", {}).get("ready") or original.get(
        "step_attempts"
    ) not in (0, 1):
        raise Hold("Missing initial preparation evidence")
    if plan["ledger_before_sha256"] != gate.canonical_hash(original):
        raise Hold("Ledger snapshot hash mismatch")
    return before_b, before_r, after_b, after_r


def make_plan(profile):
    p, r = gate.read_profile(profile)
    ledger = gate.Ledger(p["state_dir"])
    with ledger.lock(shared=True):
        state = ledger.read()
        if state["boot_id"] != rt.boot_id() or state["phase"] != "closed":
            raise Hold("Current boot must be closed")
        paths = {
            "collector_unit": "/etc/systemd/system/marketdata-collector@.service",
            "runtime": p["runtime_profile"],
            "bootstrap": str(Path(profile)),
        }
        files = {}
        for role, path in paths.items():
            gate.trusted(path)
            q = Path(path)
            st = q.stat()
            files[role] = {
                "path": path,
                "uid": st.st_uid,
                "mode": stat.S_IMODE(st.st_mode),
                "before": q.read_text(),
            }
        new_r = {**r, "release_root": "/opt/marketdata/releases/" + pins()["after"]["release_id"]}
        new_unit = expected_unit(
            files["collector_unit"]["before"],
            r["release_root"],
            new_r["release_root"],
            paths["bootstrap"],
        )
        new_b = json.loads(json.dumps(p))
        new_b["runtime_profile_sha256"] = sha(encode(new_r))
        new_b["guard_files"][paths["collector_unit"]] = sha(new_unit.encode())
        after = {
            "collector_unit": new_unit,
            "runtime": encode(new_r).decode(),
            "bootstrap": encode(new_b).decode(),
        }
        for role, f in files.items():
            f["after"] = after[role]
            for side in ("before", "after"):
                f[side + "_sha256"] = sha(f[side].encode())
        value = {
            "schema": 1,
            "kind": "collector_runtime_transition",
            "files": files,
            "recovery_policy": "same_boot_only_other_boot_hold_for_review",
            "boot_id": state["boot_id"],
            "state_dir": p["state_dir"],
            "ledger_before": state,
            "ledger_before_sha256": gate.canonical_hash(state),
            "tools_sha256": tool_hashes(),
            "release_pins": pins(),
            "prepared_sample": rt.stamp(),
        }
        value["plan_id"] = gate.canonical_hash(value)
        validate_plan(value)
        return value


def check_release(runtime, side):
    root = Path(runtime["release_root"])
    expected = pins()[side]
    gate.trusted(root, directory=True)
    if rt.digest(root / "candidate.json") != expected["candidate_json_sha256"]:
        raise Hold("Release metadata differs")
    for name, digest in expected["source_files_sha256"].items():
        if rt.digest(root / name) != digest:
            raise Hold("Release source changed: " + name)
    manifest = json.loads((root / "runtime-manifest.json").read_text())
    if (
        manifest.get("release_id") != expected["release_id"]
        or manifest.get("editable") is not False
    ):
        raise Hold("Invalid sealed installation receipt")
    reviewed = {
        name
        for name in expected["source_files_sha256"]
        if name.endswith(".py")
        and (
            name.startswith("packages/marketdata/src/marketdata/")
            or name.startswith("integrations/runtime/")
        )
    }
    installed = set()
    for name, digest in manifest["runtime_files_sha256"].items():
        p = root / name
        if not p.resolve().is_relative_to(root) or rt.digest(p) != digest:
            raise Hold("Installed release file changed")
        source = name
        if "/site-packages/marketdata/" in name:
            source = (
                "packages/marketdata/src/marketdata/"
                + name.split("/site-packages/marketdata/", 1)[1]
            )
        if expected["source_files_sha256"].get(source) != digest:
            raise Hold("Installed module differs from reviewed source")
        if source in installed:
            raise Hold("Duplicate installed module")
        installed.add(source)
    if installed != reviewed:
        raise Hold("Sealed installation receipt omits reviewed modules")
    return manifest


class LiveHost:
    def __init__(self, plan):
        self.plan = plan
        self.before_p, self.before_r, self.after_p, self.after_r = validate_plan(plan)

    def audit(self):
        # While pending, allow ONLY the two pinned guard-file byte versions.
        p = json.loads(json.dumps(self.before_p))
        f = self.plan["files"]["collector_unit"]
        actual = rt.digest(f["path"])
        if actual not in (f["before_sha256"], f["after_sha256"]):
            raise Hold("Collector guard changed unexpectedly")
        p["guard_files"][f["path"]] = actual
        gate.Host(p, self.before_r).audit_quiescent()
        f = self.unit("marketdata-clock-bootstrap.service")
        if f.get("MainPID") != "0" or f.get("ControlPID") != "0":
            raise Hold("Bootstrap must not execute during transition")
        if rt.digest(self.before_r["chrony_config"]) != self.before_r["chrony_config_sha256"]:
            raise Hold("Chrony configuration changed outside this plan")

    def unit(self, name):
        raw = rt.command(
            [
                "/usr/bin/systemctl",
                "show",
                name,
                "--no-pager",
                "--property=ActiveState,MainPID,ControlPID,LoadState,NeedDaemonReload,FragmentPath,DropInPaths,"
                "ExecStart,ExecStartPre,User,WorkingDirectory,RuntimeMaxUSec,Restart,KillMode,KillSignal,TimeoutStopUSec,"
                "NoNewPrivileges,ProtectClock,ProtectSystem,ProtectHome,ReadWritePaths,CapabilityBoundingSet,PrivateTmp,RestrictAddressFamilies",
            ]
        )
        return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)

    def snapshot(self):
        return {"provider": rt.provider_identity()}

    def activation(self, record, direction):
        side = "after" if direction == "apply" else "before"
        p = self.after_p if side == "after" else self.before_p
        r = self.after_r if side == "after" else self.before_r
        provider = self.snapshot()
        if provider != record["baseline_provider"]:
            raise Hold("Provider changed; this runtime-only transition does not adopt a restart")
        manifest = check_release(r, side)
        units = {}
        for name in p["system_collectors"]:
            u = self.unit(name)
            stream = name.split("@", 1)[1].split(".", 1)[0]
            script = "capture_runtime.py" if side == "after" else "marketdata_runtime.py"
            root = r["release_root"]
            command = f"{root}/.venv/bin/python -I {root}/integrations/runtime/{script}"
            command += (
                "" if side == "after" else " run"
            ) + f" --profile {p['runtime_profile']} --stream {stream}"
            if side == "after":
                command += " --bootstrap-profile " + self.plan["files"]["bootstrap"]["path"]
            if (
                u.get("LoadState") != "loaded"
                or u.get("NeedDaemonReload") != "no"
                or u.get("DropInPaths")
                or u.get("FragmentPath") != self.plan["files"]["collector_unit"]["path"]
                or f"argv[]={command} ;" not in u.get("ExecStart", "")
                or u.get("ExecStart", "").count("argv[]=") != 1
            ):
                raise Hold("Effective collector command/unit not activated")
            expected = {
                "User": p["user"],
                "WorkingDirectory": root,
                "RuntimeMaxUSec": "infinity",
                "Restart": "always",
                "KillMode": "mixed",
                "KillSignal": "2",
                "TimeoutStopUSec": "5min 30s" if side == "after" else "2min 30s",
                "NoNewPrivileges": "yes",
                "ProtectClock": "yes",
                "ProtectSystem": "strict",
                "ProtectHome": "read-only",
                "CapabilityBoundingSet": "",
                "PrivateTmp": "yes",
            }
            if any(u.get(k) != v for k, v in expected.items()):
                raise Hold("Effective collector protection/lifetime differs")
            if set(u.get("ReadWritePaths", "").split()) != {r["data_root"], r["evidence_root"]}:
                raise Hold("Effective writable paths differ")
            if set(u.get("RestrictAddressFamilies", "").split()) != {
                "AF_INET",
                "AF_INET6",
                "AF_UNIX",
                "AF_NETLINK",
            }:
                raise Hold("Address family protections differ")
            gate_cmd = (
                f"{root}/.venv/bin/python -I {root}/integrations/runtime/clock_gate.py check --profile "
                + self.plan["files"]["bootstrap"]["path"]
            )
            ready_cmd = f"{root}/.venv/bin/python -I {root}/integrations/runtime/marketdata_runtime.py wait-ready --profile {p['runtime_profile']} --stream {stream}"
            if u.get("ExecStartPre", "").count("argv[]=") != 2 or any(
                f"argv[]={c} ;" not in u.get("ExecStartPre", "") for c in (gate_cmd, ready_cmd)
            ):
                raise Hold("Effective strict start checks differ")
            units[name] = u
        # Read-only user import/environment check: no collector/clock control command.
        code = "import importlib.util,json,sys; from pathlib import Path; p=Path(sys.argv[1]); s=importlib.util.spec_from_file_location('rt',p/'integrations/runtime/marketdata_runtime.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps({'release_id':m.environment_check(m.load_profile(sys.argv[2]))}))"
        check = json.loads(
            rt.command(
                [
                    "/usr/sbin/runuser",
                    "-u",
                    p["user"],
                    "--",
                    str(Path(r["release_root"]) / ".venv/bin/python"),
                    "-I",
                    "-c",
                    code,
                    r["release_root"],
                    p["runtime_profile"],
                ]
            )
        )
        if check["release_id"] != manifest["release_id"]:
            raise Hold("Actual import/environment mismatch")
        clock = rt.probe(r)
        if clock.get("provider") != provider["provider"]:
            raise Hold("Provider changed during commit")
        return {
            "activation": {"units": units, "environment": check, "provider": provider},
            "clock_at_commit": clock,
        }


class Transition:
    def __init__(
        self, plan, approval, host, *, boot=rt.boot_id, fault=lambda _: None, trust=gate.trusted
    ):
        self.plan, self.approval, self.host = plan, approval, host
        self.boot, self.fault, self.trust = boot, fault, trust
        self.before_p, _, self.after_p, _ = validate_plan(plan)
        self.ledger = gate.Ledger(plan["state_dir"])
        self.directory = self.ledger.root / "runtime-transitions" / plan["plan_id"]
        required = {
            "schema",
            "approved",
            "plan_id",
            "approved_by",
            "approval_reference",
            "approved_at",
            "actions",
        }
        if set(approval) != required or approval["schema"] != 1 or approval["approved"] is not True:
            raise Hold("An explicit approval receipt is required")
        if approval["plan_id"] != plan["plan_id"] or set(approval["actions"]) != {
            "stage",
            "commit",
            "rollback",
        }:
            raise Hold("Approval is not bound to this plan and recovery scope")
        if not all(
            isinstance(approval[k], str) and approval[k].strip()
            for k in ("approved_by", "approval_reference", "approved_at")
        ):
            raise Hold("Approval attribution is missing")

    def read(self, name):
        path = self.directory / name
        self.trust(path)
        value = json.loads(path.read_text())
        if name == "progress.json":
            if value.get("record_id") != gate.canonical_hash(
                {k: v for k, v in value.items() if k != "record_id"}
            ):
                raise Hold("Transition progress content/hash mismatch")
            if value.get("direction") not in {"apply", "rollback"} or value.get("phase") not in {
                "prepared",
                "files_staged",
            }:
                raise Hold("Unknown recorded transition phase")
        if name.endswith("-receipt.json"):
            if value.get("receipt_id") != gate.canonical_hash(
                {k: v for k, v in value.items() if k != "receipt_id"}
            ):
                raise Hold("Commit receipt content/hash mismatch")
            if value.get("plan_id") != self.plan["plan_id"] or value.get(
                "approval_sha256"
            ) != gate.canonical_hash(self.approval):
                raise Hold("Commit receipt belongs to another plan/approval")
            if value.get("ledger_after_sha256") != gate.canonical_hash(value["ledger_after"]):
                raise Hold("Commit receipt ledger hash mismatch")
        return value

    def record(self, value):
        record = {k: v for k, v in value.items() if k != "record_id"}
        record["record_id"] = gate.canonical_hash(record)
        atomic(self.directory / "progress.json", encode(record))

    def state(self):
        self.trust(self.ledger.path)
        return json.loads(self.ledger.path.read_text())

    def verify_files(self):
        found = {}
        for role, f in self.plan["files"].items():
            self.trust(f["path"])
            s = Path(f["path"]).stat()
            # Root ownership and all ancestor permissions were checked by trust().
            if stat.S_IMODE(s.st_mode) != f["mode"]:
                raise Hold("Target mode changed")
            digest = rt.digest(f["path"])
            sides = [side for side in ("before", "after") if digest == f[side + "_sha256"]]
            if not sides:
                raise Hold(f"Unapproved file content: {role}")
            found[role] = sides
        return found

    def pending(self, base, direction):
        return {
            **base,
            "schema": 2,
            "configuration_transition": {
                "kind": "collector_runtime_transition_pending",
                "plan_id": self.plan["plan_id"],
                "approval_sha256": gate.canonical_hash(self.approval),
                "direction": direction,
                "evidence_directory": str(self.directory),
            },
        }

    def ensure_current_boot(self, direction="apply"):
        if self.boot() != self.plan["boot_id"]:
            raise Hold("Boot changed: keep pending; a reviewed recovery procedure is required")

    def _prepare(self, direction):
        self.ensure_current_boot(direction)
        self.host.audit()
        actual = self.verify_files()
        state = self.state()
        if not self.directory.exists():
            if state != self.plan["ledger_before"] or any(
                "before" not in sides for sides in actual.values()
            ):
                raise Hold("Plan baseline no longer matches")
            self.trust(self.ledger.root, directory=True)
            parent = self.directory.parent
            parent.mkdir(mode=0o755, exist_ok=True)
            self.trust(parent, directory=True)
            sync_dir(self.ledger.root)
            self.directory.mkdir(mode=0o755)
            sync_dir(parent)
        self.trust(self.directory, directory=True)
        immutable(self.directory / "plan.json", self.plan)
        immutable(self.directory / "approval.json", self.approval)
        if not (self.directory / "progress.json").exists():
            if state != self.plan["ledger_before"] or any(
                "before" not in sides for sides in actual.values()
            ):
                raise Hold("Incomplete preparation cannot adopt a changed baseline")
            progress = {
                "direction": direction,
                "phase": "prepared",
                "base_ledger": state,
                "baseline_provider": self.host.snapshot(),
            }
            self.record(progress)
            self.fault("prepared")
        progress = self.read("progress.json")
        if progress["direction"] != direction:
            if direction != "rollback":
                raise Hold("Rollback cannot silently become apply")
            apply_receipt = self.directory / "apply-receipt.json"
            if apply_receipt.exists() and state == self.read("apply-receipt.json")["ledger_after"]:
                base = state
            elif state == self.plan["ledger_before"] or state == self.pending(
                progress["base_ledger"], "apply"
            ):
                base = progress["base_ledger"]
            else:
                raise Hold("Unexpected ledger while choosing rollback")
            # Persist rollback intent before touching the ledger or files.
            progress = {
                **progress,
                "direction": "rollback",
                "phase": "prepared",
                "base_ledger": base,
            }
            progress.pop("files_verified_boottime", None)
            self.record(progress)
            self.fault("rollback_intent")
        receipt_path = self.directory / f"{direction}-receipt.json"
        if receipt_path.exists() and state == self.read(receipt_path.name)["ledger_after"]:
            target = "after" if direction == "apply" else "before"
            if any(target not in sides for sides in actual.values()):
                raise Hold("Completed transition files have changed")
            return progress, True
        allowed = [progress["base_ledger"], self.pending(progress["base_ledger"], direction)]
        if direction == "rollback":
            allowed.append(self.pending(self.plan["ledger_before"], "apply"))
        if state not in allowed:
            raise Hold("Ledger does not match an acknowledged transition stage")
        atomic(self.ledger.path, encode(self.pending(progress["base_ledger"], direction)))
        self.fault("pending")
        return progress, False

    def stage(self, direction="apply"):
        self.trust(self.ledger.root, directory=True)
        self.trust(self.ledger.path)
        with self.ledger.lock():
            progress, completed = self._prepare(direction)
            if completed:
                return {"configuration": direction + "_committed", "idempotent": True}
            target = "after" if direction == "apply" else "before"
            for role in ROLES:
                f = self.plan["files"][role]
                if rt.digest(f["path"]) != f[target + "_sha256"]:
                    atomic(f["path"], f[target].encode(), f["mode"])
                self.fault("file_" + role)
            if (
                progress["phase"] != "files_staged"
                or progress.get("files_verified_boot_id") != self.boot()
            ):
                progress.update(
                    phase="files_staged",
                    files_verified_boottime=time.clock_gettime(time.CLOCK_BOOTTIME),
                    files_verified_boot_id=self.boot(),
                )
                self.record(progress)
            self.fault("files_staged")
            return {
                "configuration": direction + "_staged",
                "work_allowed": False,
                "next": "daemon-reload and verify the exact collector unit/release, then explicit commit; no service commands were sent",
            }

    def commit(self):
        self.trust(self.ledger.root, directory=True)
        self.trust(self.ledger.path)
        with self.ledger.lock():
            progress = self.read("progress.json")
            direction = progress["direction"]
            self.ensure_current_boot(direction)
            state = self.state()
            actual = self.verify_files()
            target = "after" if direction == "apply" else "before"
            if any(target not in sides for sides in actual.values()):
                raise Hold("All files must match the committed direction")
            receipt_path = self.directory / f"{direction}-receipt.json"
            if receipt_path.exists() and state == self.read(receipt_path.name)["ledger_after"]:
                return {
                    "configuration": direction + "_committed",
                    "idempotent": True,
                    "current_clock_ready": "not inferred from a past receipt",
                }
            if progress["phase"] != "files_staged" or state != self.pending(
                progress["base_ledger"], direction
            ):
                raise Hold("Not at the recorded activation boundary")
            if progress.get("files_verified_boot_id") != self.boot():
                raise Hold(
                    "Boot changed after staging; stage rollback before activation verification"
                )
            if self.read("plan.json") != self.plan or self.read("approval.json") != self.approval:
                raise Hold("Recorded plan/approval mismatch")
            self.host.audit()
            evidence = self.host.activation(progress, direction)
            p = self.after_p if direction == "apply" else self.before_p
            final = {
                **progress["base_ledger"],
                "schema": 1,
                "profile_sha256": gate.canonical_hash(p),
            }
            history = list(final.get("configuration_history", []))
            history.append(
                {
                    "plan_id": self.plan["plan_id"],
                    "approval_sha256": gate.canonical_hash(self.approval),
                    "direction": direction,
                    "receipt": str(receipt_path),
                }
            )
            final["configuration_history"] = history
            receipt = {
                "schema": 1,
                "plan_id": self.plan["plan_id"],
                "direction": direction,
                "observed_boot_id": self.boot(),
                "approval_sha256": gate.canonical_hash(self.approval),
                "sample": rt.stamp(),
                "ledger_before_sha256": gate.canonical_hash(progress["base_ledger"]),
                "ledger_after": final,
                "ledger_after_sha256": gate.canonical_hash(final),
                **evidence,
            }
            receipt["receipt_id"] = gate.canonical_hash(receipt)
            if receipt_path.exists():
                saved = self.read(receipt_path.name)
                if (
                    saved["ledger_after"] != final
                    or saved["approval_sha256"] != receipt["approval_sha256"]
                ):
                    raise Hold("Interrupted commit receipt is inconsistent")
                receipt = saved  # Preserve the first observation; the gate checks readiness afresh.
            else:
                immutable(receipt_path, receipt)
            self.fault("receipt")
            atomic(self.ledger.path, encode(final))
            self.fault("committed")
            return {
                "configuration": direction + "_committed",
                "step_reopened": False,
                "current_clock_ready": evidence["clock_at_commit"]["ready"],
                "current_boot_preparation_preserved": final["boot_id"] == self.boot(),
                "receipt": str(receipt_path),
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "stage", "commit", "rollback", "status"])
    parser.add_argument(
        "--profile", type=Path, default=Path("/etc/marketdata/clock-bootstrap.json")
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approval", type=Path)
    a = parser.parse_args()
    try:
        if a.action == "plan":
            if a.plan.exists():
                raise Hold("New plan path required")
            value = make_plan(a.profile)
            atomic(a.plan, encode(value))
            result = {"plan_id": value["plan_id"], "approved": False}
        else:
            plan = json.loads(a.plan.read_text())
            validate_plan(plan)
            if a.action == "status":
                result = {
                    "ledger": json.loads((Path(plan["state_dir"]) / "boot-state.json").read_text()),
                    "current_boot_id": rt.boot_id(),
                    "file_hashes": {k: rt.digest(f["path"]) for k, f in plan["files"].items()},
                }
            else:
                if os.geteuid() != 0:
                    raise Hold(
                        "Only an explicitly approved root operator can apply/recover this runtime transition"
                    )
                for path in (
                    Path(__file__),
                    Path(__file__).with_name("clock_gate.py"),
                    Path(__file__).with_name("marketdata_runtime.py"),
                    Path(__file__).with_name("render_units.py"),
                    Path(__file__).with_name("release-pins.json"),
                    a.plan,
                    a.approval,
                ):
                    if path is None:
                        raise Hold("Approval receipt required")
                    gate.trusted(path)
                approval = json.loads(a.approval.read_text())
                tx = Transition(plan, approval, LiveHost(plan))
                result = (
                    tx.commit()
                    if a.action == "commit"
                    else tx.stage("rollback" if a.action == "rollback" else "apply")
                )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Hold, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"work_allowed": False, "hold": str(exc)}), file=sys.stderr)
        return 75


if __name__ == "__main__":
    sys.exit(main())
