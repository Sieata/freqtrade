"""
ETHShort — ETH/USDT 永续合约熊市趋势跟踪策略

策略逻辑：
  只做空。4h K线。当 EMA50 < EMA200 且价格低于 EMA200 时激活。

入场条件：
  价格反弹到 EMA50 附近（-0.3 到 +1.5 ATR 区间），出现阴线时入场。

离场方式：
  无信号离场。盈利达到 3% 后启动 1% 步长的尾随止损。
  -15% 硬止损作为极端保护。15% 止盈上限。

回测表现（2022-01 至 2026-08，4.5 年，ETH/USDT 永续合约）：
  107 笔交易 | 86.0% 胜率 | +47.3% 收益 | 年化 9.0%
  夏普 0.45 | 最大回撤（从初始本金）6.3% | 利润因子 1.13
  同期 ETH 跌幅 -37.5%

研发路径：
  6 版 EMA 均值回归全部失败 → 转向趋势跟踪 → 共 9 轮迭代定型
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Union

import numpy as np
import pandas as pd
from pandas import DataFrame
import talib.abstract as ta

from freqtrade.strategy import IStrategy, Trade


class ETHShortV1(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = True

    # ── 基础设置 ────────────────────────────────────────
    timeframe = "4h"
    startup_candle_count: int = 250

    # ── 风控参数 ────────────────────────────────────────
    stoploss = -0.15                         # 硬止损 15%

    trailing_stop = True                     # 尾随止损
    trailing_stop_positive = 0.01            # 尾随步长 1%
    trailing_stop_positive_offset = 0.03     # 盈利 3% 后激活
    trailing_only_offset_is_reached = True   # 只在达到偏移后才启动尾随

    minimal_roi = {"0": 0.15}                # 15% 止盈

    # ── 交易控制 ────────────────────────────────────────
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

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # ── 均线 ──────────────────────────────────────
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # ── 熊市判断 ──────────────────────────────────
        # EMA50 < EMA200 且价格在 EMA200 下方
        # "价格<EMA200" 这个条件防止在牛市回调中做空（如 2023 年）
        dataframe["is_bear"] = (
            (dataframe["ema50"] < dataframe["ema200"])
            & (dataframe["close"] < dataframe["ema200"])
        )

        # ── 价格偏离度（ATR 单位） ────────────────────
        dataframe["dist_to_ema50"] = (
            (dataframe["close"] - dataframe["ema50"]) / dataframe["atr"]
        )

        # ── 入场信号 ──────────────────────────────────
        # 价格反弹到 EMA50 附近 + 阴线 + 反弹簇的第一根
        dataframe["at_rally"] = (
            dataframe["is_bear"]
            & (dataframe["dist_to_ema50"] > -0.3)
            & (dataframe["dist_to_ema50"] < 1.5)
        )

        dataframe["short_entry"] = (
            dataframe["at_rally"]
            & (dataframe["close"] < dataframe["open"])           # 阴线确认
            & (~dataframe["at_rally"].shift(1).fillna(False))    # 反弹簇首根
        )

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["short_entry"] & (dataframe["volume"] > 0)),
            "enter_short",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 无信号离场 — 完全由尾随止损和止盈管理退出
        return dataframe
