"""Create an installation receipt after non-editable uv sync in a fresh release path."""

import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[2]
    if Path(sys.prefix).resolve() != root / ".venv":
        raise SystemExit("Run with this release's .venv/bin/python -I")
    spec = importlib.util.find_spec("marketdata")
    package = Path(spec.origin).parent.resolve()
    if not package.is_relative_to(root / ".venv"):
        raise SystemExit("Non-editable installation required")
    metadata = json.loads((root / "candidate.json").read_text())
    files = sorted(package.rglob("*.py")) + sorted((root / "integrations/runtime").glob("*.py"))
    checksums = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    distributions = {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}
    receipt = {
        "schema": 1,
        "release_id": metadata["release_id"],
        "python": platform.python_version(),
        "runtime_files_sha256": checksums,
        "distributions": distributions,
        "marketdata_origin": str(package),
        "editable": False,
    }
    (root / "runtime-manifest.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "release_id": receipt["release_id"],
                "checked_files": len(files),
                "marketdata_origin": str(package),
            }
        )
    )


if __name__ == "__main__":
    main()
