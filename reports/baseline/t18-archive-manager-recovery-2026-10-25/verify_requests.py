"""Read-only current terminal request verification using the installed contract."""

import hashlib
import importlib.util
import json
from pathlib import Path

BASE = Path("/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11")
ROOT = Path("/home/yoonhc/projects/algoTrade/historical_data/state/scheduled-clock-policy")
JOBS = (
    "oi",
    "oi-probe",
    "oi-quality",
    "daily",
    "pack",
    "backup",
    "restic-archive",
    "network-observer",
)


def main():
    prepared = json.loads(Path(__file__).with_name("prepared-plan.json").read_bytes())
    expected = prepared["release_entries"]["policy/request_contract.py"]["sha256"]
    if hashlib.sha256((BASE / "policy/request_contract.py").read_bytes()).hexdigest() != expected:
        raise RuntimeError("Installed request contract changed")
    spec = importlib.util.spec_from_file_location(
        "unchanged_contract", BASE / "policy/request_contract.py"
    )
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    result = {}
    for job in JOBS:
        path = ROOT / job / "request.json"
        raw = path.read_bytes()
        request = json.loads(raw)
        if request["state"] != "complete":
            raise RuntimeError("Unfinished request: " + job)
        disposition = contract.classify(
            job, request["exit_code"], request["work_result"], request["pins"], request["arguments"]
        )
        if disposition["state"] != "complete":
            raise RuntimeError("Terminal request proof differs: " + job)
        if path.read_bytes() != raw:
            raise RuntimeError("Request changed during read-only verification: " + job)
        result[job] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "request_id": request["request_id"],
            "disposition": disposition,
        }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
