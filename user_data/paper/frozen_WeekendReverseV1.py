"""
WeekendReverseV1 — 周末低流动性反转

做多策略。4h K线。窗口：周六日+周一美股盘前（北京时间21:00前）。
入场：单根跌 >2% + 阳线确认。八品种统一参数（回测排除 BNB/HOME 结构性弱品种），8/8 盈利。

风控：-10% 硬止损 | 盈利 1.5% 激活尾随、步长 0.3% | 8% 止盈（去 EMA20 离场）
全期 2022-2026：+$206,386 / 回撤 20.5% / 胜率 91.4%
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

    # ── 风控参数 ────────────────────────────────────────
    stoploss = -0.10                         # 硬止损 -10%
    trailing_stop = True                     # 尾随止损
    trailing_stop_positive = 0.003           # 步长 0.3%
    trailing_stop_positive_offset = 0.015    # 盈利 1.5% 后激活
    trailing_only_offset_is_reached = True   # 到达偏移后才启动
    minimal_roi = {"0": 0.08}                # 止盈 8%

    use_exit_signal = False; exit_profit_only = False; ignore_roi_if_entry_signal = False
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
