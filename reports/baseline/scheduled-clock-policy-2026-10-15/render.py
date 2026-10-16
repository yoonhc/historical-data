"""Render review-only units/profile linkage. Never write outside this report."""

import hashlib
import json
import shlex
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANDIDATE = HERE / "candidate"
JOBS = (
    "oi",
    "oi-probe",
    "oi-quality",
    "daily",
    "pack",
    "backup",
    "restic-archive",
    "network-observer",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def raw(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def render():
    runtime = {}
    for f in CANDIDATE.rglob("*"):
        rel = f.relative_to(CANDIDATE)
        if not f.is_file() or any(
            part in (".venv", ".cache", ".pytest_cache", "__pycache__", "tests", "build")
            for part in rel.parts
        ):
            continue
        if f.suffix not in (
            ".py",
            ".toml",
            ".json",
            ".lock",
            ".service",
            ".timer",
            ".conf",
            ".env",
        ):
            continue
        runtime[str(rel)] = digest(f.read_bytes())
    source_hash = digest(raw(runtime))
    rid = "scheduled-clock-policy-20261015-" + source_hash[:16]
    release = Path("/opt/marketdata/releases") / rid
    out = HERE / "rendered"
    (out / "units").mkdir(parents=True, exist_ok=True)
    topology_path = "/etc/marketdata/scheduled-system/topology.json"
    topology = json.loads(Path(topology_path).read_bytes())
    profile = json.loads(Path("/etc/marketdata/clock-bootstrap.json").read_bytes())
    before = {}
    after = {}
    unit_specs = {}
    waiter = "marketdata-jobs-clock-ready.service"
    binding = "/etc/marketdata/scheduled-clock-policy/clock-binding.json"
    state = "/home/yoonhc/projects/algoTrade/historical_data/state/scheduled-clock-policy"
    for job in JOBS:
        name = "marketdata-" + job + ".service"
        path = Path("/etc/systemd/system") / name
        text = path.read_text()
        before[str(path)] = digest(text.encode())
        command = [
            line.partition("=")[2]
            for line in text.splitlines()
            if line.startswith("ExecStart=") and line != "ExecStart="
        ][-1]
        argv = shlex.split(command)
        if "-m" in argv:
            args = argv[argv.index("-m") + 2 :]
        else:
            args = argv[4:]  # python -B -I script work arguments
        variant = (
            "daily"
            if job == "daily"
            else "preserve"
            if job in ("backup", "restic-archive")
            else "base"
        )
        command = " ".join(
            [
                str(release / variant / ".venv/bin/python"),
                "-B",
                "-I",
                str(release / "policy/run_job.py"),
                "--job",
                job,
                "--binding",
                binding,
                "--state-root",
                state,
                "--run",
                "--",
                *map(shlex.quote, args),
            ]
        )
        # Canonicalize old command/reset blocks. Keep all unrelated service options.
        lines = []
        for line in text.splitlines():
            if line.startswith(("ExecStart=", "ExecStartPre=")):
                continue
            if line.startswith("# Existing /etc/systemd/user/") or line.startswith(
                "# must remain unchanged:"
            ):
                continue
            lines.append(line.replace("marketdata-clock-ready.service", waiter))
        lines.extend(["[Service]", "ExecStart=" + command])
        content = "\n".join(lines) + "\n"
        (out / "units" / name).write_text(content)
        spec = dict(topology["units"][name])
        spec.update(ExecStart=[command], ExecStartPre=[], After=[waiter], Requires=[waiter])
        topology["units"][name] = spec
        unit_specs[name] = spec
        after[str(path)] = digest(content.encode())
        # Timers are explicitly unchanged, including activation catch-up rules.
        timer = path.with_suffix(".timer")
        (out / "units" / timer.name).write_bytes(timer.read_bytes())
        before[str(timer)] = after[str(timer)] = digest(timer.read_bytes())
    wait_command = f"{release}/base/.venv/bin/python -B -I {release}/policy/run_job.py --job oi --binding {binding} --wait"
    waiter_content = f"""[Unit]
Description=Scheduled job clock observation and same-boot preparation
After=network-online.target
[Service]
Type=oneshot
User=yoonhc
ExecStart={wait_command}
TimeoutStartSec=infinity
RemainAfterExit=no
ProtectClock=yes
Environment=PYTHONDONTWRITEBYTECODE=1
"""
    (out / "units" / waiter).write_text(waiter_content)
    after["/etc/systemd/system/" + waiter] = digest(waiter_content.encode())
    topology["units"][waiter] = {
        "kind": "waiter",
        "fragment": "/etc/systemd/system/" + waiter,
        "User": "yoonhc",
        "ExecStart": [wait_command],
    }
    topology["system_inventory"] = sorted(set(topology["system_inventory"]) | {waiter})
    # v1 remains the inventory reader's schema; policy_changes explicitly records
    # that this is no longer the T15 manager-only configuration.
    topology["policy_changes"] = ["scheduled-decisions-v1; eight jobs only"]
    (out / "topology.json").write_bytes(raw(topology))
    after[topology_path] = digest(raw(topology))
    before[topology_path] = digest(Path(topology_path).read_bytes())
    for path, sha in after.items():
        profile["guard_files"][path] = sha
    (out / "clock-bootstrap.json").write_bytes(raw(profile))
    after["/etc/marketdata/clock-bootstrap.json"] = digest(raw(profile))
    before["/etc/marketdata/clock-bootstrap.json"] = digest(
        Path("/etc/marketdata/clock-bootstrap.json").read_bytes()
    )
    clock_binding = {
        "bootstrap_profile": "/etc/marketdata/clock-bootstrap.json",
        "bootstrap_profile_sha256": digest(raw(profile)),
    }
    (out / "clock-binding.json").write_bytes(raw(clock_binding))
    after[binding] = digest(raw(clock_binding))
    # Existing T05 observer must read the same new profile without re-registering any evidence.
    t05 = "/etc/marketdata/checkpoint-clock-retirement/clock-binding.json"
    before[t05] = digest(Path(t05).read_bytes())
    after[t05] = digest(raw(clock_binding))
    manifest = {
        "release_id": rid,
        "source_sha256": source_hash,
        "runtime_files": runtime,
        "review_only": True,
        "before": before,
        "after": after,
        "unchanged_timers": 8,
        "new_waiter": waiter,
        "production_applied": False,
        "ledger_transition_required": True,
        "collector_bootstrap_policy_unchanged": True,
        "T05_deletion_policy_unchanged": True,
    }
    (HERE / "manifest.json").write_bytes(raw(manifest))
    print(
        json.dumps(
            {
                "release_id": rid,
                "files": len(runtime),
                "rendered_services": 9,
                "timers_unchanged": 8,
            }
        )
    )


if __name__ == "__main__":
    render()
