"""
WeekendReverseV1 — 周末低流动性反转

窗口：周末+周一美股盘前 | 4h单根跌>2% | 阳线做多 | 十品种通用
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Union
import numpy as np
import pandas as pd
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.strategy import IStrategy, Trade


class WeekendReverseV1(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "4h"
    startup_candle_count: int = 250

    stoploss = -0.10
    trailing_stop = True
    trailing_stop_positive = 0.003
    trailing_stop_positive_offset = 0.012
    trailing_only_offset_is_reached = True
    minimal_roi = {"0": 0.08}

    use_exit_signal = True; exit_profit_only = False; ignore_roi_if_entry_signal = False
    max_open_trades = 1; process_only_new_candles = True
    order_types = {"entry": "limit", "exit": "limit", "stoploss": "market", "stoploss_on_exchange": False}

    def populate_indicators(self, df, meta):
        df["ema20"] = ta.EMA(df, timeperiod=20)
        df["ret_1p"] = df["close"].pct_change(periods=1)

        tss = pd.to_datetime(df["date"])
        bj_h = (tss.dt.hour + 8) % 24
        dow = tss.dt.dayofweek
        wknd = (dow >= 5) | ((dow == 0) & (bj_h <= 21))

        df["long_entry"] = (
            wknd
            & (df["ret_1p"].shift(1) < -0.02)
            & (df["close"] > df["open"])
            & (df["ret_1p"] >= -0.02)
            & (df["volume"] > 0)
        )
        return df

    def populate_entry_trend(self, df, meta): df.loc[df["long_entry"], "enter_long"] = 1; return df
    def populate_exit_trend(self, df, meta): df.loc[(df["close"] > df["ema20"]) & (df["close"].shift(1) <= df["ema20"].shift(1)) & (df["volume"] > 0), "exit_long"] = 1; return df
