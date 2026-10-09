"""Explicit, audited three-file Chrony source configuration transition.

No service control, step, RTC write, enrollment, or deletion command exists here.
The unchanged deployed gate rejects schema 2 while a transaction is pending.
The original closed clock state is preserved inside that envelope. Only after
activation verification is a schema-1 state committed with a linked receipt.
"""

import argparse
import hashlib
import importlib.util
import ipaddress
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
ROLES = ("chrony", "runtime", "bootstrap")


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


def sources_and_body(text, *, candidate=False):
    sources, body = [], []
    for raw in text.splitlines():
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] in {"server", "pool", "peer"}:
            if candidate:
                if parts[0] != "server" or parts[2:] != ["iburst", "minpoll", "6", "maxpoll", "8"]:
                    raise Hold(
                        "Only four pinned server addresses with unchanged polling are allowed"
                    )
                address = ipaddress.ip_address(parts[1])
                if address.version != 4 or not address.is_global or str(address) == "91.189.91.157":
                    raise Hold("Candidate source is not an approved public IPv4 address")
            sources.append(parts[1])
        else:
            body.append(line)
    if candidate and (len(sources) != 4 or len(set(sources)) != 4):
        raise Hold("Exactly four distinct source addresses required")
    return sources, body


def tool_hashes():
    return {
        n: rt.digest(Path(__file__).with_name(n))
        for n in ("clock_config_transition.py", "clock_gate.py", "marketdata_runtime.py")
    }


def validate_plan(plan):
    if plan.get("schema") != 1 or plan.get("kind") != "chrony_source_configuration":
        raise Hold("Unknown plan schema/scope")
    if plan.get("recovery_policy") != "new_boot_rollback_only_with_bootstrap_stopped_and_disabled":
        raise Hold("Explicit closed-window recovery policy required")
    if plan.get("plan_id") != gate.canonical_hash(
        {k: v for k, v in plan.items() if k != "plan_id"}
    ):
        raise Hold("Plan content/ID mismatch")
    if plan["tools_sha256"] != tool_hashes():
        raise Hold("Plan belongs to different transition code")
    if set(plan["files"]) != set(ROLES):
        raise Hold("Unexpected transition file set")
    for f in plan["files"].values():
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", f["path"]) or ".." in Path(f["path"]).parts:
            raise Hold("Unsafe target path")
        if f["mode"] != 0o644 or f["uid"] != 0:
            raise Hold("Reviewed root-owned 0644 files required")
        for side in ("before", "after"):
            if sha(f[side].encode()) != f[side + "_sha256"]:
                raise Hold("File content/hash mismatch")
    old_c = plan["files"]["chrony"]["before"]
    new_c = plan["files"]["chrony"]["after"]
    _, old_body = sources_and_body(old_c)
    ips, new_body = sources_and_body(new_c, candidate=True)
    if old_body != new_body:
        raise Hold("Non-source Chrony configuration change is outside scope")
    before_r = json.loads(plan["files"]["runtime"]["before"])
    after_r = json.loads(plan["files"]["runtime"]["after"])
    expected_r = {**before_r, "chrony_config_sha256": sha(new_c.encode())}
    if after_r != expected_r or before_r["chrony_config_sha256"] != sha(old_c.encode()):
        raise Hold("Only the runtime configuration hash may change")
    before_b = json.loads(plan["files"]["bootstrap"]["before"])
    after_b = json.loads(plan["files"]["bootstrap"]["after"])
    if before_b["runtime_profile_sha256"] != plan["files"]["runtime"]["before_sha256"]:
        raise Hold("Old profile binding is inconsistent")
    if after_b != {**before_b, "runtime_profile_sha256": plan["files"]["runtime"]["after_sha256"]}:
        raise Hold("Only the bootstrap runtime-profile hash may change")
    paths = plan["files"]
    if (
        paths["runtime"]["path"] != before_b["runtime_profile"]
        or paths["chrony"]["path"] != before_r["chrony_config"]
    ):
        raise Hold("Profile paths and file targets differ")
    if plan["state_dir"] != before_b["state_dir"] or plan["new_source_addresses"] != ips:
        raise Hold("State directory/source inventory mismatch")
    original = plan["ledger_before"]
    if original.get("schema") != 1 or original.get("phase") != "closed":
        raise Hold("Only a completed, closed boot can change configuration")
    if original.get("boot_id") != plan["boot_id"] or original.get(
        "profile_sha256"
    ) != gate.canonical_hash(before_b):
        raise Hold("Old ledger/profile/boot mismatch")
    if not original.get("closed_observation", {}).get("ready") or original.get(
        "step_attempts"
    ) not in (0, 1):
        raise Hold("Missing closed clock evidence")
    if plan["ledger_before_sha256"] != gate.canonical_hash(original):
        raise Hold("Ledger snapshot hash mismatch")
    return before_b, before_r, after_b, after_r


