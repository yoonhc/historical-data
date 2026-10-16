"""Read frozen evidence without importing or executing operational modules."""

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
BASE = ROOT / "reports/baseline"
NAMES = (
    "t13-operating-completion-2026-10-12",
    "t13-trial-cleanup-2026-10-12",
    "scheduled-system-migration-2026-10-12",
    "scheduled-system-handoff-2026-10-12",
    "scheduled-system-deploy-2026-10-13",
    "scheduled-system-operating-completion-2026-10-13",
    "scheduled-clock-policy-2026-10-15",
    "scheduled-clock-policy-recovery-2026-10-15",
    "scheduled-clock-policy-storage-2026-10-16",
    "scheduled-clock-handoff-2026-10-16",
    "scheduled-clock-deploy-2026-10-16",
    "scheduled-clock-result-fix-2026-10-16",
    "scheduled-clock-repair-deploy-2026-10-16",
)
DOCS = (
    ".gitignore", "TODO.md", "reports/README.md",
    "docs/system-runtime-operations.md",
    "docs/scheduled-jobs-system-migration-contract.md",
    "docs/milestones/2026-10-17-scheduled-jobs-clock-policy-and-recovery.md",
    "reports/baseline/t05-evidence-deploy-2026-10-11/status.md",
)
PATTERNS = {
    "private_key": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "aws_access_key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "github_token": rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
    "openai_key": rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b",
}


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def write(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    directories = [str((BASE / name).relative_to(ROOT)) for name in NAMES]
    raw = subprocess.check_output([
        "git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *directories,
    ], cwd=ROOT)
    paths = sorted({ROOT / name for name in raw.decode().split("\0") if name}
                   | {ROOT / name for name in DOCS})
    records = [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size,
                "sha256": sha(p.read_bytes())} for p in paths]
    write("preserved-inputs.json", {"base_commit": "51a997c", "files": records})
    selected = set(paths)
    counts, python_hashes = collections.Counter(), set()
    issues, secrets, tests, releases, seals = [], [], [], [], []

    def inspect(label, blob):
        for kind, pattern in PATTERNS.items():
            if re.search(pattern, blob):
                secrets.append({"path": label, "pattern": kind})
        if label.endswith(".py"):
            digest = sha(blob)
            if digest not in python_hashes:
                ast.parse(blob, filename=label)
                python_hashes.add(digest)
                counts["unique_python_syntax"] += 1
        elif label.endswith(".json"):
            json.loads(blob)
            counts["json"] += 1
        elif label.endswith(".jsonl"):
            rows = sum(1 for line in blob.splitlines() if line.strip() and json.loads(line) is not None)
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
        elif path.name.endswith(".tar.gz"):
            with tarfile.open(path) as tar:
                members = tar.getmembers()
                for member in members:
                    assert member.isfile() and not Path(member.name).is_absolute()
                    assert ".." not in Path(member.name).parts
                    inspect(label + "::" + member.name, tar.extractfile(member).read())
                counts["tar_archives"] += 1
                counts["tar_files"] += len(members)

    def compare(directory, mapping):
        for name, expected in mapping.items():
            path = directory / name
            assert sha(path.read_bytes()) == expected, str(path)
            # Every required local source/input must also be retained by Git.
            if path not in selected:
                tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", str(path)],
                                         cwd=ROOT, capture_output=True)
                assert tracked.returncode == 0, "Required input excluded from Git: " + str(path)
        return len(mapping)

    for name in NAMES:
        directory = BASE / name
        manifest_path = directory / "manifest.json"
        if manifest_path.exists():
            manifest = read(manifest_path)
            mapping = manifest.get("runtime_files")
            if not mapping and isinstance(manifest.get("source_sha256"), dict):
                mapping = manifest["source_sha256"]
            if mapping:
                count = compare(directory / "candidate", mapping)
                item = {"release_id": manifest["release_id"], "source_files": count}
                archive = directory / "source.tar.gz"
                if archive.exists():
                    with tarfile.open(archive) as tar:
                        members = tar.getmembers()
                        assert {m.name for m in members} == set(mapping)
                        assert len(members) == len(mapping)
                        for member in members:
                            assert member.isfile() and not Path(member.name).is_absolute()
                            assert ".." not in Path(member.name).parts
                            blob = tar.extractfile(member).read()
                            assert sha(blob) == mapping[member.name]
                    item["archive_matches"] = True
                releases.append(item)
        for seal_name in ("seal.json", "tool-seal.json"):
            path = directory / seal_name
            if path.exists():
                seal = read(path)
                count = compare(directory, seal["files"])
                if "base_files" in seal:
                    count += compare(BASE / "scheduled-system-migration-2026-10-12", seal["base_files"])
                seals.append({"path": str(path.relative_to(ROOT)), "hash_links": count})

    t13 = BASE / "t13-operating-completion-2026-10-12"
    report_links = compare(t13, read(t13 / "report-manifest.json")["files_sha256"])
    t15 = BASE / "scheduled-system-operating-completion-2026-10-13"
    completion = read(t15 / "completion.json")
    assert completion["passed"] is True
    report_links += compare(t15, completion["evidence"])
    repair = BASE / "scheduled-clock-repair-deploy-2026-10-16/operating-evidence"
    reviews = ["post-apply-native.json", "recovery-review.json", "initial-jobs-review.json",
               "initial-runtime-journal-review.json", "pack-and-prior-state-review.json"]
    for name in reviews:
        assert read(repair / name)["passed"] is True, name
    root_attempt = read(repair / "root-attempt.json")
    assert root_attempt["passed"] is True
    assert root_attempt["plan_id"] == "8ce6ba51659556e5901e85c81694a9d585c0817dbc9a0c34c990547467217f31"
    assert root_attempt["release_id"] == "scheduled-clock-policy-20261016-686bb70c9ee4ef11"

    excluded = []
    for path in BASE.glob("scheduled-clock-*/candidate/base/integrations/freqtrade/user_data/backtest_results/*.zip"):
        assert path not in selected
        directory = path.parents[6]
        manifest = read(directory / "manifest.json")
        assert str(path.relative_to(directory / "candidate")) not in manifest["runtime_files"]
        excluded.append({"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size,
                         "reason": "Copied backtest archive outside sealed runtime manifest; kept locally."})

    docs = [p for p in paths if p.suffix == ".md" and "candidate" not in p.parts]
    docs.append(HERE / "summary.md")
    tracked = {ROOT / p for p in subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0") if p}
    for path in docs:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            target = target.split("#", 1)[0]
            if not target or "://" in target:
                continue
            resolved = (path.parent / target).resolve()
            if resolved == HERE / "commit-review.json":
                continue
            if not resolved.exists():
                issues.append({"kind": "missing_link", "path": str(path.relative_to(ROOT)), "target": target})
            elif resolved.is_file() and resolved.is_relative_to(ROOT) and resolved not in selected | tracked and not resolved.is_relative_to(HERE):
                issues.append({"kind": "link_not_preserved", "path": str(path.relative_to(ROOT)), "target": target})

    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(), "base_commit": "51a997c",
        "preserved_files": len(records), "preserved_bytes": sum(x["bytes"] for x in records),
        "content_checks": dict(counts), "releases": releases, "seals": seals,
        "report_hash_links": report_links, "decimal_repair_reviews_passed": reviews,
        "documents_checked": len(docs), "existing_test_results": tests,
        "test_suites_rerun": False, "operating_changes": False,
        "excluded_local_archives": excluded, "credential_pattern_matches": secrets,
        "issues": issues, "passed": not issues and not secrets,
    }
    write("commit-review.json", result)
    print(json.dumps({k: result[k] for k in (
        "preserved_files", "preserved_bytes", "content_checks", "releases",
        "report_hash_links", "issues", "credential_pattern_matches", "passed",
    )}, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
