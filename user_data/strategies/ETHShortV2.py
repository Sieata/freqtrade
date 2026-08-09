"""
ShortTrend V2 — V1 + BTC 联动过滤

加入 BTC 4h 涨幅过滤：BTC 涨幅 > 1.5% 时暂停做空 ETH。
逻辑：BTC 领涨时 ETH 通常跟涨，此时做空等于逆势。
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Union

import numpy as np
import pandas as pd
from pandas import DataFrame
import talib.abstract as ta

from freqtrade.strategy import IStrategy, Trade, informative, merge_informative_pair


class ETHShortV2(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = True

    timeframe = "4h"
    startup_candle_count: int = 250

    # ── 风控（同 V1）────────────────────────────────────
    stoploss = -0.15
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True
    minimal_roi = {"0": 0.15}

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    max_open_trades = 1
    process_only_new_candles = True

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    def informative_pairs(self):
        return [("BTC/USDT:USDT", "4h")]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # ── ETH 指标（同 V1）───────────────────────────
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        dataframe["is_bear"] = (
            (dataframe["ema50"] < dataframe["ema200"])
            & (dataframe["close"] < dataframe["ema200"])
        )

        dataframe["dist_to_ema50"] = (
            (dataframe["close"] - dataframe["ema50"]) / dataframe["atr"]
        )

        # ── BTC 涨幅过滤 ───────────────────────────────
        btc_df = self.dp.get_pair_dataframe("BTC/USDT:USDT", "4h")
        if btc_df is not None and len(btc_df) > 0:
            btc_ret = btc_df["close"].pct_change()
            btc_ret = btc_ret.reindex(dataframe.index, method="ffill").fillna(0)
            dataframe["btc_surging"] = btc_ret > 0.015  # BTC 涨超 1.5%
        else:
            dataframe["btc_surging"] = False

        # ── 入场（V1 逻辑 + BTC 过滤）─────────────────
        dataframe["at_rally"] = (
            dataframe["is_bear"]
            & (dataframe["dist_to_ema50"] > -0.3)
            & (dataframe["dist_to_ema50"] < 1.5)
            & (~dataframe["btc_surging"])  # BTC 没在拉盘
        )

        dataframe["short_entry"] = (
            dataframe["at_rally"]
            & (dataframe["close"] < dataframe["open"])
            & (~dataframe["at_rally"].shift(1).fillna(False))
        )

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["short_entry"] & (dataframe["volume"] > 0)),
            "enter_short",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe
