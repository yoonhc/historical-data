"""Write review-only deployment inputs beside this script; do not install them."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "work"))
from scripts.render_retirement_units import evidence_dropins, render


def main():
    manifest = json.loads((HERE / "manifest.json").read_text())
    base = HERE.parent / "t05-clock-retirement-2026-10-11/deployment"
    output = HERE / "deployment"
    output.mkdir(exist_ok=True)
    value = json.loads((base / "render-input.json").read_text())
    value["release"] = "/opt/marketdata/releases/" + manifest["release_id"]
    for name in ("settings.toml", "policy.toml", "clock-binding.json"):
        shutil.copyfile(base / name, output / name)
    files = {"units/" + n: v for n, v in render(**value).items()}
    dropins = evidence_dropins(value["release"], value["settings"],
                              "/home/yoonhc/.config/marketdata/restic-archive.toml")
    files.update({"user-dropins/" + n: v for n, v in dropins.items()})
    for name, content in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for name in ("recurring.json", "manual.json"):
        (output / name).write_text(json.dumps({
            "enabled": False, "purpose": "system-clock-bounded-checkpoint-retirement-v2",
            "note": "Disabled template only. Derive a fresh installed scope before separate approval.",
        }, indent=2) + "\n")
    (output / "registration-approval.template.json").write_text(json.dumps({
        "enabled": False, "purpose": "checkpoint-evidence-registration-v1",
        "action": "register", "plan_id": None, "approval_reference": None,
        "deletion_authority": False,
    }, indent=2) + "\n")
    (output / "render-input.json").write_text(json.dumps(value, indent=2) + "\n")
    hashes = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(output.rglob("*")) if p.is_file()}
    (HERE / "deployment-files.json").write_text(json.dumps({
        "release_id": manifest["release_id"], "installed": False, "sha256": hashes,
    }, indent=2) + "\n")
    print(json.dumps({"release_id": manifest["release_id"], "rendered_files": len(hashes),
                      "installed": False}))


if __name__ == "__main__":
    main()
