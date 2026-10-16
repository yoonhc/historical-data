"""Verify actual Freqtrade result artifacts and save a compact integration report."""

import json
import zipfile
from pathlib import Path

from marketdata.domain import json_bytes, sha256
from marketdata.store import atomic_write
from marketdata_freqtrade.export import verify_export


def main():
    root = Path(__file__).resolve().parents[1]
    exported = verify_export(root / "data/exports/freqtrade/smoke-v2")
    results = []
    for path in sorted((root / "integrations/freqtrade/user_data/backtest_results").glob("*.zip")):
        with zipfile.ZipFile(path) as zf:
            report = json.loads(zf.read(path.stem + ".json"))
            strategy = report["strategy"].get("DataSmokeStrategy")
            if strategy is None:
                continue
            trades = strategy["trades"]
            assert len(trades) == strategy["total_trades"] > 0
            assert strategy["backtest_start"] == "2026-09-15 00:00:00"
            assert strategy["backtest_end"] == "2026-09-20 23:55:00"
            config = json.loads(zf.read(path.stem + "_config.json"))
            assert config["dry_run"] is True
            results.append(
                {
                    "mode": strategy["trading_mode"],
                    "result_zip": str(path.relative_to(root)),
                    "result_sha256": sha256(path.read_bytes()),
                    "trades": len(trades),
                    "backtest_start": strategy["backtest_start"],
                    "backtest_end": strategy["backtest_end"],
                    "trades_with_funding_fees": sum(bool(t.get("funding_fees")) for t in trades),
                    "strategy_sha256": sha256(zf.read(path.stem + "_DataSmokeStrategy.py")),
                }
            )
    assert {r["mode"] for r in results} == {"spot", "futures"}
    assert any(r["mode"] == "futures" and r["trades_with_funding_fees"] > 0 for r in results)
    report = {
        "ok": True,
        "freqtrade_version": "2026.8",
        "export_id": exported["export_id"],
        "snapshot_id": exported["options"]["snapshot_id"],
        "results": results,
        "scope": "Integration smoke tests, not strategy profitability validation",
    }
    atomic_write(root / "reports/freqtrade-backtests.json", json_bytes(report))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
