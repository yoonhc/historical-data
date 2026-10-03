"""Run the existing recovery procedure with the original host paths inaccessible."""

import hashlib
import json
import os
import sys
from pathlib import Path

import marketdata
from marketdata.domain import json_bytes, now

from scripts.archive_backups import Profile, execute

KIT = Path("/recovery/kit")
REPORTS = Path("/recovery/reports")
ORIGINAL = Path("/home/yoonhc/projects/algoTrade/historical_data")
SNAPSHOT = "d27a0a557e1795a4651132016491f1b2c6735e8181b71303f3adccd414f0f8ad"


def main():
    absent = [
        ORIGINAL,
        Path("/mnt/backup/marketdata-historical/automatic"),
        Path("/mnt/backup/marketdata-restic"),
        Path("/home/yoonhc/.config/marketdata"),
        ORIGINAL / "archive-state",
        ORIGINAL / ".cache/restic-archive",
    ]
    assert all(not p.exists() for p in absent)
    assert Path(marketdata.__file__).is_relative_to(KIT / "code")
    assert all(not str(p).startswith(str(ORIGINAL)) for p in sys.path)
    profile = Profile.load(KIT / "profile.toml")
    assert profile.store_root == ORIGINAL
    assert not profile.state_file.exists()
    assert not list(profile.cache_root.iterdir())
    assert not list(profile.restore_work_root.iterdir())
    profile.guard()
    preparation = json.loads((KIT / "kit.json").read_bytes())
    for relative, expected in preparation["code_sha256"].items():
        assert hashlib.sha256((KIT / "code" / relative).read_bytes()).hexdigest() == expected
    proof = {
        "checked_at": now(),
        "original_paths_absent": {str(p): not p.exists() for p in absent},
        "python": sys.version,
        "marketdata_module": marketdata.__file__,
        "sys_path": sys.path,
        "uid": os.getuid(),
        "state_initially_absent": True,
        "cache_initially_empty": True,
        "restore_work_initially_empty": True,
        "source_root_guard_unchanged": True,
        "credential_source": "separate 0600 recovery copy on second SSD; original key path inaccessible",
        "repository_source": "verified independent file copies; production repository not mounted",
        "network_namespace": "unshared",
        "new_machine_or_offsite_test": False,
    }
    (REPORTS / "isolation-proof.json").write_bytes(json_bytes(proof))
    print(json_bytes({"phase": "isolation-verified", "at": now()}).decode(), flush=True)
    result = execute(profile, "restore-check", SNAPSHOT)
    assert result["ok"] and result["result"]["ok"]
    assert result["result"]["snapshot_id"] == SNAPSHOT
    assert all(not p.exists() for p in absent)
    (REPORTS / "recovery-result.json").write_bytes(json_bytes(result))
    print(
        json_bytes(
            {
                "phase": "recovery-complete",
                "at": now(),
                "run_id": result["run_id"],
                "ok": True,
                "new_backup_id": result["result"]["new_backup_id"],
            }
        ).decode(),
        flush=True,
    )


if __name__ == "__main__":
    main()
