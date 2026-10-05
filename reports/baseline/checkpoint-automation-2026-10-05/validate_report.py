"""Validate the frozen candidate and pending scope without running retirement."""

import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    release = json.loads((OUT / "release.json").read_bytes())
    before = json.loads((OUT / "operating-source-before.json").read_bytes())
    assert all(digest(OUT / "release" / p) == h for p, h in release["files"].items())
    assert all(not (OUT / "release" / p).stat().st_mode & 0o222 for p in release["files"])
    assert digest(OUT / "source.tar.gz") == release["source_archive_sha256"]
    assert digest(OUT / "candidate.patch") == release["patch_sha256"]
    assert all(digest(ROOT / p) == h for p, h in before["files"].items())
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == before["base_commit"] == release["base_commit"]

    config_hashes = {}
    for copy in sorted((OUT / "proposed-settings").iterdir()):
        actual = Path("/home/yoonhc/.config/marketdata") / copy.name
        assert actual.read_bytes() == copy.read_bytes()
        assert actual.stat().st_uid == os.getuid()
        assert stat.S_IMODE(actual.stat().st_mode) == 0o600
        config_hashes[str(actual)] = digest(actual)
    grant = json.loads(
        (OUT / "proposed-settings/checkpoint-retention-authorization.json").read_bytes()
    )
    assert grant["enabled"] is False
    assert grant["approved_at"] is None and grant["approval_reference"] is None
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    proposal = subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            str(OUT / "release/scripts/run_checkpoint_schedule.py"),
            "--settings",
            "/home/yoonhc/.config/marketdata/checkpoint-schedule.toml",
            "propose",
        ],
        cwd="/home/yoonhc",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proposal.stdout) == grant
    for unit in (
        "marketdata-checkpoint-retirement.service",
        "marketdata-checkpoint-retirement.timer",
    ):
        assert not os.path.lexists(Path("/home/yoonhc/.config/systemd/user") / unit)

    broken = []
    links = 0
    documents = [
        OUT / "summary.md",
        OUT / "approval-review.md",
        ROOT / "reports/README.md",
        ROOT / "docs/restic-transition-and-retention.md",
    ]
    for document in documents:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            path = target.strip("<>").split("#", 1)[0]
            if not (document.parent / path).exists():
                broken.append({"document": str(document), "target": target})
            links += 1
    assert not broken, broken
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "ok": True,
        "frozen_release_files_match": len(release["files"]),
        "release_files_read_only": True,
        "source_archive_and_patch_hashes_match": True,
        "operating_files_unchanged": len(before["files"]),
        "operating_head": head,
        "pending_configuration": config_hashes,
        "fresh_proposal_matches_pending_grant": True,
        "recurring_deletion_authorized": False,
        "new_unit_files_absent": True,
        "scope_id": grant["scope_id"],
        "local_document_links_checked": links,
        "git_diff_check": "passed",
        "operational_preview": json.loads((OUT / "preview-results.json").read_bytes()),
        "artifact_sha256": {
            str(p.relative_to(OUT)): digest(p)
            for p in sorted(OUT.rglob("*"))
            if p.is_file() and p.name != "final-validation.json"
        },
    }
    (OUT / "final-validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in {"artifact_sha256", "pending_configuration", "operational_preview"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
