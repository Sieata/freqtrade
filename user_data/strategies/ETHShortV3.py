"""
ShortTrend V3 — V2 + ATR 动态止损

用 2 倍 ATR 替代固定 -15% 止损。
波动低时止损窄（减少单笔亏损），波动高时止损宽（给趋势空间）。
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Union

import numpy as np
import pandas as pd
from pandas import DataFrame
import talib.abstract as ta

from freqtrade.strategy import IStrategy, Trade


class ETHShortV3(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = True

    timeframe = "4h"
    startup_candle_count: int = 250

    # ── 风控 ────────────────────────────────────────────
    stoploss = -0.20            # 兜底，实际由 custom_stoploss 管理
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
            dataframe["btc_surging"] = btc_ret > 0.015
        else:
            dataframe["btc_surging"] = False

        # ── 入场（同 V2）───────────────────────────────
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
        """基于入场时 ATR 的动态止损：2 倍 ATR，钳位在 -5% 到 -20%"""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0:
            return self.stoploss

        entry_date = trade.open_date_utc.replace(tzinfo=None)
        entry_rows = dataframe[dataframe.index <= entry_date]
        if len(entry_rows) == 0:
            return self.stoploss

        entry_row = entry_rows.iloc[-1]
        entry_atr = entry_row["atr"]
        entry_close = trade.open_rate

        if entry_close <= 0 or entry_atr <= 0:
            return self.stoploss

        # 2 倍 ATR 作为止损距离
        atr_stop = -(2.0 * entry_atr / entry_close)

        # 钳位：不窄于 -5%，不宽于 -20%
        return max(min(atr_stop, -0.05), -0.20)
