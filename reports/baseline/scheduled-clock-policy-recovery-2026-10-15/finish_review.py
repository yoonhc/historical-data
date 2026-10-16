"""Seal isolated review evidence. Read operating guards; never apply changes."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, value):
    (HERE / name).write_text(json.dumps(value, indent=2) + "\n")


m = json.loads((HERE / "manifest.json").read_bytes())
parents = json.loads((HERE / "parents.json").read_bytes())
prior = json.loads((HERE / "prior-candidate.json").read_bytes())
old = Path(prior["path"])
assert digest(old / "manifest.json") == prior["manifest_sha256"]
for name, sha in prior["runtime_files"].items():
    assert digest(old / "candidate" / name) == sha, name
assert m["release_id"] != prior["release_id"]


def compare(reference, *, require_all=True):
    changed, added, unchanged = [], [], []
    for name, sha in m["runtime_files"].items():
        assert digest(HERE / "candidate" / name) == sha, name
        before = reference.get(name)
        if before == sha:
            unchanged.append(name)
        elif before:
            changed.append({"path": name, "before_sha256": before, "after_sha256": sha})
        else:
            added.append({"path": name, "sha256": sha})
    if require_all:
        assert not set(reference) - set(m["runtime_files"]), "Runtime file removed"
    return {"changed": changed, "added": added, "unchanged": unchanged}


comparison = compare(prior["runtime_files"])
parent_comparison = compare({p: r["sha256"] for p, r in parents["files"].items()}, require_all=False)
old_manifest = json.loads((old / "manifest.json").read_bytes())
assert m["before"] == old_manifest["before"]
for path, sha in m["before"].items():
    assert digest(Path(path)) == sha, path
masks = {}
for directory in ("/etc/systemd/system", "/home/yoonhc/.config/systemd/user"):
    for suffix in ("service", "timer"):
        p = Path(directory) / ("marketdata-checkpoint-retirement." + suffix)
        masks[str(p)] = os.readlink(p)
        assert masks[str(p)] == "/dev/null"
modules = {}
for variant in ("base", "daily", "preserve"):
    root = HERE / "candidate" / variant
    src = root / "packages/marketdata/src/marketdata"
    installed = root / ".venv/lib/python3.12/site-packages/marketdata"
    assert all(digest(p) == digest(installed / p.name) for p in src.glob("*.py")), variant
    assert digest(src / "job_clock.py") == digest(HERE / "candidate/policy/job_clock.py")
    modules[variant] = len(list(src.glob("*.py")))
for prefix, names in {
    "preserve/packages/marketdata/src/marketdata/": ("checkpoint_clock.py", "checkpoint_evidence.py", "backup.py"),
    "preserve/scripts/": ("archive_backups.py", "clock_retirement.py", "retire_checkpoints.py", "checkpoint_schedule.py"),
}.items():
    for name in names:
        rel = prefix + name
        assert digest(HERE / "candidate" / rel) == parents["files"][rel]["sha256"]
state = json.loads(Path("/var/lib/marketdata-clock/boot-state.json").read_bytes())
source = {
    "release_id": m["release_id"], "source_sha256": m["source_sha256"],
    "prior_candidate": {"release_id": prior["release_id"], "unchanged_files_verified": len(prior["runtime_files"])},
    "compared_to_prior_candidate": comparison,
    "compared_to_operating_parent_sources": parent_comparison,
    "candidate_environment_source_copies_equal": modules,
    "operating_configuration_inputs_unchanged": len(m["before"]),
    "automatic_retirement_masks": masks,
    "ledger": {k: state[k] for k in ("boot_id", "phase", "step_attempts")},
    "operating_service_commands_issued": 0, "operating_clock_commands_issued": 0,
    "operating_data_mutations": 0,
}
write("source-review.json", source)
groups = [(v, f"{v}-regression.xml") for v in ("base", "daily", "preserve")]
groups += [("policy-" + v, f"{v}-policy.xml") for v in ("base", "daily", "preserve")]
counts, runs = {}, []
for variant, name in groups:
    root = ET.parse(HERE / name).getroot()
    cases = root.findall(".//testcase")
    assert cases and not any(root.findall(".//" + tag) for tag in ("failure", "error", "skipped")), name
    ids = {(c.attrib["classname"], c.attrib["name"]) for c in cases}
    assert len(ids) == len(cases), name
    counts[variant] = len(ids)
    runs.append({"variant": variant, "report": name, "sha256": digest(HERE / name), "passed": len(ids)})
unit = json.loads((HERE / "unit-verification.json").read_bytes())
assert unit["returncode"] == 0 and unit["units"] == 17
validation = {
    "release_id": m["release_id"], "source_sha256": m["source_sha256"],
    "reviewed_at": datetime.now(timezone.utc).isoformat(), "passed": True,
    "policy_and_recovery_tests": sum(n for v, n in counts.items() if v.startswith("policy-")),
    "regression_tests_per_variant": {v: n for v, n in counts.items() if not v.startswith("policy-")},
    "runs": runs, "unit_syntax_and_order": unit,
    "actual_system_manager_execution": False, "operating_deployed": False,
    "prior_candidate_preserved": True, "T05_deletion_authority": False,
    "limits": ["Provider/network/systemd provenance are isolated doubles in wrapper connection tests",
               "Real restic used only with temporary repositories by existing preservation regressions",
               "Runtime transition, rollback and actual protected system service execution remain pending"],
}
write("validation.json", validation)
print(json.dumps({"release_id": m["release_id"], "passed": True, "counts": counts,
                  "source_changes_from_prior": len(comparison["changed"]), "source_added": len(comparison["added"])}))
