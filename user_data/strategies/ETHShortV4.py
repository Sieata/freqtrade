"""
ShortTrend V4 — V3 + 短期趋势确认

新增：价格必须在 SMA20 下方才允许做空。
即使长周期熊市，短期反弹中也不逆势入场。
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Union

import numpy as np
import pandas as pd
from pandas import DataFrame
import talib.abstract as ta

from freqtrade.strategy import IStrategy, Trade


class ETHShortV4(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = True

    timeframe = "4h"
    startup_candle_count: int = 250

    # ── 风控（同 V3）────────────────────────────────────
    stoploss = -0.20
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
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["sma20"] = ta.SMA(dataframe, timeperiod=20)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # 熊市：EMA 空头排列 + 价格在 EMA200 下方 + EMA50 仍在下降
        # EMA50 比 10 根 K 线前更低 → 趋势方向仍向下
        dataframe["is_bear"] = (
            (dataframe["ema50"] < dataframe["ema200"])
            & (dataframe["close"] < dataframe["ema200"])
            & (dataframe["ema50"] < dataframe["ema50"].shift(10))  # EMA50 仍在下降
        )

        dataframe["dist_to_ema50"] = (
            (dataframe["close"] - dataframe["ema50"]) / dataframe["atr"]
        )

        # ── BTC 涨幅过滤 ───────────────────────────────
        btc_df = self.dp.get_pair_dataframe("BTC/USDT:USDT", "4h")
        if btc_df is not None and len(btc_df) > 0:
            btc_ret = btc_df["close"].pct_change()
            btc_ret = btc_ret.reindex(dataframe.index, method="ffill").fillna(0)
            dataframe["btc_surging"] = btc_ret > 0.015
        else:
            dataframe["btc_surging"] = False

        # ── 入场 ───────────────────────────────────────
        dataframe["at_rally"] = (
            dataframe["is_bear"]
            & (dataframe["dist_to_ema50"] > -0.3)
            & (dataframe["dist_to_ema50"] < 1.5)
            & (~dataframe["btc_surging"])
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

    def custom_stoploss(
        self,
        pair: str,
        trade: "Trade",
        current_time: "datetime",
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> Optional[float]:
        """ATR 动态止损：2 倍 ATR，钳位 -5% ~ -20%"""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0:
            return self.stoploss

        entry_date = trade.open_date_utc.replace(tzinfo=None)
        entry_rows = dataframe[dataframe.index <= entry_date]
        if len(entry_rows) == 0:
            return self.stoploss

        entry_atr = entry_rows.iloc[-1]["atr"]
        entry_close = trade.open_rate
        if entry_close <= 0 or entry_atr <= 0:
            return self.stoploss

        atr_stop = -(2.0 * entry_atr / entry_close)
        return max(min(atr_stop, -0.05), -0.20)
