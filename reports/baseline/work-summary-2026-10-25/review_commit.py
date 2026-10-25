"""Check Git evidence without importing deployment tools or reading live state."""

import ast
import collections
import hashlib
import json
import re
import subprocess
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = ROOT / "reports/baseline"
BASE_COMMIT = "6308c7f"
NAMES = (
    "archive-capacity-planning-2026-10-25",
    "collector-capacity-incident-2026-10-19",
    "scheduled-clock-operating-review-2026-10-18",
    "t05-disabled-scope-2026-10-17",
    "t05-first-manual-2026-10-17",
    "t05-first-manual-exdev-2026-10-17",
    "t05-first-manual-recovery-2026-10-17",
    "t05-first-manual-resume-2026-10-17",
    "t05-formal-preview-2026-10-17",
    "t05-mount-recovery-2026-10-17",
    "t05-readiness-2026-10-17",
    "t05-recurring-candidate-2026-10-18",
    "t05-recurring-deploy-2026-10-18",
    "t05-recurring-operating-completion-2026-10-19",
    "t05-recurring-preparation-2026-10-18",
    "t17-oi-archive-investigation-2026-10-19",
    "t17-oi-rest-comparison-2026-10-19",
    "t17-oi-source-check-2026-10-19",
    "t18-archive-deploy-2026-10-22",
    "t18-archive-deploy-2026-10-25",
    "t18-archive-manager-recovery-2026-10-25",
    "t18-archive-recovery-2026-10-25",
    "t18-capacity-recovery-2026-10-19",
    "t18-incident-completion-2026-10-25",
    "t18-output-durability-2026-10-22",
    "t19-capacity-apply-2026-10-25",
)
DOCS = (
    ".gitignore", "README.md", "TODO.md", "reports/README.md", "docs/system-runtime-operations.md",
    "docs/milestones/2026-10-25-retirement-and-archive-capacity-recovery.md",
    "reports/baseline/t05-evidence-deploy-2026-10-11/status.md",
    "scripts/archive_backups.py", "scripts/archive_output.py",
    "tests/test_archive_output.py", "tests/test_restic_archive.py",
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


def write(name, data):
    (HERE / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def git_paths(*args):
    raw = subprocess.check_output(["git", "ls-files", *args, "-z"], cwd=ROOT)
    return {ROOT / x for x in raw.decode().split("\0") if x}


def main():
    available = git_paths("--cached", "--others", "--exclude-standard")
    roots = [BASE / name for name in NAMES]
    selected = {p for p in available if any(p.is_relative_to(d) for d in roots)}
    selected.update(ROOT / name for name in DOCS)
    paths = sorted(selected)
    issues, secrets, hash_links, archives, tests, external_refs = [], [], [], [], [], []
    frozen_doc_links = []
    counts, syntax = collections.Counter(), set()
    records = []

    def inspect(label, blob):
        for kind, pattern in PATTERNS.items():
            if re.search(pattern, blob):
                secrets.append({"path": label, "pattern": kind})
        try:
            if label.endswith(".py") and sha(blob) not in syntax:
                ast.parse(blob, filename=label)
                syntax.add(sha(blob))
                counts["unique_python_syntax"] += 1
            elif label.endswith(".json"):
                json.loads(blob)
                counts["json"] += 1
            elif label.endswith(".jsonl"):
                for line in blob.splitlines():
                    if line.strip():
                        json.loads(line)
                        counts["jsonl_rows"] += 1
                counts["jsonl_files"] += 1
            elif label.endswith(".xml"):
                tree = ET.fromstring(blob)
                counts["xml"] += 1
                for suite in tree.iter("testsuite"):
                    tests.append({"path": label, **{
                        key: suite.get(key) for key in ("tests", "failures", "errors", "skipped")
                    }})
        except (SyntaxError, ValueError, ET.ParseError) as exc:
            issues.append({"kind": "parse", "path": label, "error": str(exc)})

    # No imports or execution of operational evidence. Archives are read in memory.
    for path in paths:
        label = str(path.relative_to(ROOT))
        if path.is_symlink() or not path.is_file():
            issues.append({"kind": "nonregular", "path": label})
            continue
        blob = path.read_bytes()
        records.append({"path": label, "bytes": len(blob), "sha256": sha(blob)})
        inspect(label, blob)
        if path.name.endswith(".tar.gz"):
            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    assert member.isfile() and not Path(member.name).is_absolute()
                    assert ".." not in Path(member.name).parts
                    inspect(label + "::" + member.name, archive.extractfile(member).read())
                archives.append({"path": label, "members": len(archive.getmembers())})
        elif path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                assert archive.testzip() is None
                for name in archive.namelist():
                    assert not Path(name).is_absolute() and ".." not in Path(name).parts
                    inspect(label + "::" + name, archive.read(name))
                archives.append({"path": label, "members": len(archive.namelist())})

    write("preserved-inputs.json", {"base_commit": BASE_COMMIT, "files": records})

    def compare(path, expected, source):
        path = path.resolve()
        # Hash-only references to live state / external environments are not read.
        assert path.is_relative_to(ROOT), str(path)
        label = str(path.relative_to(ROOT))
        if path not in available:
            issues.append({"kind": "required_input_not_preserved", "path": label, "source": source})
            return
        actual = sha(path.read_bytes())
        hash_links.append({"path": label, "source": source, "sha256": actual})
        if actual != expected:
            issues.append({"kind": "hash_mismatch", "path": label, "source": source,
                           "expected": expected, "actual": actual})

    def mapping(directory, values, source):
        for name, value in values.items():
            digest = value.get("sha256") if isinstance(value, dict) else value
            if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                continue
            path = Path((value.get("copy") or name) if isinstance(value, dict) else name)
            if not path.is_absolute():
                path = directory / path
            if not path.is_relative_to(ROOT):
                external_refs.append({"path": str(path), "source": source, "read": False})
                continue
            compare(path, digest, source)

    # Frozen local evidence manifests, not current-state or protected-input maps.
    for directory in roots:
        for filename in ("evidence-manifest.json", "operating-manifest.json", "artifacts.json",
                         "activation-artifacts.json"):
            p = directory / filename
            if not p.exists():
                continue
            document = read(p)
            values = document.get("files", document)
            if isinstance(values, list):
                values = {v.get("copy") or v["path"]: v for v in values}
            mapping(directory, values, str(p.relative_to(ROOT)))

    candidate = BASE / "t05-recurring-candidate-2026-10-18"
    manifest = read(candidate / "manifest.json")
    compare(candidate / "source.tar.gz", manifest["source_archive_sha256"], "T05 source archive")
    compare(candidate / "candidate.patch", manifest["patch_sha256"], "T05 candidate patch")
    with tarfile.open(candidate / "source.tar.gz") as archive:
        assert set(archive.getnames()) == set(manifest["source_sha256"])
        for name, digest in manifest["source_sha256"].items():
            assert sha(archive.extractfile(name).read()) == digest, name
    mapping(candidate, read(candidate / "preflight-inputs.json")["files"], "T05 preflight seal")

    recurring = BASE / "t05-recurring-deploy-2026-10-18"
    operation = read(recurring / "operation-plan.json")
    assert operation["operation_plan_id"] == "2c03a81f309d0eca6f03e24a5d84fb838d75598fd1d38dbc6dd2a4204a57ae6e"
    mapping(recurring, operation["tool_files"], "T05 recurring tools")
    mapping(recurring, operation["verification_inputs"], "T05 verification inputs")

    deploy = BASE / "t18-archive-deploy-2026-10-25"
    compare(deploy / "operation-plan.json",
            "5fe462588a59c9bfecc94c68d5473143bb4f1e02c56ca834744acd8bb02f2198", "T18 approved JSON")
    operation = read(deploy / "operation-plan.json")
    mapping(deploy, operation["tool_files"], "T18 original tools")
    mapping(deploy, operation["evidence_files"], "T18 refrozen evidence")
    mapping(deploy, operation["isolated_tests"], "T18 reused tests")
    prepared = read(deploy / "prepared-plan.json")
    for name, value in prepared["release_entries"].items():
        assert value["kind"] == "file", name
        compare(Path(value["source"]), value["sha256"], "T18 fixed source")
    runtime = BASE / "t18-archive-deploy-2026-10-22/runtime/preserve"
    name = "scripts/archive_output.py"
    compare(ROOT / name, sha((runtime / name).read_bytes()), "shared helper equals deployed source")

    def match_body(path):
        tree = ast.parse(path.read_bytes())
        archive = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Archive")
        return ast.dump(next(n for n in archive.body if isinstance(n, ast.FunctionDef) and n.name == "match"))

    # The sealed preserve variant also contains earlier T05 evidence integration.
    # Compare the changed call boundary rather than claim whole-file identity.
    assert match_body(ROOT / "scripts/archive_backups.py") == match_body(runtime / "scripts/archive_backups.py")

    for name in ("t18-archive-recovery-2026-10-25", "t18-archive-manager-recovery-2026-10-25"):
        directory = BASE / name
        recovery = read(directory / "recovery-plan.json")
        mapping(directory, recovery["files"], name)
        mapping(directory, recovery["evidence_files"], name + " evidence")

    capacity = BASE / "t19-capacity-apply-2026-10-25"
    compare(capacity / "operation.json",
            "98f9cb59adc39581bfa3d3d6c32379e686b4580b270574a21c4c56c1d141b3e4", "T19 approved JSON")
    mapping(capacity, read(capacity / "operation.json")["operation"]["bundle_sha256"], "T19 exact tools")
    assert read(capacity / "audit/complete.json")["passed"] is True

    # Report broken historical links without altering sealed evidence to hide them.
    documents = [p for p in paths if p.suffix == ".md"] + [HERE / "summary.md"]
    generated = {HERE / name for name in ("commit-review.json", "preserved-inputs.json", "staging-review.json")}
    for path in documents:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            target = target.split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (path.parent / target).resolve()
            if resolved in generated:
                continue
            if not resolved.exists():
                item = {"kind": "missing_link", "path": str(path.relative_to(ROOT)), "target": target}
                if path.is_relative_to(runtime):
                    frozen_doc_links.append({**item, "reason": "Inherited README in sealed source subset; original bytes preserved."})
                else:
                    issues.append(item)
            elif resolved.is_file() and resolved.is_relative_to(ROOT) and resolved not in available:
                issues.append({"kind": "link_not_preserved", "path": str(path.relative_to(ROOT)), "target": target})

    for path in (HERE / "review_commit.py", HERE / "summary.md", HERE / "focused-tests.xml"):
        inspect(str(path.relative_to(ROOT)), path.read_bytes())
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(), "base_commit": BASE_COMMIT,
        "preserved_files": len(records), "preserved_bytes": sum(x["bytes"] for x in records),
        "content_checks": dict(counts), "archives": archives,
        "t05_archive_source_files": len(manifest["source_sha256"]), "hash_links": hash_links,
        "external_hash_references_not_read": external_refs,
        "frozen_source_document_links": frozen_doc_links,
        "workspace_deployed_integration_match": True,
        "test_results": tests, "documents_checked": len(documents),
        "credential_pattern_matches": secrets, "issues": issues,
        "live_host_rechecked": False, "operating_changes": False,
        "excluded_runtime_lock": str((capacity / "audit/operation.lock").relative_to(ROOT)),
        "passed": not issues and not secrets,
    }
    write("commit-review.json", result)
    print(json.dumps({k: result[k] for k in (
        "preserved_files", "preserved_bytes", "content_checks", "archives", "t05_archive_source_files",
        "credential_pattern_matches", "issues", "passed",
    )}, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
