"""
CrashBuyV1 — 全品种暴跌抄底

4h K线，16h跌超9%后收阳线（实体>0.5%）做多。五品种统一参数。
跌9-12%半仓，跌>12%满仓。尾随5%激活，-12%止损，25%止盈，EMA20离场。
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Union
import numpy as np
import pandas as pd
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.strategy import IStrategy, Trade


class CrashBuyV1(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "4h"
    startup_candle_count: int = 250

    stoploss = -0.12
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.05
    trailing_only_offset_is_reached = True
    minimal_roi = {"0": 0.25}

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    max_open_trades = 5
    process_only_new_candles = True

    order_types = {
        "entry": "limit", "exit": "limit",
        "stoploss": "market", "stoploss_on_exchange": False,
    }

    def populate_indicators(self, df, meta):
        df["ema20"] = ta.EMA(df, timeperiod=20)
        df["ret_4p"] = df["close"].pct_change(periods=4)
        df["body_pct"] = abs(df["close"] - df["open"]) / df["open"]

        # 分级：9% 中跌、12% 暴跌
        mid_crash = (
            (df["ret_4p"].shift(1) < -0.09)
            & (df["ret_4p"].shift(1) >= -0.12)
            & (df["close"] > df["open"])
            & (df["body_pct"] > 0.005)     # A: 实体过滤
            & (df["ret_4p"] >= -0.09)
        )
        big_crash = (
            (df["ret_4p"].shift(1) < -0.12)
            & (df["close"] > df["open"])
            & (df["body_pct"] > 0.005)
            & (df["ret_4p"] >= -0.12)
        )

        df["long_entry"] = (mid_crash | big_crash) & (df["volume"] > 0)

        # B: 分级仓位标记（9-12% = 半仓, >12% = 满仓）
        df["entry_tag"] = "default"
        df.loc[mid_crash & (df["volume"] > 0), "entry_tag"] = "half"
        df.loc[big_crash & (df["volume"] > 0), "entry_tag"] = "full"
        return df

    def populate_entry_trend(self, df, meta):
        df.loc[df["long_entry"], "enter_long"] = 1
        return df

    def populate_exit_trend(self, df, meta):
        df.loc[(df["close"] > df["ema20"]) & (df["close"].shift(1) <= df["ema20"].shift(1)) & (df["volume"] > 0), "exit_long"] = 1
        return df

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake,
                            min_stake, max_stake, entry_tag, side, **kwargs):
        if entry_tag == "half":
            return proposed_stake * 0.5
        return proposed_stake
