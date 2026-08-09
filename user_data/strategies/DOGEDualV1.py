"""
DOGEDualV1 — 暴跌做多 + 暴涨做空

双向策略。4h K线。4 期跌 >8% 后阳线做多，4 期涨 >8% 后阴线做空。
❌ 过拟合对照：仅 DOGE 盈利，换品种崩。统一参数仅对 DOGE 有效。

风控：-12% 硬止损 | 盈利 6% 激活尾随、步长 3% | 25% 止盈 | EMA20 离场
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Union
import numpy as np
import pandas as pd
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.strategy import IStrategy, Trade


class DOGEDualV1(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = True
    timeframe = "4h"
    startup_candle_count: int = 250

    # ── 多空共用风控 ────────────────────────────────────
    stoploss = -0.12                         # 硬止损 -12%
    trailing_stop = True                     # 尾随止损
    trailing_stop_positive = 0.03            # 步长 3%
    trailing_stop_positive_offset = 0.06     # 盈利 6% 后激活
    trailing_only_offset_is_reached = True   # 到达偏移后才启动
    minimal_roi = {"0": 0.25}                # 止盈 25%（实际靠尾随离场）

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    max_open_trades = 1
    process_only_new_candles = True

    order_types = {
        "entry": "limit", "exit": "limit",
        "stoploss": "market", "stoploss_on_exchange": False,
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ret_4p"] = dataframe["close"].pct_change(periods=4)
        dataframe["body_pct"] = abs(dataframe["close"] - dataframe["open"]) / dataframe["open"]

        # ── 做多：暴跌后阳线 ──────────────────────────
        dataframe["long_entry"] = (
            (dataframe["ret_4p"].shift(1) < -0.08)
            & (dataframe["close"] > dataframe["open"])
            & (dataframe["body_pct"] > 0.005)
            & (dataframe["ret_4p"] >= -0.08)
            & (dataframe["volume"] > 0)
        )

        # ── 做空：暴涨后阴线 ──────────────────────────
        dataframe["short_entry"] = (
            (dataframe["ret_4p"].shift(1) > 0.10)
            & (dataframe["close"] < dataframe["open"])
            & (dataframe["body_pct"] > 0.005)
            & (dataframe["ret_4p"] <= 0.10)
            & (dataframe["volume"] > 0)
        )

        return dataframe

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[df["long_entry"], "enter_long"] = 1
        df.loc[df["short_entry"], "enter_short"] = 1
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        # 做多离场：回到 EMA20
        df.loc[(df["close"] > df["ema20"]) & (df["close"].shift(1) <= df["ema20"].shift(1)) & (df["volume"] > 0), "exit_long"] = 1
        # 做空离场：回到 EMA20
        df.loc[(df["close"] < df["ema20"]) & (df["close"].shift(1) >= df["ema20"].shift(1)) & (df["volume"] > 0), "exit_short"] = 1
        return df
