"""
WeekendReverseV2 — 周末低流动性反转 (改进版)

基线 = WeekendReverseV1:
  4h K线, 周末+周一盘前窗口, 前一根跌 >2% + 当前阳线
  -10% 硬止损 | 尾随 1.5% 激活/0.3% 步长 | 8% 止盈 | 满仓复利 max_open_trades=1

相对 V1 的两处改进(2022-2026, 8品种, freqtrade 回测):
  1. 去掉 populate_exit_trend 的 EMA20 信号 —— V1 里该信号即使 use_exit_signal=False,
     也会在 check_for_trade_entry 中阻挡同根K线入场(隐藏过滤器), 去掉后多 37 笔,
     利润 $206,386 → $255,811。
  2. 尾随止损步长 0.3% → 0.2% —— 更贴近峰值锁定利润, 逐年稳定 +13~16%。

合体(默认参数): 515 笔 / +$393,416 / 胜率 90.9% / PF 1.93 / 回撤(钱包口径) 32.1%
逐年独立 $1,000: 2022 +2329 / 2023 +2480 / 2024 +1154 / 2025 +3761 / 2026 +2097

可选旋钮(通过 <策略名>.json 或改默认值):
  buy_drop / buy_drop2 / buy_body_min / buy_vol_mult / buy_rsi_below /
  buy_close_above_prev / buy_window_mode / buy_cooldown_h
  stoploss -0.12 全期 +$486k 但回撤 ~39%(2025-2026 更强), 属激进档
  minimal_roi 0.06 全期略优但 2024-2026 偏弱, 默认保持 0.08

注意: 本文件不参与 WeekendReverseV1 的 forward-test(该测试 SHA 已冻结),
如需实盘请单独走一遍 V2 的样本外验证。
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


class WeekendReverseV2(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "4h"
    startup_candle_count: int = 250

    # ── 风控(可被参数文件覆盖) ────────────────────────
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

    # ── 可调参数 ──────────────────────────────────────
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
        # 注意: 不写任何 exit 信号列 —— V1 的 EMA20 exit 信号会意外阻挡同根K线入场
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
