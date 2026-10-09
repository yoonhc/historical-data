"""Exercise data and backtesting plumbing; no profitability claim is intended."""

from freqtrade.strategy import IStrategy


class DataSmokeStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    startup_candle_count = 20
    minimal_roi = {"0": 0.02}
    stoploss = -0.05
    can_short = False

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
