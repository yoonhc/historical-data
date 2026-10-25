"""Freeze a source-only release and proposed connection changes; no deployment."""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c


def main():
    candidate = c.HERE.parent / "t18-output-durability-2026-10-22/candidate"
    expected = json.loads((candidate.parent / "candidate-source.json").read_bytes())
    c.require(
        c.file_sha(c.BASE / "preserve/scripts/archive_backups.py") == expected["base_sha256"],
        "Installed base changed",
    )
    for name, digest in expected["candidate_files"].items():
        c.require(c.file_sha(candidate / name) == digest, "Reviewed candidate changed")
    sources = {}
    for directory in (
        "preserve/packages",
        "preserve/scripts",
        "preserve/integrations/systemd",
        "policy",
    ):
        for path in sorted((c.BASE / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                c.require(not path.is_symlink(), "Source symlink")
                sources[str(path.relative_to(c.BASE))] = path
    for rel in ("preserve/pyproject.toml", "preserve/uv.lock"):
        sources[rel] = c.BASE / rel
    for name in expected["candidate_files"]:
        sources["preserve/scripts/" + name] = candidate / name
    runtime = c.HERE / "runtime"
    runtime.mkdir(exist_ok=True)
    entries = {}
    for name, source in sorted(sources.items()):
        raw = source.read_bytes()
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            c.require(target.read_bytes() == raw, "Frozen runtime changed: " + name)
        else:
            target.write_bytes(raw)
        entries[name] = {"kind": "file", "sha256": c.sha(raw), "mode": 0o644, "source": str(target)}
    clock = "preserve/packages/marketdata/src/marketdata/checkpoint_clock.py"
    parents = {
        "variants": {"preserve": str(c.BASE / "preserve")},
        "files": {clock: {"parent": str(c.BASE / clock), "sha256": c.file_sha(c.BASE / clock)}},
    }
    parent_raw = c.encode(parents)
    (runtime / "parents.json").write_bytes(parent_raw)
    entries["parents.json"] = {
        "kind": "file",
        "sha256": c.sha(parent_raw),
        "mode": 0o644,
        "source": str(runtime / "parents.json"),
    }
    external = {}
    for path in sorted((c.BASE / "preserve/.venv").rglob("*")):
        if "__pycache__" in path.parts or path.is_dir() and not path.is_symlink():
            continue
        external[str(path)] = c.node(path)
    external["/usr/bin/python3.12"] = c.node("/usr/bin/python3.12")
    release_digest = c.sha(
        c.canonical(
            {
                "files": {k: v["sha256"] for k, v in entries.items()},
                "external": external,
                "python": str(c.PYTHON),
            }
        )
    )
    release_id = "restic-output-20261022-" + release_digest[:16]
    release = "/opt/marketdata/releases/" + release_id

    paths = (c.SERVICE_PATH, c.TOPOLOGY, c.PROFILE, c.BINDING, c.T05_BINDING)
    changes = {p: {"before": c.node(p, text=True)} for p in paths}
    before_unit = changes[c.SERVICE_PATH]["before"]["text"]
    old_entry = str(c.BASE / "policy/run_job.py")
    c.require(before_unit.count(old_entry) == 1, "Unexpected service entrypoint")
    after_unit = before_unit.replace(old_entry, release + "/policy/run_job.py")

    def after(path, raw):
        changes[path]["after"] = {
            "kind": "file",
            "mode": 0o644,
            "sha256": c.sha(raw),
            "text": raw.decode(),
        }

    after(c.SERVICE_PATH, after_unit.encode())
    topology = json.loads(changes[c.TOPOLOGY]["before"]["text"])
    entry = topology["units"][c.SERVICE]["ExecStart"]
    c.require(len(entry) == 1 and entry[0] in before_unit, "Topology entrypoint differs")
    entry[0] = entry[0].replace(old_entry, release + "/policy/run_job.py")
    after(c.TOPOLOGY, c.encode(topology))
    profile = json.loads(changes[c.PROFILE]["before"]["text"])
    for path in (c.SERVICE_PATH, c.TOPOLOGY):
        c.require(
            profile["guard_files"][path] == changes[path]["before"]["sha256"],
            "Current guard differs",
        )
        profile["guard_files"][path] = changes[path]["after"]["sha256"]
    after(c.PROFILE, c.encode(profile))
    binding = c.encode(
        {
            "bootstrap_profile": c.PROFILE,
            "bootstrap_profile_sha256": changes[c.PROFILE]["after"]["sha256"],
        }
    )
    for path in (c.BINDING, c.T05_BINDING):
        after(path, binding)

    current_grant = json.loads(Path(c.GRANT).read_bytes())
    c.require(
        current_grant["enabled"] is True
        and current_grant["scope_id"] == c.sha(c.canonical(current_grant["scope"])),
        "Current recurring authority differs",
    )
    proposal = json.loads(json.dumps(current_grant))
    proposal.update(enabled=False, approved_at=None, approval_reference=None)
    proposal["scope"]["clock_binding"] = json.loads(binding)
    proposal["scope_id"] = c.sha(c.canonical(proposal["scope"]))
    immutable = {}
    protected = set(profile["guard_files"]) - set(changes)
    protected |= {
        profile["runtime_profile"],
        profile["rtc_confirmation"],
        "/etc/chrony/marketdata.conf",
        c.MANUAL,
    }
    protected |= {
        str(p)
        for p in Path("/etc/marketdata/checkpoint-clock-retirement").rglob("*")
        if p.is_file() and str(p) not in (*changes, c.GRANT)
    }
    protected |= {
        "/home/yoonhc/.config/marketdata/restic-archive.toml",
        "/home/yoonhc/.config/marketdata/restic-service.env",
    }
    for job in (*c.JOBS, "checkpoint-retirement"):
        for base in (
            "/etc/systemd/system/timers.target.wants",
            "/home/yoonhc/.config/systemd/user",
        ):
            protected.add(base + "/marketdata-" + job + ".timer")
        protected.add("/home/yoonhc/.config/systemd/user/marketdata-" + job + ".service")
    for path in sorted(protected):
        immutable[path] = c.node(path)
    owners = {}
    for path in {*immutable, *changes, c.GRANT}:
        if Path(path).exists() or Path(path).is_symlink():
            uid = Path(path).lstat().st_uid
            c.require(uid in (0, 1000, 65534), "Unexpected namespace UID")
            # Root is unmapped as 65534 in this tool sandbox. The native root
            # preflight must independently confirm this expectation before use.
            owners[path] = 0 if uid == 65534 else uid
    ledger = json.loads(Path(c.LEDGER).read_bytes())
    c.require(ledger["phase"] == "closed", "Boot preparation not closed")
    archive_request = c.ROOT / "state/scheduled-clock-policy/restic-archive/request.json"
    request = json.loads(archive_request.read_bytes())
    c.require(request["state"] == "complete", "Archive request requires reconciliation")
    rfile = request["work_result"]["files"][0]
    c.require(c.file_sha(rfile["path"]) == rfile["sha256"], "Committed request result changed")
    run = request["work_result"]["result"]["archive"]["run_id"]
    latest = c.ROOT / "reports/restic-archive-runs" / run
    bodies = {}
    for path in latest.glob("*.json"):
        receipt = json.loads(path.read_bytes())
        args = receipt.get("arguments", [])
        if (
            len(args) == 3
            and args[0] == "dump"
            and args[2] == "/backup.json"
            and receipt.get("returncode") == 0
        ):
            out = Path(receipt["stdout"])
            bodies[args[1]] = {
                "path": str(out),
                "bytes": out.stat().st_size,
                "sha256": c.file_sha(out),
            }
    pending = json.loads(
        (
            c.HERE.parent / "t18-capacity-recovery-2026-10-19/current-preservation-20261021.json"
        ).read_bytes()
    )["unarchived_enrolled_checkpoints"]
    for item in pending:
        manifest = Path(item["path"]) / "backup.json"
        c.require(
            c.file_sha(manifest) == item["manifest_sha256"], "Pending checkpoint manifest changed"
        )
        item["manifest_bytes"] = manifest.stat().st_size
    first_bodies = sum(b["bytes"] for b in bodies.values()) + sum(
        p["manifest_bytes"] for p in pending
    )
    max_temp = max([b["bytes"] for b in bodies.values()] + [p["manifest_bytes"] for p in pending])
    available = os.statvfs(c.ROOT).f_bavail * os.statvfs(c.ROOT).f_frsize
    plan = {
        "schema": 1,
        "purpose": "t18-archive-output-deployment-preparation-v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "release_id": release_id,
        "release_root": release,
        "release_digest": release_digest,
        "python": str(c.PYTHON),
        "release_entries": entries,
        "external_runtime": external,
        "changes": changes,
        "recurring_before": c.node(c.GRANT, text=True),
        "recurring_proposal_after": proposal,
        "ledger_before": ledger,
        "protected": immutable,
        "expected_host_uids": owners,
        "archive_request": {
            "path": str(archive_request),
            "sha256": c.file_sha(archive_request),
            "request_id": request["request_id"],
            "state": request["state"],
        },
        "pending_checkpoints_observed": pending,
        "capacity": {
            "available_bytes": available,
            "release_bytes": sum(Path(v["source"]).stat().st_size for v in entries.values()),
            "first_body_bytes": first_bodies,
            "largest_temporary_stdout_bytes": max_temp,
            "required_collection_reserve_bytes": 10 * 1024**3,
            "source_run": run,
            "manifest_outputs": bodies,
            "limitation": "Excludes future data growth, cache and failure outputs; remeasure immediately before resume.",
        },
        "production_approved": False,
        "operating_applied": False,
        "timer_resumed": False,
    }
    plan["preparation_id"] = c.sha(c.canonical(plan))
    (c.HERE / "prepared-plan.json").write_bytes(c.encode(plan))
    print(
        json.dumps(
            {k: plan[k] for k in ("preparation_id", "release_id", "production_approved")}, indent=2
        )
    )
    print(
        json.dumps(
            {
                "changes": list(changes),
                "t05_scope_before": current_grant["scope_id"],
                "t05_scope_after": proposal["scope_id"],
                "release_files": len(entries),
                "capacity": {k: v for k, v in plan["capacity"].items() if k != "manifest_outputs"},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
