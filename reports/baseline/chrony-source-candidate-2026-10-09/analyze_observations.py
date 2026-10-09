"""Derive bounded observation summaries; never modify collection or test inputs."""

import datetime as dt
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def evidence(path):
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def selected(csv):
    return [r.split(",")[2] for r in csv.splitlines() if r.split(",")[1] == "*"]


def compact(row, fault=False):
    assessment = row["assessment" if fault else "clock_assessment"]
    commands = row["queries" if fault else "commands"]
    t = assessment["tracking"]
    return {
        "observed_at": row["at"],
        "selected_addresses": selected(commands["sources"]["stdout"]),
        "reference_address": t["reference_address"],
        "reference_epoch": t["reference_time"],
        "reference_utc": dt.datetime.fromtimestamp(
            t["reference_time"], dt.timezone.utc
        ).isoformat(),
        "reference_age_seconds": assessment["reference_age_seconds"],
        "ready": assessment["ready"],
        "reasons": assessment["reasons"],
    }


def events(data, key):
    out, previous = [], object()
    for row in data:
        value = key(row)
        if value != previous:
            out.append(row)
            previous = value
    return out


def public(directory):
    start, result = load(directory / "start.json"), load(directory / "result.json")
    observed = rows(directory / "observations.jsonl")
    states = [compact(r) for r in observed]
    source_records = []
    last_stats = {
        r.split(",")[0]: r.split(",")
        for r in observed[-1]["commands"]["ntpdata"]["stdout"].splitlines()
    }
    for source in start["sources"]:
        ip = source["observed_address"]
        samples = []
        for row in observed:
            for raw in row["commands"]["ntpdata"]["stdout"].splitlines():
                parts = raw.split(",")
                if parts[0] == ip and int(parts[33]) > 0:
                    samples.append(parts)
        v = last_stats[ip]
        source_records.append(
            {
                **source,
                "tx": int(v[30]),
                "rx": int(v[31]),
                "valid_rx": int(v[32]),
                "good_rx": int(v[33]),
                "last_peer_delay_seconds": float(v[19]),
                "last_source_offset_seconds": float(v[18]),
                "observed_peer_delay_range_seconds": [
                    min(float(x[19]) for x in samples),
                    max(float(x[19]) for x in samples),
                ]
                if samples
                else None,
                "observed_source_offset_range_seconds": [
                    min(float(x[18]) for x in samples),
                    max(float(x[18]) for x in samples),
                ]
                if samples
                else None,
                "timestamp_modes": {"tx": v[28], "rx": v[29]},
                "authenticated": v[27],
                "upstream_reference_id": v[15],
                "observed_leap": v[5],
            }
        )
    ready_first = next((i for i, v in enumerate(states) if v["ready"]), None)
    post = states[ready_first:] if ready_first is not None else []
    return {
        "started_at": start["started_at"],
        "finished_at": result["finished_at"],
        "completed": result["completed"],
        "sample_seconds": 30,
        "binary_sha256": start["binary_sha256"],
        "clock_control": False,
        "capabilities": result["capabilities"],
        "source_records": source_records,
        "first_ready_observation": None if ready_first is None else states[ready_first],
        "selection_changes": events(states, lambda v: v["selected_addresses"]),
        "reference_updates": events(
            states, lambda v: (v["reference_address"], v["reference_epoch"])
        ),
        "readiness_changes": events(states, lambda v: (v["ready"], v["reasons"])),
        "max_reference_age_after_first_ready_seconds": max(
            (v["reference_age_seconds"] for v in post), default=None
        ),
        "post_ready_rejected_samples": sum(not v["ready"] for v in post),
        "evidence": [
            evidence(directory / f)
            for f in ("start.json", "result.json", "observations.jsonl", "chrony.conf")
        ],
    }


def fault(directory):
    result = load(directory / "result.json")
    observed = rows(directory / "observations.jsonl")
    data = [compact(r, True) for r in observed if r["fault_active"]]
    original = result["last_valid_reference_before_fault"]
    different = next(
        (
            r
            for r in data
            if r["selected_addresses"] and original["address"] not in r["selected_addresses"]
        ),
        None,
    )
    fresh = [
        r
        for r in events(data, lambda v: (v["reference_address"], v["reference_epoch"]))
        if r["reference_epoch"] > original["epoch"]
    ]
    first_bad = next((i for i, r in enumerate(data) if not r["ready"]), None)
    recovery = (
        None if first_bad is None else next((r for r in data[first_bad + 1 :] if r["ready"]), None)
    )
    return {
        "completed": result["completed"],
        "case": result["case"],
        "fault_started_at": result["fault_started_at"],
        "finished_at": result["finished_at"],
        "warmup_seconds": result["warmup_seconds"],
        "fault_window_seconds": result["fault_window_seconds"],
        "sample_seconds": 5,
        "last_valid_reference_before_fault": original,
        "last_valid_reference_utc": dt.datetime.fromtimestamp(
            original["epoch"], dt.timezone.utc
        ).isoformat(),
        "first_selection_to_another_source": different,
        "first_new_reference_after_fault": fresh[0] if fresh else None,
        "first_reference_from_another_source": next(
            (v for v in fresh if v["reference_address"] != original["address"]), None
        ),
        "first_rejection": None if first_bad is None else data[first_bad],
        "first_ready_after_rejection": recovery,
        "selection_changes": events(data, lambda v: v["selected_addresses"]),
        "reference_updates": fresh,
        "readiness_changes": events(data, lambda v: (v["ready"], v["reasons"])),
        "max_reference_age_seconds": max(v["reference_age_seconds"] for v in data),
        "last_observation": data[-1],
        "server_counters": result["counters"],
        "failover_within_600_seconds_demonstrated": False,
        "provider_gate": result["full_operating_provider_gate"],
        "evidence": [
            evidence(directory / f) for f in ("result.json", "observations.jsonl", "chrony.conf")
        ],
    }


def main():
    results = {"public": {}, "fault": {}}
    for name in ("public-observation-attempt-3", "public-alternatives", "public-regional"):
        if (ROOT / name / "result.json").exists():
            results["public"][name] = public(ROOT / name)
    for name in ("silent", "intermittent"):
        results["fault"][name] = fault(ROOT / "fault-observation-attempt-3" / name)
    (ROOT / "observation-analysis.json").write_text(json.dumps(results, indent=2) + "\n")
    for name, case in results["fault"].items():
        print(
            name,
            json.dumps(
                {
                    k: v
                    for k, v in case.items()
                    if k
                    in {
                        "first_selection_to_another_source",
                        "first_new_reference_after_fault",
                        "first_rejection",
                        "first_ready_after_rejection",
                        "max_reference_age_seconds",
                    }
                },
                ensure_ascii=False,
            ),
        )
    for name, case in results["public"].items():
        print(
            name,
            [
                (r["operator"], r["observed_address"], r["tx"], r["rx"])
                for r in case["source_records"]
            ],
            case["post_ready_rejected_samples"],
            case["max_reference_age_after_first_ready_seconds"],
        )


if __name__ == "__main__":
    main()
