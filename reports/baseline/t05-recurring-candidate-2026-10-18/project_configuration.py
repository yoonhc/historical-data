"""Calculate review-only post-install scope. Never write to /etc or /opt."""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"
sys.path[:0] = [str(WORK), str(WORK / "packages/marketdata/src")]
from marketdata.domain import json_bytes, sha256  # noqa: E402

from scripts import clock_retirement as safe  # noqa: E402


def main():
    manifest = json.loads((HERE / "manifest.json").read_bytes())
    deployed = json.loads((HERE / "deployment-files.json").read_bytes())
    settings = safe.Settings.load("/etc/marketdata/checkpoint-clock-retirement/settings.toml")
    proposal = safe.proposal(settings)
    before = json.loads(settings.authorization_file.read_bytes())
    final_root = "/opt/marketdata/releases/" + manifest["release_id"]
    code = proposal["scope"]["code"]
    code["code_root"] = final_root
    code.pop("code_id")
    code["code_id"] = sha256(json_bytes(code))
    proposal["scope"]["system_units"] = {n: v["sha256"] for n, v in deployed["files"].items()}
    proposal["scope_id"] = sha256(json_bytes(proposal["scope"]))
    assert (
        proposal["enabled"] is False
        and proposal["approved_at"] is None
        and proposal["approval_reference"] is None
    )
    changed = sorted(k for k in proposal["scope"] if proposal["scope"][k] != before["scope"].get(k))
    assert changed == ["code", "system_units"], changed
    output = HERE / "projected-recurring.disabled.json"
    output.write_bytes(json_bytes(proposal))
    summary = {
        "review_only": True,
        "release_id": manifest["release_id"],
        "final_release_path": final_root,
        "current_scope_id": before["scope_id"],
        "projected_scope_id": proposal["scope_id"],
        "changed_scope_fields": changed,
        "approval_enabled": False,
        "projected_disabled_grant_sha256": sha256(output.read_bytes()),
        "after_install_requirement": "Recompute via installed propose as UID 1000 and require exact equality before any activation.",
        "operating_writes": False,
    }
    (HERE / "scope-projection.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
