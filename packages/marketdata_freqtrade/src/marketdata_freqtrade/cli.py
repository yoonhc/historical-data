import argparse
import json
from pathlib import Path

from marketdata.domain import DataError
from marketdata.store import Store

from .export import export_snapshot, verify_export


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export fixed snapshots for Freqtrade 2026.8")
    parser.add_argument("--root", type=Path, default=Path("."))
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--snapshot", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--timeframes", nargs="+", default=["1m", "5m", "15m", "1h"])
    check = sub.add_parser("verify")
    check.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_export(args.path)
        else:
            store = Store(args.root)
            try:
                with store.writer():
                    result = export_snapshot(store, args.snapshot, args.output, args.timeframes)
            finally:
                store.close()
        print(
            json.dumps(
                {
                    "export_id": result["export_id"],
                    "files": len(result["files"]),
                    "profile": result["options"]["profile"],
                }
            )
        )
        return 0
    except (DataError, OSError, KeyError) as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