def make_plan(profile, configuration, source_review):
    p, runtime = gate.read_profile(profile)
    ledger = gate.Ledger(p["state_dir"])
    with ledger.lock(shared=True):
        state = ledger.read()
        if state["boot_id"] != rt.boot_id() or state["phase"] != "closed":
            raise Hold("Current boot must already be closed")
        paths = {
            "bootstrap": str(Path(profile)),
            "runtime": p["runtime_profile"],
            "chrony": runtime["chrony_config"],
        }
        files = {}
        for role, path in paths.items():
            gate.trusted(path)
            s = Path(path).stat()
            files[role] = {
                "path": path,
                "uid": s.st_uid,
                "mode": stat.S_IMODE(s.st_mode),
                "before": Path(path).read_text(),
            }
        files["chrony"]["after"] = Path(configuration).read_text()
        files["runtime"]["after"] = encode(
            {**runtime, "chrony_config_sha256": sha(files["chrony"]["after"].encode())}
        ).decode()
        files["bootstrap"]["after"] = encode(
            {**p, "runtime_profile_sha256": sha(files["runtime"]["after"].encode())}
        ).decode()
        for f in files.values():
            for side in ("before", "after"):
                f[side + "_sha256"] = sha(f[side].encode())
        value = {
            "schema": 1,
            "kind": "chrony_source_configuration",
            "files": files,
            "recovery_policy": "new_boot_rollback_only_with_bootstrap_stopped_and_disabled",
            "boot_id": state["boot_id"],
            "state_dir": p["state_dir"],
            "ledger_before": state,
            "ledger_before_sha256": gate.canonical_hash(state),
            "tools_sha256": tool_hashes(),
            "chronyd_sha256": rt.digest("/usr/sbin/chronyd"),
            "prepared_sample": rt.stamp(),
            "source_review_sha256": rt.digest(source_review),
            "new_source_addresses": sources_and_body(files["chrony"]["after"], candidate=True)[0],
        }
        value["plan_id"] = gate.canonical_hash(value)
        validate_plan(value)
        return value


