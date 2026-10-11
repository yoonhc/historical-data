"""Validate fixed commit evidence; never import or run operational code."""

import ast
import collections
import gzip
import hashlib
import json
import re
import subprocess
import tarfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
NAMES = (
    "t05-clock-retirement-2026-10-11",
    "t05-completion-evidence-2026-10-11",
    "t05-evidence-deploy-2026-10-11",
    "t04-restore-cleanup-2026-10-11",
    "t13-operating-review-2026-10-11",
)
DOCS = (
    "TODO.md", "reports/README.md", "docs/system-runtime-operations.md",
    "docs/t05-checkpoint-clock-safety-design.md",
    "docs/milestones/2026-10-11-checkpoint-evidence-and-daily-processing.md",
)
PATTERNS = {
    "private_key": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "aws_access_key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "github_token": rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
    "openai_key": rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b",
}


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def canonical(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


def read(path):
    return json.loads(path.read_bytes())


def main():
    directories = ["reports/baseline/" + name for name in NAMES]
    selected = subprocess.check_output([
        "git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *directories,
    ], cwd=ROOT).decode().split("\0")
    paths = sorted({ROOT / name for name in selected if name} | {ROOT / name for name in DOCS})
    records = [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size, "sha256": sha(p.read_bytes())} for p in paths]
    (HERE / "preserved-inputs.json").write_text(json.dumps({"base_commit": "bf630cc", "files": records}, indent=2) + "\n")
    counts, python_hashes = collections.Counter(), set()
    secrets, issues, tests, releases = [], [], [], []

    def inspect(label, blob):
        for kind, pattern in PATTERNS.items():
            if re.search(pattern, blob):
                secrets.append({"path": label, "pattern": kind})
        if label.endswith(".py"):
            if sha(blob) not in python_hashes:
                ast.parse(blob, filename=label)
                python_hashes.add(sha(blob))
                counts["unique_python_syntax"] += 1
        elif label.endswith(".json"):
            json.loads(blob)
            counts["json"] += 1
        elif label.endswith(".jsonl"):
            rows = 0
            for line in blob.splitlines():
                if line.strip():
                    json.loads(line)
                    rows += 1
            counts["jsonl_files"] += 1
            counts["jsonl_rows"] += rows
        elif label.endswith(".xml"):
            tree = ET.fromstring(blob)
            counts["xml"] += 1
            for suite in tree.iter("testsuite"):
                tests.append({"path": label, **{k: suite.get(k) for k in ("tests", "failures", "errors", "skipped")}})

    for path in paths + [HERE / "review_commit.py"]:
        label = str(path.relative_to(ROOT))
        blob = path.read_bytes()
        inspect(label, blob)
        if path.name.endswith(".gz") and not path.name.endswith(".tar.gz"):
            inspect(label.removesuffix(".gz"), gzip.decompress(blob))
            counts["gzip"] += 1

    for name in NAMES[:2]:
        directory = ROOT / "reports/baseline" / name
        manifest = read(directory / "manifest.json")
        expected = manifest["source_sha256"]
        assert canonical(expected) == manifest["content_manifest_sha256"]
        for filename, field in (("source.tar.gz", "source_archive_sha256"), ("candidate.patch", "patch_sha256")):
            assert sha((directory / filename).read_bytes()) == manifest[field], filename
        with tarfile.open(directory / "source.tar.gz") as archive:
            members = archive.getmembers()
            assert len(members) == len(expected) and {m.name for m in members} == set(expected)
            for member in members:
                assert member.isfile() and not Path(member.name).is_absolute() and ".." not in Path(member.name).parts
                blob = archive.extractfile(member).read()
                assert sha(blob) == expected[member.name], member.name
                inspect(name + "/source.tar.gz::" + member.name, blob)
        copies = []
        for subdir in ("work", "reproduced"):
            if (directory / subdir).is_dir():
                assert all(sha((directory / subdir / p).read_bytes()) == h for p, h in expected.items())
                copies.append(subdir)
        releases.append({"release_id": manifest["release_id"], "source_files": len(expected), "local_copies_match": copies})

    deploy = ROOT / "reports/baseline/t05-evidence-deploy-2026-10-11"
    linked = 0
    maps = [
        (ROOT, read(deploy / "install-inputs.json")["files"]),
        (deploy, read(deploy / "activation-recovery-result.json")["provenance_sha256"]),
        (deploy, read(deploy / "deployment-receipt.json")["receipt_sha256"]),
        (deploy / "root-receipts", read(deploy / "root-receipts/sha256.json")),
    ]
    for directory, mapping in maps:
        for name, expected in mapping.items():
            assert sha((directory / name).read_bytes()) == expected, name
            linked += 1
    final = ROOT / "reports/baseline/t05-completion-evidence-2026-10-11"
    manifest = read(final / "manifest.json")
    assert sha((final / "freeze_candidate.py").read_bytes()) == manifest["freeze_script_sha256"]
    design_provenance = {
        "manifest_design_sha256": manifest["design_sha256"],
        "current_document_sha256": sha((ROOT / "docs/t05-checkpoint-clock-safety-design.md").read_bytes()),
        "scope": "Current design document includes post-installation status; do not treat it as the frozen document bytes.",
        "original_design_snapshot_verified": False,
    }
    design_provenance["same_bytes"] = design_provenance["manifest_design_sha256"] == design_provenance["current_document_sha256"]

    cleanup = ROOT / "reports/baseline/t04-restore-cleanup-2026-10-11"
    for item in read(cleanup / "preserved-evidence.json")["files"]:
        stored = (cleanup / item["preserved_path"]).read_bytes()
        assert sha(stored) == item["preserved_sha256"]
        original = gzip.decompress(stored) if item["original_path"].endswith(".jsonl") else stored
        assert sha(original) == item["original_sha256"]
    assert sha((cleanup / "evidence/whole-t04-inventory.jsonl.gz").read_bytes()) == read(cleanup / "parent-plan.json")["inventory_sha256"]
    assert sha(gzip.decompress((cleanup / "evidence/parent-deletion-journal.jsonl.gz").read_bytes())) == read(cleanup / "parent-recovery-result.json")["journal_sha256"]

    t13 = ROOT / "reports/baseline/t13-operating-review-2026-10-11"
    targets = read(t13 / "completion-targets.json")
    target_id = targets.pop("target_id")
    assert canonical(targets) == target_id
    for name, expected in targets["evidence_sha256"].items():
        assert sha((t13 / name).read_bytes()) == expected, name
    ledger = json.loads(gzip.decompress((t13 / "ledger-records.json.gz").read_bytes()))["live"]
    attempt_hashes = {canonical(x) for x in ledger["daily_attempts"]}
    task_hashes = {canonical(x) for x in ledger["daily_tasks"]}
    assert len(targets["attempts"]) == 26 and len(targets["selected_task_ids"]) == 32
    for attempt in targets["attempts"]:
        assert attempt["attempt_record_sha256"] in attempt_hashes
        assert attempt["task_record_sha256"] in task_hashes
        assert canonical(attempt["proof"]) == attempt["completed_proof_sha256"]
    for task in targets["shared_success_refreshes"]:
        assert task["task_record_sha256"] in task_hashes

    docs = [p for p in paths if p.suffix == ".md"] + [HERE / "summary.md"]
    for path in docs:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            target = target.split("#", 1)[0]
            if not target or "://" in target:
                continue
            resolved = (path.parent / target).resolve()
            if resolved != HERE / "commit-review.json" and not resolved.exists():
                issues.append({"kind": "missing_link", "path": str(path.relative_to(ROOT)), "target": target})

    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(), "base_commit": "bf630cc",
        "preserved_files": len(records), "preserved_bytes": sum(x["bytes"] for x in records),
        "content_checks": dict(counts), "releases": releases, "deployment_hash_links": linked,
        "design_document_provenance": design_provenance,
        "t04_final_evidence_matches": True, "t13_fixed_target_id": target_id,
        "documents_checked": len(docs), "existing_test_results": tests, "test_suites_rerun": False,
        "credential_pattern_matches": secrets, "issues": issues,
        "operating_changes": False, "passed": not issues and not secrets,
    }
    (HERE / "commit-review.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("preserved_files", "preserved_bytes", "content_checks", "deployment_hash_links", "issues", "credential_pattern_matches", "passed")}, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
