"""Run preserved, isolated baseline/candidate processes for each synthetic market."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from compare_depth_decisions import SCENARIOS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    results, commands = {}, []
    for scenario in SCENARIOS:
        results[scenario] = {}
        for variant, code in (("baseline", args.baseline), ("logging", args.candidate)):
            output = args.output / variant / scenario
            command = [
                sys.executable,
                str(args.candidate / "scripts/compare_depth_decisions.py"),
                "--output",
                str(output),
                "--scenario",
                scenario,
            ]
            env = {
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    str(p)
                    for p in (
                        code / "packages/marketdata/src",
                        code / "packages/marketdata_freqtrade/src",
                        code,
                    )
                ),
            }
            commands.append({"command": command, "pythonpath": env["PYTHONPATH"]})
            subprocess.run(command, env=env, cwd=code, check=True)
            results[scenario][variant] = json.loads((output / "result.json").read_bytes())
        baseline, instrumented = results[scenario]["baseline"], results[scenario]["logging"]
        # The old baseline has no decision log or durable context. Compare externally
        # visible request scheduling and quality, independently of its new metadata.
        for key in (
            "state",
            "messages",
            "connections",
            "coverage_resets",
            "top_shortfalls",
            "extra_requests",
            "periodic_requests",
            "all_rest_calls",
            "exact_full_market_comparisons",
            "minimum_known_levels",
            "maximum_receive_queue",
            "rest_verifications",
            "unreconstructed_messages",
        ):
            assert baseline[key] == instrumented[key], (
                scenario,
                key,
                baseline[key],
                instrumented[key],
            )
    summary = {
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "baseline_matches_logging": True,
        "scenario_count": len(SCENARIOS),
        "capture_count": sum(len(v) for v in results.values()),
        "results": results,
        "commands": commands,
    }
    (args.output / "matrix.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
