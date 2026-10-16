"""Reconcile one hash-bound OI report; there is no workload invocation path.

Production entry requires the root-owned, approved handoff and UID 1000. The
original request lock is held through verification and durable terminal state.
"""

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import uuid

HERE = Path(__file__).resolve().parent
PURPOSE = "t16-exact-oi-result-recovery-v1"


class Hold(RuntimeError):
    pass


def require(ok, message):
    if not ok:
        raise Hold(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def safe(path):
    path = Path(path)
    require(path.is_absolute(), "Absolute path required")
    require(
        all(not p.is_symlink() for p in (path, *path.parents)), "Symlink in evidence/state path"
    )
    return path


def read(path):
    path = safe(path)
    require(path.is_file(), "Evidence file missing: " + str(path))
    return path.read_bytes()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def mkdir(path):
    path = safe(path)
    if not path.exists():
        mkdir(path.parent)
        path.mkdir(mode=0o700)
        sync_directory(path.parent)
    require(path.is_dir(), "State directory differs")


def write(path, raw, *, immutable=False):
    path = safe(path)
    if immutable and path.exists():
        require(read(path) == raw, "Immutable recovery record differs: " + path.name)
        return
    # A unique temporary name preserves the original failed request.tmp.
    temporary = path.parent / (".recovery-" + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if immutable:
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            require(read(path) == raw, "Concurrent immutable recovery record differs")
        temporary.unlink()  # only this call's temporary file
    else:
        os.replace(temporary, path)
    sync_directory(path.parent)


def validate_plan(plan):
    require(plan.get("purpose") == PURPOSE and plan.get("schema") == 1, "Wrong recovery scope")
    require(
        plan.get("plan_id") == sha(encode({k: v for k, v in plan.items() if k != "plan_id"})),
        "Recovery plan hash differs",
    )
    require(
        plan["job"] == "oi-quality"
        and plan["work_invocations"] == 0
        and plan["observed_work_exit_code"] is None
        and plan["original_process_exit_code"] == 1,
        "Recovery is not a workload retry",
    )
    path = safe(plan["request_path"])
    require(
        path.name == "request.json" and path.parent.name == "oi-quality", "Unexpected request path"
    )


def evidence(plan, original, contract):
    require(sha(original) == plan["request_sha256"], "Original request changed")
    request = json.loads(original)
    require(
        request["schema"] == contract.VERSION
        and request["state"] == "pending"
        and request["job"] == plan["job"]
        and request["request_id"] == plan["request_id"]
        and request["identity"] == plan["identity"]
        and request["pins"] == plan["pins"]
        and request["scope"] == plan["scope"]
        and request["arguments"] == plan["arguments"]
        and not request.get("work_result")
        and not request.get("unsealed_result"),
        "Original scope differs",
    )
    require(
        contract.digest(contract.encode(request["scope"])) == plan["identity"],
        "Original identity differs",
    )
    attempt = request["attempt"]
    require(
        attempt["id"] == plan["original_attempt_id"]
        and attempt["invocation_id"] == plan["original_invocation_id"],
        "Original attempt differs",
    )
    raw = read(plan["report_path"])
    require(sha(raw) == plan["report_sha256"], "Report changed")
    report = json.loads(raw)
    from marketdata.domain import json_bytes

    require(
        report["report_id"]
        == plan["report_id"]
        == sha(json_bytes({k: v for k, v in report.items() if k != "report_id"})),
        "Report identity differs",
    )
    # Read exactly the approved immutable prefix; later unrelated journal appends
    # cannot substitute a different observation or failure for this attempt.
    with safe(plan["journal_path"]).open("rb") as stream:
        prefix = stream.read(plan["journal_prefix_bytes"])
    require(
        sha(prefix) == plan["journal_prefix_sha256"] and prefix.endswith(b"\n"),
        "Original journal changed",
    )
    events = [json.loads(line) for line in prefix.splitlines()]
    related = [
        event
        for event in events
        if event.get("request_id") == plan["request_id"]
        and event.get("attempt_id") == plan["original_attempt_id"]
        and event.get("invocation_id") == plan["original_invocation_id"]
        and event.get("job") == plan["job"]
    ]
    observations = [
        e
        for e in related
        if e.get("kind") == "time_decision" and e.get("reason") == "oi_quality_as_of"
    ]
    require(
        len(observations) == 1 and observations[0]["observation"] == report["clock_observation"],
        "Report does not match the original invocation observation",
    )
    failures = [e for e in related if e.get("kind") == "request_finished"]
    require(
        len(failures) == 1
        and failures[0]["state"] == "finalization_failed"
        and failures[0]["exit_code"] == 1
        and failures[0]["work"] is None
        and any(
            "decimal.Decimal" in e.get("cause", "") and "isoformat" in e.get("cause", "")
            for e in failures[0]["errors"]
        ),
        "Original serialization failure differs",
    )
    value = contract.seal_result(
        {"job": "oi-quality", "result": report, "files": [plan["report_path"]], "records": []}
    )
    code = 2 if report["needs_review"] else 0
    disposition = contract.classify(
        "oi-quality", code, value, request["pins"], request["arguments"]
    )
    require(
        code == plan["derived_report_exit_code"] and disposition["state"] == "complete",
        "Report disposition differs",
    )
    return request, raw, prefix, failures[0], value, disposition


def recover(plan, contract, scope_check, monitor, *, approval, fault=lambda point: None):
    """All mutations are under the original request lock; no manager operations."""
    validate_plan(plan)
    require(
        approval.get("enabled") is True
        and approval.get("recovery_plan_id") == plan["plan_id"]
        and approval.get("host_plan_id")
        and approval.get("reference"),
        "Exact recovery approval required",
    )
    request_path = safe(plan["request_path"])
    lock = safe(request_path.with_name("request.lock"))
    require(lock.is_file(), "Original request lock missing")
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = read(request_path)
        directory = request_path.parent / "reconciliations" / plan["plan_id"]
        proposed = directory / "terminal-request.json"
        before = directory / "original-request.json"
        if proposed.exists():
            original = read(before)
            terminal_raw = read(proposed)
            require(
                sha(current) == plan["request_sha256"] or current == terminal_raw,
                "Request changed after recovery staging",
            )
        else:
            require(sha(current) == plan["request_sha256"], "Current request differs; no overwrite")
            original, terminal_raw = current, None
        request, report_raw, prefix, failure, value, disposition = evidence(
            plan, original, contract
        )
        mkdir(directory)
        write(directory / "approval.json", encode(approval), immutable=True)
        write(before, original, immutable=True)
        write(directory / "original-report.json", report_raw, immutable=True)
        write(directory / "original-journal.jsonl", prefix, immutable=True)
        write(directory / "original-failure.json", encode(failure), immutable=True)
        fault("evidence_preserved")
        if terminal_raw is not None:
            terminal = json.loads(terminal_raw)
            require(
                all(
                    terminal[k] == request[k]
                    for k in (
                        "schema",
                        "request_id",
                        "identity",
                        "scope",
                        "job",
                        "arguments",
                        "pins",
                    )
                ),
                "Staged request identity/pins differ",
            )
            require(
                terminal.get("state") == "complete"
                and terminal.get("work_result") == value
                and terminal.get("disposition") == disposition
                and terminal.get("reconciliation", {}).get("plan_id") == plan["plan_id"],
                "Staged result differs",
            )
            require(
                terminal["attempt"]["work"]
                == {
                    "recovered_committed_result": True,
                    "observed_exit_code": None,
                    "result_code": disposition["returncode"],
                    "work_invocations": 0,
                }
                and terminal["attempt"]["monitor"].get("ok") is True
                and not terminal["attempt"]["errors"],
                "Staged recovery was not successfully finalized",
            )
        if current != terminal_raw:
            scope_check()
            # Even a staged result gets a new live observation before its first
            # request commit. A failed monitor/recording leaves pending untouched.
            attempt_id = uuid.uuid4().hex

            def emit(event):
                raw = contract.encode(
                    {
                        **event,
                        "request_id": plan["request_id"],
                        "recovery_plan_id": plan["plan_id"],
                        "attempt_id": attempt_id,
                        "job": "oi-quality",
                        "kind_source": "explicit-reconciliation",
                    }
                )
                path = directory / ("observation-" + attempt_id + ".jsonl")
                out = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                with os.fdopen(out, "ab") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())

            try:
                monitored = monitor(emit, scope_check)
                require(
                    monitored.get("stopped") is True and monitored.get("ok") is True,
                    "Recovery monitor failed",
                )
                scope_check()
                # Recheck the stored report immediately before terminal staging.
                contract.classify(
                    "oi-quality",
                    disposition["returncode"],
                    value,
                    request["pins"],
                    request["arguments"],
                )
            except BaseException as exc:
                write(
                    directory / ("failed-" + attempt_id + ".json"),
                    encode(
                        {
                            "type": type(exc).__name__,
                            "error": str(exc),
                            "request_written": False,
                            "work_invocations": 0,
                        }
                    ),
                    immutable=True,
                )
                raise
            fault("monitor_completed")
            if terminal_raw is None:
                terminal = copy.deepcopy(request)
                terminal.update(
                    state="complete",
                    work_result=value,
                    disposition=disposition,
                    exit_code=disposition["returncode"],
                    reconciliation={
                        "plan_id": plan["plan_id"],
                        "original_attempt_id": plan["original_attempt_id"],
                        "original_invocation_id": plan["original_invocation_id"],
                        "original_process_exit_code": 1,
                        "original_request_sha256": plan["request_sha256"],
                        "report_sha256": plan["report_sha256"],
                    },
                    attempt={
                        "id": attempt_id,
                        "invocation_id": None,
                        "execution": "approved-manual-result-reconciliation",
                        "work": {
                            "recovered_committed_result": True,
                            "observed_exit_code": None,
                            "result_code": disposition["returncode"],
                            "work_invocations": 0,
                        },
                        "monitor": monitored,
                        "errors": [],
                    },
                )
                terminal_raw = contract.encode(terminal)
                write(proposed, terminal_raw, immutable=True)
            fault("terminal_staged")
            attempts = request_path.parent / "attempts" / plan["request_id"]
            mkdir(attempts)
            write(attempts / (terminal["attempt"]["id"] + ".json"), terminal_raw, immutable=True)
            fault("attempt_saved")
            require(read(request_path) == original, "Request changed before commit")
            write(request_path, terminal_raw)
            fault("request_committed")
        else:
            attempts = request_path.parent / "attempts" / plan["request_id"]
            require(
                read(attempts / (terminal["attempt"]["id"] + ".json")) == terminal_raw,
                "Recovery attempt receipt missing",
            )
        receipt = {
            "purpose": PURPOSE,
            "plan_id": plan["plan_id"],
            "request_id": plan["request_id"],
            "before_sha256": plan["request_sha256"],
            "after_sha256": sha(terminal_raw),
            "report_sha256": plan["report_sha256"],
            "identity_preserved": True,
            "pins_preserved": True,
            "original_process_exit_code": 1,
            "observed_work_exit_code": None,
            "derived_result_code": disposition["returncode"],
            "work_invocations": 0,
            "request_state": "complete",
            "data_quality": "report findings preserved",
            "recovered_attempt_id": terminal["attempt"]["id"],
        }
        write(directory / "receipt.json", encode(receipt), immutable=True)
        fault("receipt_saved")
        return receipt
    finally:
        os.close(fd)


def modules(plan):
    release = Path(plan["release"])
    for name, digest in plan["source_files"].items():
        require(sha(read(release / name)) == digest, "Original recovery runtime changed")
    sys.path[:0] = [str(release / "base/packages/marketdata/src"), str(release / "base")]
    spec = importlib.util.spec_from_file_location(
        "original_recovery_wrapper", release / "policy/run_job.py"
    )
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    return wrapper, wrapper.load_contract()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--approve-recovery")
    parser.add_argument("--authority", type=Path)
    args = parser.parse_args()
    plan = json.loads((HERE / "recovery-plan.json").read_bytes())
    validate_plan(plan)
    if not args.run:
        print(
            json.dumps(
                {"plan_id": plan["plan_id"], "operating_approval": False, "work_invocations": 0}
            )
        )
        return
    host = json.loads((HERE / "host-plan.json").read_bytes())
    require(
        os.geteuid() == 1000 and HERE == Path("/opt/marketdata/tools") / host["tool_id"],
        "Sealed UID 1000 recovery required",
    )
    require(
        args.approve_recovery == plan["plan_id"] == host["recovery_plan_id"],
        "Exact recovery plan required",
    )
    authority_path = (
        Path("/var/lib/marketdata-clock/scheduled-policy-handoffs")
        / host["plan_id"]
        / "approval.json"
    )
    require(args.authority == authority_path, "Unexpected authority path")
    authority = json.loads(read(authority_path))
    meta = authority_path.stat()
    require(meta.st_uid == 0 and not stat.S_IMODE(meta.st_mode) & 0o022, "Untrusted approval file")
    require(
        authority.get("enabled") is True
        and authority.get("plan_id") == host["plan_id"]
        and authority.get("purpose") == host["purpose"]
        and authority.get("approval_reference"),
        "Recovery not approved by host plan",
    )
    require(
        Path("/proc/sys/kernel/random/boot_id").read_text().strip() == plan["boot_id"],
        "Boot changed",
    )
    wrapper, contract = modules(plan)

    def guard():
        current = contract.inputs(
            "oi-quality", plan["arguments"], Path(plan["binding"]), Path(plan["release"])
        )
        require(current == plan["scope"], "Original input scope changed")

    from marketdata.job_clock import Context

    def monitor(emit, scope_check):
        context = Context(
            wrapper.query_factory(Path(plan["binding"])), emit, scope_guard=scope_check
        )
        try:
            context.start()
            context.point("explicit_stored_result_reconciliation")
            context.check_health()
        finally:
            stopped = context.close()
        return {**stopped, "ok": True}

    result = recover(
        plan,
        contract,
        guard,
        monitor,
        approval={
            "enabled": True,
            "recovery_plan_id": plan["plan_id"],
            "host_plan_id": host["plan_id"],
            "reference": str(authority_path),
        },
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
