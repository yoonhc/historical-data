"""Reuse the installed-source patch builder without replacing the reviewed v1."""

import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "t18-capacity-recovery-2026-10-19"


if __name__ == "__main__":
    source = PREVIOUS / "build_candidate.py"
    spec = importlib.util.spec_from_file_location("t18_installed_builder", source)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.HERE = HERE
    builder.main()
    inputs = [source, Path(__file__), PREVIOUS / "candidate-source.json"]
    (HERE / "build-inputs.json").write_text(json.dumps({
        "files": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
        "previous_candidate_preserved": True,
    }, indent=2) + "\n")
