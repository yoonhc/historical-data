"""Prepare NEW, disabled recurring-approval settings only. No service operations."""

import json
import os
import subprocess
import tomllib
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
CONFIG = Path("/home/yoonhc/.config/marketdata")
NAMES = [
    "checkpoint-retention-automatic.toml",
    "checkpoint-schedule.toml",
    "checkpoint-retention-authorization.json",
    "checkpoint-retirement-service.env",
]


def create(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def main():
    assert all(not (CONFIG / name).exists() for name in NAMES)
    assert all(
        not (Path("/home/yoonhc/.config/systemd/user") / name).exists()
        for name in (
            "marketdata-checkpoint-retirement.service",
            "marketdata-checkpoint-retirement.timer",
        )
    )
    source = (
        ROOT / "reports/baseline/checkpoint-retention-readiness-2026-10-05/approval-policy.toml"
    )
    values = tomllib.loads(source.read_text())
    assert values["keep_latest"] == 3 and values["min_age_hours"] == 72
    header = "# Separate automatic policy. Pending recurring grant; no timer installed.\n"
    policy = (
        header + "\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items()) + "\n"
    )
    create(CONFIG / NAMES[0], policy.encode())
    settings = (
        f'retention_policy = "{CONFIG / NAMES[0]}"\nauthorization_file = "{CONFIG / NAMES[2]}"\n'
    )
    create(CONFIG / NAMES[1], settings.encode())
    python = ROOT / ".venv/bin/python"
    entry = OUT / "release/scripts/run_checkpoint_schedule.py"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    grant = json.loads(
        subprocess.check_output(
            [str(python), str(entry), "--settings", str(CONFIG / NAMES[1]), "propose"],
            env=env,
        )
    )
    assert grant["enabled"] is False and grant["approved_at"] is None
    create(CONFIG / NAMES[2], (json.dumps(grant, indent=2) + "\n").encode())
    create(
        CONFIG / NAMES[3],
        (
            f"MARKETDATA_RETIREMENT_PYTHON={python}\n"
            f"MARKETDATA_RETIREMENT_ENTRYPOINT={entry}\n"
            f"MARKETDATA_RETIREMENT_SETTINGS={CONFIG / NAMES[1]}\n"
        ).encode(),
    )
    copies = OUT / "proposed-settings"
    copies.mkdir(exist_ok=False)
    for name in NAMES:
        create(copies / name, (CONFIG / name).read_bytes())
    result = {
        "ok": True,
        "new_files": [str(CONFIG / name) for name in NAMES],
        "scope_id": grant["scope_id"],
        "code_id": grant["scope"]["code"]["code_id"],
        "recurring_deletion_authorized": False,
        "timer_installed": False,
    }
    create(OUT / "profile-preparation.json", (json.dumps(result, indent=2) + "\n").encode())
    print(json.dumps(result))


if __name__ == "__main__":
    main()
