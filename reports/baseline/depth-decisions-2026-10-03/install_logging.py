"""Install the validated logging-only files without signalling any collector."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
STAGE = Path("/tmp/marketdata-depth-decisions-20261003")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    release_path = OUT / "release.json"
    if release_path.exists():
        raise RuntimeError("Release already recorded; this installer is not a retry command")
    validation = json.loads((OUT / "validation.json").read_bytes())
    for rel, expected in validation["source_sha256"].items():
        assert digest(STAGE / rel) == expected, rel
    for rel, expected in validation["original_runtime_sha256"].items():
        assert digest(ROOT / rel) == expected, rel
    for path, expected in validation["protected_input_sha256"].items():
        assert digest(Path(path)) == expected, path
    original = json.loads((ROOT / "state/depth-latest.json").read_bytes())
    assert original["state"] == "running" and "refresh_decisions" not in original
    started = datetime.now(timezone.utc)
    age = (started - datetime.fromisoformat(original["started_at"])).total_seconds()
    assert original["seconds_requested"] - age > 120, "Too close to natural process rotation"
    paths = list(validation["source_sha256"])
    # Prepare every replacement first. Install the backwards-compatible helper
    # before the stream module. The running process retains its loaded modules.
    prepared, changes = [], {}
    for rel in paths:
        target = ROOT / rel
        changes[rel] = {
            "old_sha256": digest(target) if target.exists() else None,
            "new_sha256": validation["source_sha256"][rel],
        }
        temporary = target.with_name(target.name + ".logging-install.tmp")
        with temporary.open("xb") as handle:
            handle.write((STAGE / rel).read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        prepared.append((temporary, target))
    for temporary, target in prepared:
        temporary.replace(target)
    for rel, expected in validation["source_sha256"].items():
        assert digest(ROOT / rel) == expected, rel
    for path, expected in validation["protected_input_sha256"].items():
        assert digest(Path(path)) == expected, path
    current = json.loads((ROOT / "state/depth-latest.json").read_bytes())
    release = {
        "started_at": started.isoformat(),
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "files": changes,
        "current_capture_before": original,
        "current_capture_after_id": current["id"],
        "running_capture_unchanged": current["id"] == original["id"],
        "request_policy": original["coverage_refresh"],
        "protected_input_sha256": validation["protected_input_sha256"],
        "no_services_restarted": True,
        "activation": "pending next natural hourly process; source installation is not activation",
    }
    release_path.write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: release[k] for k in ("installed_at", "running_capture_unchanged", "activation")}
        )
    )


if __name__ == "__main__":
    main()
