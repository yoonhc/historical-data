"""Run a pinned export through the existing ft_userdata Docker installation."""

import argparse
import json
import os
import re
import shlex
import subprocess
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marketdata.domain import DataError, json_bytes, utc
from marketdata.store import atomic_write
from marketdata_freqtrade.export import verify_export
from marketdata_freqtrade.window import file_sha

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "integrations/freqtrade"
IMAGE = (
    "freqtradeorg/freqtrade@sha256:7031bca43ed7668ebf421725dd5016acade6ef88b0771db3e08c96e6d19a42db"
)


def validate_timerange(value, manifest, warmup_days):
    if not re.fullmatch(r"\d{8}-\d{8}", value):
        raise DataError("Timerange must be YYYYMMDD-YYYYMMDD (UTC, end exclusive)")
    begin, end = [
        datetime.strptime(t, "%Y%m%d").replace(tzinfo=timezone.utc) for t in value.split("-")
    ]
    if warmup_days < 1:
        raise DataError("Reserve at least one full day of startup candles")
    if (
        not utc(manifest["start"]) + timedelta(days=warmup_days)
        <= begin
        < end
        <= utc(manifest["end"])
    ):
        raise DataError("Timerange and startup candles must fit inside this export")
    return begin, end


def freqtrade_timerange(begin, end):
    # Freqtrade 2026.8 trims with date <= stopdt. Keep our public [start, end) contract.
    return f"{int(begin.timestamp())}-{int(end.timestamp()) - 1}"


def backtest_summary(directory, strategy, mode, begin, end):
    archives = sorted(directory.glob("*.zip"))
    if len(archives) != 1:
        raise DataError(f"Expected one backtest ZIP, got {len(archives)}")
    archive = archives[0]
    with zipfile.ZipFile(archive) as zf:
        content = json.loads(zf.read(archive.stem + ".json"))
        result = content["strategy"][strategy]
    if result["trading_mode"] != mode or utc(result["backtest_start"]) != begin:
        raise DataError("Backtest mode/start changed; check configuration and startup candle count")
    if utc(result["backtest_end"]) != end - timedelta(minutes=5):
        raise DataError("Backtest did not cover the requested 5m interval")
    if len(result["trades"]) != result["total_trades"]:
        raise DataError("Backtest trade count mismatch")
    return {
        "zip": str(archive),
        "sha256": file_sha(archive),
        "trades": result["total_trades"],
        "start": result["backtest_start"],
        "end": result["backtest_end"],
        "trades_with_funding_fees": sum(bool(t.get("funding_fees")) for t in result["trades"]),
    }


def compose_preflight(command, env, home, export):
    config_command = command[: command.index("run")] + ["config", "--format", "json"]
    resolved = subprocess.run(config_command, env=env, capture_output=True, text=True)
    if resolved.returncode:
        raise DataError("Compose configuration could not be resolved")
    service = json.loads(resolved.stdout)["services"]["freqtrade"]
    mounts = {v["target"]: v for v in service["volumes"]}
    if service["image"] != IMAGE:
        raise DataError("Compose image differs from the verified pinned image")
    if Path(mounts["/freqtrade/user_data"]["source"]).resolve() != home / "user_data":
        raise DataError("Compose user_data mount does not point at the selected installation")
    if Path(mounts["/marketdata"]["source"]).resolve() != export or not mounts["/marketdata"].get(
        "read_only"
    ):
        raise DataError("Compose export mount must match the selected data and be read-only")
    return {"image": service["image"], "user_data": mounts["/freqtrade/user_data"]["source"]}


