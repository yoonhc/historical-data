"""Freeze the baseline, prerequisites and isolated patch without installing it."""

import ast
import difflib
import gzip
import hashlib
import importlib.metadata
import io
import json
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
CHANGES = [
    "packages/marketdata/src/marketdata/depth.py",
    "packages/marketdata/src/marketdata/depth_stream.py",
    "tests/test_depth_independent_cases.py",
    "tests/depth_failure_harness.py",
    "tests/test_depth_failure_publication.py",
]


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def main():
    setup = json.loads((OUT / "isolation.json").read_bytes())
    candidate = Path(setup["candidate"])
    for name, expected in setup["protected_sha256"].items():
        assert sha(Path(name).read_bytes()) == expected, name
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == setup["base_commit"]
    archive = subprocess.check_output(["git", "archive", "--format=tar", head], cwd=ROOT)
    baseline = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source:
            if member.isdir():
                continue
            assert member.isfile(), member.name
            baseline[member.name] = (source.extractfile(member).read(), member.mode)
    prerequisites = [ROOT / CHANGES[2]] + sorted(
        p for p in (ROOT / "reports/baseline/depth-independent-2026-10-05").iterdir() if p.is_file()
    )
    for path in prerequisites:
        relative = path.relative_to(ROOT).as_posix()
        assert relative not in baseline
        baseline[relative] = (path.read_bytes(), 0o644)
    with (OUT / "baseline.tar.gz").open("xb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as bundle:
                for name, (blob, mode) in sorted(baseline.items()):
                    member = tarfile.TarInfo(name)
                    member.size, member.mode = len(blob), mode
                    bundle.addfile(member, io.BytesIO(blob))
    patch, changed, after = [], [], {p: sha(blob) for p, (blob, _) in baseline.items()}
    for relative in CHANGES:
        before = baseline.get(relative, (b"", 0o644))[0]
        final = (candidate / relative).read_bytes()
        diff = "".join(
            difflib.unified_diff(
                before.decode().splitlines(keepends=True),
                final.decode().splitlines(keepends=True),
                fromfile="a/" + relative if relative in baseline else "/dev/null",
                tofile="b/" + relative,
            )
        )
        assert diff
        patch.append(
            f"diff --git a/{relative} b/{relative}\n"
            + ("new file mode 100644\n" if relative not in baseline else "")
            + diff
        )
        after[relative] = sha(final)
        changed.append(
            {
                "path": relative,
                "before_sha256": sha(before) if relative in baseline else None,
                "after_sha256": sha(final),
            }
        )
    (OUT / "candidate.patch").write_text("".join(patch))
    ast_review = {}
    for relative in CHANGES[:2]:

        def symbols(blob):
            return {
                n.name: ast.dump(n, include_attributes=False)
                for n in ast.parse(blob).body
                if isinstance(n, (ast.ClassDef, ast.FunctionDef))
            }

        original, final = (
            symbols(baseline[relative][0]),
            symbols((candidate / relative).read_bytes()),
        )
        different = [name for name, value in original.items() if final.get(name) != value]
        assert different == ([] if relative.endswith("depth.py") else ["capture"])
        ast_review[relative] = {
            "unchanged_existing": [name for name in original if name not in different],
            "changed_existing": different,
            "new": [name for name in final if name not in original],
        }
    refresh = "packages/marketdata/src/marketdata/depth_refresh.py"
    assert baseline[refresh][0] == (candidate / refresh).read_bytes()
    identity = {
        "base_commit": head,
        "baseline_sha256": sha((OUT / "baseline.tar.gz").read_bytes()),
        "patch_sha256": sha((OUT / "candidate.patch").read_bytes()),
        "changed_files": changed,
    }
    release_id = (
        "depth-status-20261005-"
        + sha(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())[:16]
    )
    result = {
        "release_id": release_id,
        "built_at": datetime.now().astimezone().isoformat(),
        **identity,
        "baseline_files_sha256": {p: sha(blob) for p, (blob, _) in baseline.items()},
        "final_files_sha256": after,
        "prerequisites": [p.relative_to(ROOT).as_posix() for p in prerequisites],
        "ast_review": ast_review,
        "refresh_policy_byte_identical": True,
        "expected_depth_tests": 167,
        "runtime": {
            "python": sys.version,
            "distributions": {
                d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
            },
        },
        "support_files_sha256": {
            name: sha((OUT / name).read_bytes()) for name in ("build_release.py", "reproduce.py")
        },
        "operating_source_applied": False,
        "note": "Self-contained source/fixture baseline; Python dependencies are recorded, not bundled.",
    }
    (OUT / "release.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "release_id": release_id,
                "baseline_files": len(baseline),
                "prerequisites": len(prerequisites),
                "changed_files": len(changed),
                "baseline_bytes": (OUT / "baseline.tar.gz").stat().st_size,
            }
        )
    )


if __name__ == "__main__":
    main()
