"""Local final-review probes; only temporary exports, no operating actions."""

import hashlib
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scripts import render_retirement_units, render_scheduled_units  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} failed: {result.stderr}")
    return result.stdout


def main():
    final = json.loads((HERE / "final-tree.json").read_text())["files"]
    origins = json.loads((HERE / "origins.json").read_text())["files"]
    result = {
        "scope": "development review; no installed service or operating data changed",
        "final_tree": {
            "count": len(final),
            "mismatches": [p for p, h in final.items() if sha(ROOT / p) != h],
        },
        "original_sources": {
            "count": len(origins),
            "mismatches": [
                p["source"] for p in origins if sha(Path(p["source"])) != p["source_sha256"]
            ],
        },
    }
    with tempfile.TemporaryDirectory(prefix="md-final-review-", dir="/tmp") as temporary:
        scratch = Path(temporary)
        release = scratch / "release"
        command([sys.executable, "-B", "-m", "venv", "--without-pip", str(release / ".venv")])
        python = release / ".venv/bin/python"
        installed = (
            Path(
                command(
                    [
                        str(python),
                        "-B",
                        "-I",
                        "-c",
                        "import sysconfig; print(sysconfig.get_path('purelib'))",
                    ]
                ).strip()
            )
            / "marketdata"
        )
        source = release / "packages/marketdata/src/marketdata"
        for target in (source, installed):
            shutil.copytree(
                ROOT / "packages/marketdata/src/marketdata",
                target,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        shutil.copytree(
            ROOT / "integrations/runtime",
            release / "integrations/runtime",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (release / "scripts").mkdir()
        shutil.copyfile(ROOT / "scripts/run_job.py", release / "scripts/run_job.py")
        (release / "candidate.json").write_text('{"release_id":"local-review-only"}\n')
        command([str(python), "-B", "-I", str(release / "integrations/runtime/seal_runtime.py")])
        manifest = json.loads((release / "runtime-manifest.json").read_text())
        hashes = manifest["runtime_files_sha256"]
        # Change only the temporary source copy after sealing. The installed
        # package remains byte-identical; no actual collector/workload is run.
        target = source / "__init__.py"
        target.write_bytes(target.read_bytes() + b"\n# changed temporary source copy\n")
        paths = json.loads(
            command(
                [
                    str(python),
                    "-B",
                    "-I",
                    "-c",
                    """
import importlib.util, json, sys
from pathlib import Path
root = Path(sys.argv[1])
installed_origin = importlib.util.find_spec('marketdata').origin
spec = importlib.util.spec_from_file_location('review_runner', root / 'scripts/run_job.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runner.use_source_tree()
import marketdata
print(json.dumps({'collector_default': installed_origin, 'scheduled': marketdata.__file__}))
""",
                    str(release),
                ]
            )
        )
        result["legacy_sealer_boundary"] = {
            "environment": "temporary real venv, copied package/runtime; no workload dependencies installed",
            "files_sealed": len(hashes),
            "root_source_is_sealed": "packages/marketdata/src/marketdata/__init__.py" in hashes,
            "scheduled_wrapper_is_sealed": "scripts/run_job.py" in hashes,
            "manifest_hashes_still_match_after_source_copy_change": all(
                sha(release / name) == expected for name, expected in hashes.items()
            ),
            "installed_and_source_copy_now_differ": sha(installed / "__init__.py") != sha(target),
            "origins": {name: str(Path(path).relative_to(release)) for name, path in paths.items()},
            "meaning": "collector-only legacy seal is not a complete unified deployment seal",
        }

        units = render_scheduled_units.render(
            release=str(ROOT),
            store_root=str(scratch / "store"),
            user=pwd.getpwuid(os.getuid()).pw_name,
            user_home=str(scratch / "home"),
        )
        profile = {
            "store_root": str(scratch / "store"),
            "backup_mount": str(scratch / "backup"),
            "backup_dir": str(scratch / "backup/marketdata/automatic"),
            "state_root": str(scratch / "archive-state"),
            "expected_repository_id": "a" * 64,
            "cache_root": str(scratch / "cache"),
            "restic_repository": str(scratch / "backup/restic"),
        }
        units.update(
            render_retirement_units.render(
                str(ROOT),
                str(scratch / "settings.toml"),
                str(scratch / "state"),
                profile,
            )
        )
        unit_root = scratch / "units"
        unit_root.mkdir()
        for name, text in units.items():
            (unit_root / name).write_text(text)
        env = {**os.environ, "SYSTEMD_UNIT_PATH": str(unit_root) + ":/usr/lib/systemd/system"}
        checked = subprocess.run(
            [
                "/usr/bin/systemd-analyze",
                "--generators=no",
                "--man=no",
                "--recursive-errors=no",
                "verify",
                *map(str, sorted(unit_root.iterdir())),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        result["rendered_unit_syntax"] = {
            "count": len(units),
            "returncode": checked.returncode,
            "stdout": checked.stdout,
            "stderr": checked.stderr,
            "meaning": "static unit parsing only; no services started or namespace checks performed",
        }
    (HERE / "final-review-probes.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
