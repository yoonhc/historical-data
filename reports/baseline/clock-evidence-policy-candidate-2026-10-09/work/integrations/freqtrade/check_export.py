"""Run inside the official Freqtrade container, without exchange/network access."""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow.feather as feather
from freqtrade import __version__
from freqtrade.data.history.datahandlers import get_datahandler
from freqtrade.enums import CandleType
from freqtrade.strategy import IStrategy


class LoaderSmokeStrategy(IStrategy):
    """Exercise strategy hooks only; this is not an investment strategy."""

    timeframe = "5m"
    startup_candle_count = 20
    minimal_roi = {"0": 0.02}
    stoploss = -0.05

    def populate_indicators(self, dataframe, metadata):
        dataframe["fast"] = dataframe["close"].rolling(3).mean()
        dataframe["slow"] = dataframe["close"].rolling(20).mean()
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[
            (dataframe["fast"] > dataframe["slow"]) & (dataframe["volume"] > 0), "enter_long"
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe.loc[dataframe["fast"] < dataframe["slow"], "exit_long"] = 1
        return dataframe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.export / "manifest.json").read_bytes())
    if __version__ != manifest["freqtrade_version"]:
        raise RuntimeError(f"Untested Freqtrade version: {__version__}")
    handler = get_datahandler(args.export / "data", "feather")
    checked = []
    strategy_checks = []
    for item in manifest["files"]:
        path = args.export / item["path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(f"Hash mismatch: {path}")
        if not item["native"]:
            continue
        expected = feather.read_table(path).to_pandas()
        actual = handler.ohlcv_load(
            item["pair"],
            item["timeframe"],
            CandleType(item["candle_type"]),
            fill_missing=False,
            drop_incomplete=False,
        )
        pd.testing.assert_frame_equal(
            actual[expected.columns], expected, check_dtype=False, check_exact=True
        )
        checked.append({"path": item["path"], "rows": len(actual), "columns": list(actual.columns)})
        if item["timeframe"] == "5m" and item["candle_type"] in {"spot", "futures"}:
            strategy = LoaderSmokeStrategy({"timeframe": "5m"})
            metadata = {"pair": item["pair"]}
            analyzed = strategy.advise_indicators(actual.copy(), metadata)
            analyzed = strategy.advise_entry(analyzed, metadata)
            analyzed = strategy.advise_exit(analyzed, metadata)
            assert len(analyzed) == len(actual)
            assert analyzed["slow"].notna().sum() == max(0, len(actual) - 19)
            strategy_checks.append(
                {
                    "pair": item["pair"],
                    "rows": len(analyzed),
                    "entry_signals": int(analyzed["enter_long"].eq(1).sum()),
                    "exit_signals": int(analyzed["exit_long"].eq(1).sum()),
                }
            )
    if not checked:
        raise RuntimeError("No native Freqtrade files checked")
    print(
        json.dumps(
            {
                "ok": True,
                "freqtrade_version": __version__,
                "export_id": manifest["export_id"],
                "checked": checked,
                "strategy_hooks": strategy_checks,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
