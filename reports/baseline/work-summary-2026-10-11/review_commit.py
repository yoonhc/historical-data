"""Check the fixed evidence being committed without running operational jobs."""

import ast
import collections
import gzip
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
BASE = "44139687b8d42c3387ab156e5750e85d5715b73a"
NAMES = (
    "archive-content-recheck-2026-10-10", "archive-publication-times-2026-10-10",
    "backup-pipeline-review-2026-10-10", "daily-early-targets-2026-10-10",
    "daily-schedule-history-2026-10-10", "t02-first-24h-2026-10-10",
    "t04-operating-clock-restore-2026-10-10", "t13-daily-deploy-2026-10-11",
    "t13-daily-followup-2026-10-10", "t13-daily-rerun-2026-10-10",
    "t13-daily-review-2026-10-10", "t13-daily-scheduler-2026-10-10",
)
SECRET_PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github_token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "openai_key": re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b"),
}


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def main():
    paths = []
    for name in NAMES:
        directory = ROOT / "reports/baseline" / name
        for p in sorted(directory.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(ROOT))
            ignored = subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=ROOT).returncode == 0
            if not ignored:
                paths.append(p)
    records = [{"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size, "sha256": sha(p.read_bytes())} for p in paths]
    (HERE / "preserved-inputs.json").write_text(json.dumps({"base_commit": BASE, "files": records}, indent=2) + "\n")
    issues, secrets, tests, manifests, releases = [], [], [], [], []
    counts = collections.Counter()
    seen_python = set()

    def content(label, blob):
        for kind, pattern in SECRET_PATTERNS.items():
            if pattern.search(blob):
                secrets.append({"path": label, "pattern": kind})
        if label.endswith(".py") and sha(blob) not in seen_python:
            ast.parse(blob, filename=label)
            seen_python.add(sha(blob))
            counts["unique_python_parsed"] += 1

    for p in paths:
        label = str(p.relative_to(ROOT))
        blob = p.read_bytes()
        content(label, blob)
        if p.suffix == ".json":
            json.loads(blob)
            counts["json"] += 1
        elif p.suffix == ".jsonl":
            for line in blob.splitlines():
                if line.strip():
                    json.loads(line)
            counts["jsonl"] += 1
        elif p.suffix == ".xml":
            tree = ET.fromstring(blob)
            counts["xml"] += 1
            for suite in tree.iter("testsuite"):
                tests.append({"path": label, **{k: suite.get(k) for k in ("tests", "failures", "errors", "skipped")}})
        elif p.suffix == ".gz" and not p.name.endswith(".tar.gz"):
            content(label.removesuffix(".gz"), gzip.decompress(blob))
            counts["gzip"] += 1
        elif p.suffix == ".zip":
            with zipfile.ZipFile(p) as archive:
                assert archive.testzip() is None, label
            counts["zip"] += 1

    base_cache = {}
    for p in paths:
        if p.name != "manifest.json":
            continue
        manifest = json.loads(p.read_text())
        checks = 0
        if "source_archive_sha256" in manifest:
            directory = p.parent
            for filename, field in (("source.tar.gz", "source_archive_sha256"), ("candidate.patch", "candidate_patch_sha256"), ("supplement.patch", "supplement_patch_sha256")):
                if field in manifest:
                    assert sha((directory / filename).read_bytes()) == manifest[field], str(directory / filename)
            expected = manifest["source_sha256"]
            assert sha(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()) == manifest["content_manifest_sha256"]
            with tarfile.open(directory / "source.tar.gz") as archive:
                assert {m.name for m in archive.getmembers()} == {"source/" + n for n in expected}
                for member in archive.getmembers():
                    assert member.isfile() and ".." not in Path(member.name).parts
                    name = member.name.removeprefix("source/")
                    blob = archive.extractfile(member).read()
                    assert sha(blob) == expected[name]
                    content(str(directory.relative_to(ROOT)) + "/source.tar.gz::" + name, blob)
                    if name not in manifest["new_files"]:
                        cache_key = (manifest["base_commit"], name)
                        if cache_key not in base_cache:
                            base_cache[cache_key] = sha(subprocess.check_output(["git", "show", cache_key[0] + ":" + name], cwd=ROOT))
                        assert base_cache[cache_key] == sha(blob), name
                    candidate = directory / "candidate" / name
                    if candidate.exists():
                        assert sha(candidate.read_bytes()) == expected[name], str(candidate)
            releases.append({"manifest": str(p.relative_to(ROOT)), "release_id": manifest["release_id"], "verified_source_files": len(expected)})
        else:
            file_list = manifest.get("files", [])
            entries = [(r["path"], r["sha256"]) for r in file_list] if isinstance(file_list, list) else list(file_list.items())
            entries += list(manifest.get("sha256", {}).items())
            for name, digest in entries:
                target = ROOT / name if name.startswith("reports/") else p.parent / name
                if not target.is_file() or sha(target.read_bytes()) != digest:
                    issues.append({"kind": "manifest_mismatch", "manifest": str(p.relative_to(ROOT)), "path": name})
                checks += 1
            manifests.append({"manifest": str(p.relative_to(ROOT)), "files_checked": checks})

    deploy = ROOT / "reports/baseline/t13-daily-deploy-2026-10-11"
    for name, digest in json.loads((deploy / "deployment-code-sha256.json").read_text()).items():
        assert sha((deploy / name).read_bytes()) == digest, name
    for result, code in (("finish-result.json", "finish_install_daily.py"), ("continuation-result.json", "continue_activation.py")):
        assert json.loads((deploy / result).read_text())["continuation_sha256"] == sha((deploy / code).read_bytes())

    docs = [p for p in paths if p.suffix == ".md"] + [
        ROOT / "TODO.md", ROOT / "reports/README.md", ROOT / "docs/system-runtime-operations.md",
        ROOT / "docs/milestones/2026-10-11-daily-scheduler-and-operating-verification.md", HERE / "summary.md",
    ]
    for p in docs:
        if not p.exists():
            issues.append({"kind": "missing_document", "path": str(p.relative_to(ROOT))})
            continue
        for target in re.findall(r"\]\(([^)]+)\)", p.read_text()):
            target = target.split("#", 1)[0]
            if not target or "://" in target:
                continue
            resolved = (p.parent / target).resolve()
            # This report is emitted immediately after all input checks finish.
            if resolved != HERE / "commit-review.json" and not resolved.exists():
                issues.append({"kind": "missing_link", "document": str(p.relative_to(ROOT)), "target": target})
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(), "base_commit": BASE,
        "preserved_files": len(paths), "preserved_bytes": sum(r["bytes"] for r in records),
        "syntax_and_archives": dict(counts), "release_manifests": releases, "evidence_manifests": manifests,
        "baseline_files_compared_to_git": len(base_cache), "existing_test_results": tests,
        "test_suites_rerun": False, "documents_checked": len(docs),
        "credential_pattern_matches": secrets, "issues": issues,
        "operating_services_or_configuration_changed": False, "passed": not issues and not secrets,
    }
    (HERE / "commit-review.json").write_text(json.dumps(result, indent=2) + "\n")
    assert (HERE / "commit-review.json").is_file()
    print(json.dumps({k: result[k] for k in ("preserved_files", "preserved_bytes", "syntax_and_archives", "baseline_files_compared_to_git", "credential_pattern_matches", "issues", "passed")}, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