class LiveHost:
    def __init__(self, plan):
        self.plan = plan
        self.before_p, self.before_r, self.after_p, self.after_r = validate_plan(plan)

    def audit(self):
        # Units, users, RTC settings and work policies are unchanged in this scope.
        gate.Host(self.before_p, self.before_r).audit_quiescent()
        raw = rt.command(
            [
                "/usr/bin/systemctl",
                "show",
                "marketdata-clock-bootstrap.service",
                "--property=MainPID,ControlPID,ActiveState,LoadState,UnitFileState",
                "--no-pager",
            ]
        )
        fields = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        if fields.get("MainPID") != "0" or fields.get("ControlPID") != "0":
            raise Hold("Bootstrap must not be executing during configuration recovery")
        if rt.boot_id() != self.plan["boot_id"] and (
            fields.get("LoadState") != "loaded"
            or fields.get("ActiveState") != "inactive"
            or fields.get("UnitFileState") != "disabled"
        ):
            raise Hold(
                "New-boot rollback requires bootstrap stopped and disabled with its unit unchanged"
            )

    def snapshot(self):
        provider = rt.provider_identity()
        provider["start_boottime"] = int(rt.process_start(provider["pid"])) / os.sysconf(
            "SC_CLK_TCK"
        )
        csv = rt.command([self.before_r["chronyc"], "-n", "-c", "-h", "127.0.0.1", "sources"])
        addresses = sorted(line.split(",")[2] for line in csv.splitlines() if line.strip())
        names = {
            ip: rt.command(
                [self.before_r["chronyc"], "-n", "-h", "127.0.0.1", "sourcename", ip]
            ).strip()
            for ip in addresses
        }
        return {"provider": provider, "source_addresses": addresses, "source_names": names}

    def activation(self, record, direction):
        current = self.snapshot()
        runtime = self.after_r if direction == "apply" else self.before_r
        argv = current["provider"]["argv"]
        if "-f" not in argv or argv[argv.index("-f") + 1] != runtime["chrony_config"]:
            raise Hold("Daemon is not using the planned configuration path")
        if rt.digest("/usr/sbin/chronyd") != self.plan["chronyd_sha256"]:
            raise Hold("Chrony executable changed")
        if direction == "apply":
            expected = sorted(self.plan["new_source_addresses"])
            if current["source_addresses"] != expected or any(
                current["source_names"][ip] != ip for ip in expected
            ):
                raise Hold("Actual daemon source inventory differs from the pinned plan")
        else:
            # Rollback restores the originally approved pool's DNS semantics.
            # Verify original association names, not a historical DNS answer.
            approved_names, _ = sources_and_body(self.plan["files"]["chrony"]["before"])
            if not current["source_addresses"] or not set(current["source_names"].values()) <= set(
                approved_names
            ):
                raise Hold("Rollback sources are not associated with the original configuration")
            if any(not ipaddress.ip_address(ip).is_global for ip in current["source_addresses"]):
                raise Hold("Rollback has no verified public source inventory")
        same_original = current["provider"] == record["baseline_provider"]["provider"]
        if not (direction == "rollback" and same_original):
            if (
                same_original
                or current["provider"]["start_boottime"] < record["files_verified_boottime"]
            ):
                raise Hold("A verified daemon activation after the file transition is required")
        # Acceptance of the new configuration is separate from current readiness.
        clock = rt.probe(runtime)
        if clock.get("provider") is None:
            raise Hold("Current provider/configuration could not be verified")
        if any(
            clock["provider"].get(k) != current["provider"].get(k)
            for k in ("pid", "invocation_id", "argv")
        ):
            raise Hold("Provider changed during commit verification")
        return {"activation": current, "clock_at_commit": clock}


class Transition:
    def __init__(
        self, plan, approval, host, *, boot=rt.boot_id, fault=lambda _: None, trust=gate.trusted
    ):
        self.plan, self.approval, self.host = plan, approval, host
        self.boot, self.fault, self.trust = boot, fault, trust
        self.before_p, _, self.after_p, _ = validate_plan(plan)
        self.ledger = gate.Ledger(plan["state_dir"])
        self.directory = self.ledger.root / "config-transitions" / plan["plan_id"]
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
                "kind": "chrony_source_configuration_pending",
                "plan_id": self.plan["plan_id"],
                "approval_sha256": gate.canonical_hash(self.approval),
                "direction": direction,
                "evidence_directory": str(self.directory),
            },
        }

    def ensure_current_boot(self, direction="apply"):
        if self.boot() != self.plan["boot_id"] and direction != "rollback":
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
                "next": "verify actual daemon activation, then explicit commit; no service commands were sent",
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
    parser.add_argument("--configuration", type=Path)
    parser.add_argument("--source-review", type=Path)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approval", type=Path)
    a = parser.parse_args()
    try:
        if a.action == "plan":
            if a.plan.exists() or not a.configuration or not a.source_review:
                raise Hold("New plan path, configuration, and source review required")
            value = make_plan(a.profile, a.configuration, a.source_review)
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
                        "Only an explicitly approved root operator can apply/recover configuration"
                    )
                for path in (
                    Path(__file__),
                    Path(__file__).with_name("clock_gate.py"),
                    Path(__file__).with_name("marketdata_runtime.py"),
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
