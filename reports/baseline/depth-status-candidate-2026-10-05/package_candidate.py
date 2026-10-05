"""Package an isolated candidate and check the patch without applying it live."""

import ast
import difflib
import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PATHS = [
    "packages/marketdata/src/marketdata/depth.py",
    "packages/marketdata/src/marketdata/depth_stream.py",
    "tests/test_depth_independent_cases.py",
    "tests/depth_failure_harness.py",
    "tests/test_depth_failure_publication.py",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    setup = json.loads((OUT / "isolation.json").read_bytes())
    candidate = Path(setup["candidate"])
    protected = {p: digest(Path(p)) == h for p, h in setup["protected_sha256"].items()}
    assert all(protected.values())
    changed_initial = {
        p for p, h in setup["candidate_initial_sha256"].items() if digest(candidate / p) != h
    }
    assert changed_initial == set(PATHS[:3])
    parts, changes = [], []
    for relative in PATHS:
        old, new = ROOT / relative, candidate / relative
        before, after = old.read_text() if old.exists() else "", new.read_text()
        difference = list(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/" + relative if old.exists() else "/dev/null",
                tofile="b/" + relative,
            )
        )
        assert difference
        parts.append(
            "diff --git a/"
            + relative
            + " b/"
            + relative
            + "\n"
            + ("new file mode 100644\n" if not old.exists() else "")
            + "".join(difference)
        )
        changes.append(
            {
                "path": relative,
                "before_sha256": digest(old) if old.exists() else None,
                "after_sha256": digest(new),
                "added_lines": sum(
                    s.startswith("+") and not s.startswith("+++") for s in difference
                ),
                "removed_lines": sum(
                    s.startswith("-") and not s.startswith("---") for s in difference
                ),
            }
        )
    (OUT / "candidate.patch").write_text("".join(parts))
    definitions = {}
    for relative in PATHS[:2]:

        def symbols(path):
            return {
                n.name: ast.dump(n, include_attributes=False)
                for n in ast.parse(path.read_text()).body
                if isinstance(n, (ast.ClassDef, ast.FunctionDef))
            }

        old, new = symbols(ROOT / relative), symbols(candidate / relative)
        altered = [name for name in old if old[name] != new.get(name)]
        assert altered == ([] if relative.endswith("/depth.py") else ["capture"])
        definitions[relative] = {
            "unchanged_existing_symbols": [name for name in old if name not in altered],
            "changed_existing_symbols": altered,
            "added_symbols": [name for name in new if name not in old],
        }
    policy = "packages/marketdata/src/marketdata/depth_refresh.py"
    assert digest(ROOT / policy) == digest(candidate / policy)
    tests = ET.parse(OUT / "tests.xml").getroot().find("testsuite").attrib
    assert tests["tests"] == "163" and all(
        tests[k] == "0" for k in ("failures", "errors", "skipped")
    )
    patch = subprocess.run(
        ["git", "apply", "--check", str(OUT / "candidate.patch")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(setup["pythonpath"])}
    origins = json.loads(
        subprocess.check_output(
            [
                setup["python"],
                "-c",
                "import json; from marketdata import depth,depth_stream,depth_refresh; "
                "print(json.dumps({m.__name__:m.__file__ for m in (depth,depth_stream,depth_refresh)}))",
            ],
            cwd=candidate,
            env=env,
            text=True,
        )
    )
    assert all(Path(p).is_relative_to(candidate) for p in origins.values())
    result = {
        "base_commit": setup["base_commit"],
        "candidate": str(candidate),
        "changes": changes,
        "ast_review": definitions,
        "refresh_policy_byte_identical": True,
        "protected_files_unchanged": protected,
        "module_origins": origins,
        "tests": tests,
        "patch_check": {"exit_code": patch.returncode, "output": patch.stdout + patch.stderr},
        "packaging_followup": "Initial new-file headers lacked explicit file mode; corrected metadata only.",
        "note": "Check only; candidate.patch has not been applied to the operating workspace.",
    }
    (OUT / "change-review.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "files": len(changes),
                "protected": len(protected),
                "tests": tests,
                "patch_check": patch.returncode,
            }
        )
    )


if __name__ == "__main__":
    main()
