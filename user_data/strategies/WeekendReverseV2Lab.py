"""
WeekendReverseV2Lab — V2 出场端实验台（**非正式策略，不得实盘/paper**）

来源：`WeekendReverseV2.py` 的逐行副本，默认参数与 V2 完全一致。
用途：V2 已冻结（SHA `1b90c90b…46d1`，paper 进行中），任何出场实验只能在本副本上做，
避免动到冻结文件。实验方法：只改本文件一个属性 → 跑 TEST 段 → 记结果。

2026-09-21 单轴扫描结果（TOP10 池 / TEST 20220101-20240828 / 独立口径 $1,000/笔、
`--cache none`、max_open_trades=10；基线 = ROI 8% + 止损 -10% = 693 笔 / +$6,098 / PF 1.82）：

| 轴 | 取值 | 利润$ | PF | 出场结构 |
|---|---|---|---|---|
| minimal_roi | 关闭 | 5,529 | 1.74 | 全走尾随 |
| minimal_roi | 0.10 | 5,900 | 1.79 | roi 10 笔 |
| minimal_roi | **0.08（基线）** | **6,098** | **1.82** | roi 19 笔 |
| minimal_roi | 0.06 | 6,235 | 1.83 | roi 43 笔 |
| minimal_roi | 0.04 | 6,265 | 1.84 | roi 127 笔 |
| minimal_roi | 0.03 | 5,594 | 1.75 | roi 223 笔 |
| minimal_roi | 0.02 | 3,717 | 1.50 | roi 423 笔 |
| stoploss | -0.08 | 5,618 | 1.72 | 止损 96 笔 @-8.1% |
| stoploss | -0.06 | 4,720 | 1.58 | 止损 133 笔 @-6.1% |

读法（细节见 RESEARCH.md 第十七节）：
- 止盈轴在 **4%~6% 存在宽峰**（+2.2~2.7% vs 基线），两侧平滑（3%/10% 都更差）——方向真实，
  但效应量 <3%，**不值得消耗一次性 VAL**，只作为未来版本的默认参考。
- 止损轴**收紧单调变差**（-8% 掉 8% 利润、-6% 掉 23%）——机制：11% 的赢家持有期曾浮亏 ≤-5%，
  收紧止损把赢家砍成亏损单。此轴闭合，不要再试。
"""

from datetime import timedelta

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import (
    IStrategy,
    PairLocks,
    BooleanParameter,
    CategoricalParameter,
    DecimalParameter,
    IntParameter,
)


class WeekendReverseV2Lab(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "4h"
    startup_candle_count: int = 250

    stoploss = -0.10
    trailing_stop = True
    trailing_stop_positive = 0.002
    trailing_stop_positive_offset = 0.015
    trailing_only_offset_is_reached = True
    minimal_roi = {"0": 0.08}

    use_exit_signal = False
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

    buy_drop = DecimalParameter(0.01, 0.05, default=0.02, decimals=3, space="buy", load=True)
    buy_drop2 = DecimalParameter(0.0, 0.10, default=0.0, decimals=3, space="buy", load=True)
    buy_body_min = DecimalParameter(0.0, 0.02, default=0.0, decimals=4, space="buy", load=True)
    buy_vol_mult = DecimalParameter(0.0, 4.0, default=0.0, decimals=2, space="buy", load=True)
    buy_rsi_below = DecimalParameter(0.0, 50.0, default=0.0, decimals=1, space="buy", load=True)
    buy_close_above_prev = BooleanParameter(default=False, space="buy", load=True)
    buy_window_mode = CategoricalParameter(
        ["fri_mon", "sat_mon", "fri_sun", "sat_sun", "fri_mon_full"],
        default="fri_mon",
        space="buy",
        load=True,
    )
    buy_cooldown_h = IntParameter(0, 96, default=0, space="buy", load=True)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ret_1p"] = dataframe["close"].pct_change(periods=1)
        dataframe["ret_2p"] = dataframe["close"].pct_change(periods=2)
        dataframe["body_pct"] = abs(dataframe["close"] - dataframe["open"]) / dataframe["open"]
        dataframe["vol_ma20"] = dataframe["volume"].rolling(20).mean()
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        tss = dataframe["date"]
        bj_h = (tss.dt.hour + 8) % 24
        dow = tss.dt.dayofweek
        mode = self.buy_window_mode.value
        if mode == "fri_mon":
            window = (dow >= 5) | ((dow == 0) & (bj_h <= 21))
        elif mode == "sat_mon":
            window = (dow >= 6) | ((dow == 0) & (bj_h <= 21))
        elif mode == "fri_sun":
            window = dow >= 5
        elif mode == "sat_sun":
            window = dow >= 6
        elif mode == "fri_mon_full":
            window = (dow >= 5) | (dow == 0)
        else:
            window = (dow >= 5) | ((dow == 0) & (bj_h <= 21))

        entry = (
            (dataframe["ret_1p"].shift(1) < -self.buy_drop.value)
            & (dataframe["close"] > dataframe["open"])
            & (dataframe["ret_1p"] >= -self.buy_drop.value)
            & (dataframe["volume"] > 0)
            & window
        )

        if self.buy_drop2.value > 0:
            entry &= (dataframe["ret_2p"].shift(1) < -self.buy_drop2.value)
        if self.buy_body_min.value > 0:
            entry &= (dataframe["body_pct"] > self.buy_body_min.value)
        if self.buy_vol_mult.value > 0:
            entry &= (dataframe["volume"].shift(1) > self.buy_vol_mult.value * dataframe["vol_ma20"].shift(1))
        if self.buy_rsi_below.value > 0:
            entry &= (dataframe["rsi"].shift(1) < self.buy_rsi_below.value)
        if self.buy_close_above_prev.value:
            entry &= (dataframe["close"] > dataframe["close"].shift(1))

        dataframe["long_entry"] = entry
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["long_entry"], "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def confirm_trade_exit(
        self, pair, trade, order_type, amount, rate, time_in_force, exit_reason, current_time, **kwargs
    ) -> bool:
        if self.buy_cooldown_h.value > 0 and "stop_loss" in exit_reason:
            PairLocks.lock_pair(
                pair,
                current_time + timedelta(hours=self.buy_cooldown_h.value),
                reason="cooldown_after_loss",
            )
        return True
