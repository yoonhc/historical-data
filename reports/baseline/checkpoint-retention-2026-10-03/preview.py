"""Reviewed production preview only; explicit refusal of an apply-enabled policy."""

import time
from pathlib import Path

from marketdata.domain import json_bytes, now, sha256

from scripts import retire_checkpoints as retention
from scripts.restic_trial import sample_streams

ROOT = Path("/home/yoonhc/projects/algoTrade/historical_data")
REPORT = Path(__file__).resolve().parent


def manifests():
    directory = Path("/mnt/backup/marketdata-historical/automatic")
    return {str(p): sha256(p.read_bytes()) for p in sorted(directory.glob("*/*/backup.json"))}


def main():
    policy = retention.Policy.load(REPORT / "preview-policy.toml")
    if policy.apply_enabled is not False:
        raise ValueError("Production preview requires apply_enabled=false")
    before = {"at": now(), "manifests": manifests(), "streams": sample_streams(ROOT)}
    if (REPORT / "before-preview.json").exists():
        (REPORT / "before-preview.json").rename(REPORT / "before-preview-sandbox.json")
    (REPORT / "before-preview.json").write_bytes(json_bytes(before))
    started = time.monotonic()
    receipt = retention.execute(policy, "plan")
    (REPORT / "operational-preview.json").write_bytes(json_bytes(receipt))
    after = {
        "at": now(),
        "manifests": manifests(),
        "streams": sample_streams(ROOT),
        "wall_seconds": time.monotonic() - started,
    }
    after["all_backup_manifests_unchanged"] = before["manifests"] == after["manifests"]
    (REPORT / "after-preview.json").write_bytes(json_bytes(after))
    result = receipt["result"]
    print(
        json_bytes(
            {
                "ok": receipt["ok"],
                "plan_id": result["plan_id"],
                "keep": len(result["keep"]),
                "candidates": len(result["candidates"]),
                "excluded": len(result["excluded"]),
                "space": result["space"],
                "deleted_checkpoints": result["deleted_checkpoints"],
                "apply_enabled": result["apply_enabled"],
                "manifests_unchanged": after["all_backup_manifests_unchanged"],
                "wall_seconds": after["wall_seconds"],
            }
        ).decode(),
        flush=True,
    )


if __name__ == "__main__":
    main()