def main():
    plan = json.loads((INTEGRATION / "datasets.json").read_bytes())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "backtest", "lookahead"])
    parser.add_argument("--dataset", choices=list(plan["datasets"]), default="futures-after-gap")
    parser.add_argument("--mode", choices=["spot", "futures"], default="futures")
    parser.add_argument("--ft-home", type=Path, default=Path("/home/yoonhc/freqtrade/ft_userdata"))
    parser.add_argument(
        "--timerange", help="UTC YYYYMMDD-YYYYMMDD; default: dataset's smoke window"
    )
    parser.add_argument("--warmup-days", type=int, default=1)
    parser.add_argument("--strategy", default="DataSmokeStrategy")
    parser.add_argument("--strategy-path", type=Path, default=INTEGRATION / "strategies")
    parser.add_argument(
        "--fee", type=float, help="Per-side fraction; default spot .001, futures .0005"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Default prints the reviewed command"
    )
    args = parser.parse_args()
    home, strategies = args.ft_home.resolve(), args.strategy_path.resolve()
    export = ROOT / "data/exports/freqtrade/research-v3" / args.dataset
    manifest = verify_export(export)
    spec = plan["datasets"][args.dataset]
    if manifest["options"]["snapshot_id"] != spec["snapshot_id"] or any(
        utc(manifest[k]) != utc(spec[k]) for k in ("start", "end")
    ):
        raise DataError("Export does not match reviewed dataset definition")
    if args.action != "check" and args.mode not in spec["modes"]:
        raise DataError("Selected market is absent from dataset")
    if not (home / "docker-compose.yml").is_file() or not (home / "user_data").is_dir():
        raise DataError("Expected an existing Docker Compose installation with user_data")
    if not strategies.is_dir():
        raise DataError("Strategy directory does not exist")
    fee = args.fee if args.fee is not None else (0.001 if args.mode == "spot" else 0.0005)
    if not 0 <= fee < 1:
        raise DataError("Fee must be a finite per-side fraction between zero and one")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    relative = Path("backtest_results/marketdata") / run_id
    output = home / "user_data" / relative
    container_output = Path("/freqtrade/user_data") / relative
    env = os.environ | {
        "MARKETDATA_ROOT": str(ROOT),
        "MARKETDATA_EXPORT": str(export),
        "MARKETDATA_STRATEGIES": str(strategies),
    }
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(home),
        "-f",
        str(home / "docker-compose.yml"),
        "-f",
        str(INTEGRATION / "ft_userdata.compose.yaml"),
        "run",
        "--rm",
        "--no-deps",
    ]
    timerange = args.timerange or spec["smoke_timerange"]
    if args.action == "check":
        command += ["--entrypoint", "python", "freqtrade", "/checks/check_export.py", "/marketdata"]
    else:
        begin, end = validate_timerange(timerange, manifest, args.warmup_days)
        command += [
            "freqtrade",
            "backtesting" if args.action == "backtest" else "lookahead-analysis",
            "--config",
            f"/configs/{args.mode}.json",
            "--datadir",
            "/marketdata/data",
            "--strategy-path",
            "/strategies",
            "--strategy",
            args.strategy,
            "--timeframe",
            "5m",
            "--timerange",
            freqtrade_timerange(begin, end),
            "--fee",
            str(fee),
        ]
        if args.action == "backtest":
            command += [
                "--cache",
                "none",
                "--export",
                "trades",
                "--backtest-directory",
                str(container_output),
                "--notes",
                f"marketdata:{args.dataset}:{manifest['export_id']}",
            ]
        else:
            command += [
                "--config",
                "/configs/lookahead.json",
                "--minimum-trade-amount",
                "10",
                "--targeted-trade-amount",
                "20",
                "--lookahead-analysis-exportfilename",
                str(container_output / "lookahead.csv"),
            ]
    preview = {
        "target": str(home),
        "export_id": manifest["export_id"],
        "command": shlex.join(command),
    }
    print(json.dumps(preview, indent=2), flush=True)
    if not args.execute:
        return
    resolved = compose_preflight(command, env, home, export)
    report = ROOT / "reports/freqtrade-runs" / run_id
    report.mkdir(parents=True)
    record = {
        "run_id": run_id,
        "compose": resolved,
        "action": args.action,
        "target": str(home),
        "image": IMAGE,
        "dataset": args.dataset,
        "export_id": manifest["export_id"],
        "snapshot_id": manifest["options"]["snapshot_id"],
        "command": command,
        "mode": args.mode,
        "timerange": timerange,
        "fee_per_side": fee,
        "strategy": args.strategy,
        "files": {
            str(p): file_sha(p)
            for p in [
                home / "docker-compose.yml",
                INTEGRATION / "ft_userdata.compose.yaml",
                INTEGRATION / f"config.{args.mode}.json",
                INTEGRATION / "config.lookahead.json",
                INTEGRATION / "check_export.py",
                INTEGRATION / "datasets.json",
                Path(__file__).resolve(),
                *sorted(strategies.rglob("*.py")),
            ]
        },
        "environment": {
            k: env[k] for k in ("MARKETDATA_ROOT", "MARKETDATA_EXPORT", "MARKETDATA_STRATEGIES")
        },
    }
    atomic_write(report / "run.json", json_bytes(record))
    if args.action != "check":
        output.mkdir(parents=True, exist_ok=False)
    with (report / "stdout.log").open("w") as stdout, (report / "stderr.log").open("w") as stderr:
        completed = subprocess.run(command, env=env, stdout=stdout, stderr=stderr)
    record["returncode"] = completed.returncode
    atomic_write(report / "run.json", json_bytes(record))
    if completed.returncode:
        raise DataError(f"Docker command failed ({completed.returncode}); inspect {report}")
    if args.action == "check":
        record["result"] = json.loads((report / "stdout.log").read_text())
        if record["result"].get("export_id") != manifest["export_id"] or not record["result"].get(
            "ok"
        ):
            raise DataError("Loader verification failed")
    elif args.action == "backtest":
        record["result"] = backtest_summary(output, args.strategy, args.mode, begin, end)
    else:
        import csv

        path = output / "lookahead.csv"
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        if (
            len(rows) != 1
            or rows[0].get("has_bias", "").lower() != "false"
            or int(rows[0].get("total_signals", 0)) < 10
        ):
            raise DataError(f"Lookahead analysis incomplete or bias detected: {path}")
        record["result"] = {"csv": str(path), "sha256": file_sha(path), "rows": rows}
    record["ok"] = True
    atomic_write(report / "run.json", json_bytes(record))
    print(json.dumps({"report": str(report / "run.json"), "result": record["result"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (DataError, OSError, ValueError, KeyError) as exc:
        raise SystemExit(f"{type(exc).__name__}: {exc}") from exc
