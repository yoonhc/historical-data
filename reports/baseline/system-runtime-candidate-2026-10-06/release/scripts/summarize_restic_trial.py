"""Summarize completed trial evidence without opening a secret or changing a backup."""

import argparse
import json
import runpy
import subprocess
from pathlib import Path

from marketdata.domain import json_bytes, now, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    trial = runpy.run_path(str(Path(__file__).with_name("restic_trial.py")))

    def read(name):
        return json.loads((args.reports / (name + ".json")).read_bytes())

    baseline = read("baseline")
    profile = baseline["profile"]
    sources, backups, recoveries, timings = [], [], [], {}
    for entry in baseline["inputs"]:
        label = entry["label"]
        marker = Path(entry["path"]) / "backup.json"
        trial["require"](
            sha256(marker.read_bytes()) == entry["manifest_sha256"], "Original manifest changed"
        )
        sources.append(
            {k: entry[k] for k in ("label", "path", "backup_id", "manifest_sha256")}
            | {"source_root": entry["manifest"]["source_root"]}
        )
        backups.append({"label": label, **read("backup-" + label)})
        recovery = read("recovery-" + label)
        trial["require"](recovery["ok"], "Incomplete recovery")
        queries = read("readback-" + label)
        recoveries.append(
            {
                "label": label,
                **recovery,
                "validate": read("validate-" + label),
                "extracted_verification": read("verify-extracted-" + label),
                "new_backup_verification": read("verify-new-backup-" + label),
                "query_summary": {
                    "ok": queries["ok"],
                    "snapshots": queries["snapshots"],
                    "snapshot_queries": len(queries["snapshot_queries"]),
                    "snapshot_rows": sum(x["rows"] for x in queries["snapshot_queries"]),
                    "event_segments": queries["event_segments"],
                    "raw_reads": len(queries["raw_ids"]),
                    "aggtrade": queries["aggtrade"],
                    "depth_replay": queries["depth_replay"],
                    "scope": queries["scope"],
                },
            }
        )
    trial["require"](read("check")["ok"], "Repository check incomplete")
    for file in sorted(args.reports.glob("*-command.json")):
        data = json.loads(file.read_bytes())
        trial["require"](data["returncode"] == 0, "Failed command")
        timings[file.stem.removesuffix("-command")] = data
    for file in sorted(args.reports.glob("*-timing.json")):
        timings[file.stem.removesuffix("-timing")] = json.loads(file.read_bytes())

    samples = [
        json.loads(line) for line in (args.reports / "monitor.jsonl").read_text().splitlines()
    ]
    sampling_errors = [s for s in samples if "sampling_error" in s]
    streams = {}
    for name in ("aggtrade", "depth"):
        values = [s[name] for s in samples if name in s]
        ages = [
            s["receive_age_seconds"] for s in values if s.get("receive_age_seconds") is not None
        ]
        counters = {}
        for field in (
            "connection_errors",
            "sync_losses",
            "unreconstructed_messages",
            "lagged_snapshots",
            "out_of_order_messages",
            "unrepaired_gaps",
        ):
            numbers = [v[field] for v in values if v.get(field) is not None]
            if numbers:
                counters[field] = max(numbers)
        streams[name] = {
            "samples": len(values),
            "states": sorted({v.get("state") or "unreported" for v in values}),
            "unreported_state_samples": sum(v.get("state") is None for v in values),
            "collection_ids": sorted({v["id"] for v in values if v.get("id")}),
            "max_receive_age_seconds": max(ages) if ages else None,
            "max_counters": counters,
            "errors": [v for v in values if v.get("error") or v.get("flush_error")],
        }

    first = backups[0]["repository_usage"]["allocated_bytes"]
    both = backups[1]["repository_usage"]["allocated_bytes"]
    hardlink = baseline["hardlink_both"]["allocated_bytes"]
    work = Path(profile["work_root"])
    repo_root = Path(__file__).resolve().parents[1]
    files = [
        "scripts/install_restic.py",
        "scripts/restic_trial.py",
        "scripts/summarize_restic_trial.py",
        "configs/backup-host.example.toml",
        "configs/restic-trial-2026-09-27.toml",
        "tests/test_restic_trial.py",
        "packages/marketdata/src/marketdata/backup.py",
        "packages/marketdata/src/marketdata/maintenance.py",
    ]
    report = {
        "at": now(),
        "source_code_base": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
        ).strip(),
        "files_sha256": {p: sha256((repo_root / p).read_bytes()) for p in files},
        "restic_binary_sha256": baseline["binary_sha256"],
        "profile": profile,
        "sources": sources,
        "repository_check": read("check"),
        "backups": backups,
        "recoveries": recoveries,
        "timings": timings,
        "storage": {
            "hardlink_first": baseline["hardlink_first"],
            "hardlink_both": baseline["hardlink_both"],
            "hardlink_increment_allocated_bytes": baseline["hardlink_increment_allocated_bytes"],
            "restic_initial": read("repository-initial"),
            "restic_first_allocated_bytes": first,
            "restic_both_allocated_bytes": both,
            "restic_increment_allocated_bytes": both - first,
            "two_checkpoints_saved_allocated_bytes": hardlink - both,
            "two_checkpoints_saved_fraction": (hardlink - both) / hardlink,
            "cache_final": trial["disk_usage"]([work / "cache"]),
            "work_including_cache": trial["disk_usage"]([work]),
            "work_components": {
                p.name: trial["disk_usage"]([p]) for p in sorted(work.iterdir()) if p.is_dir()
            },
            "local_reports": trial["disk_usage"]([args.reports]),
        },
        "monitor": {
            "first_at": samples[0]["at"],
            "last_at": samples[-1]["at"],
            "samples": len(samples),
            "sampling_errors": sampling_errors,
            "streams": streams,
            "scope": "10s samples within trial phases; excludes input preflight and between phases",
        },
        "limits": [
            "Only two checkpoints; compression and content deduplication jointly measured",
            "Restic timing excludes completed-checkpoint preparation and command preflight",
            "Readback is sampled; file and catalog integrity are checked in full",
            "Existing quality problems and gaps are preserved",
            "No retention, service scheduling, production code or source-root guard changes",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as handle:
        handle.write(json_bytes(report))
    print(
        json_bytes(
            {"output": str(args.output), "saved_fraction": (hardlink - both) / hardlink}
        ).decode()
    )


if __name__ == "__main__":
    main()
