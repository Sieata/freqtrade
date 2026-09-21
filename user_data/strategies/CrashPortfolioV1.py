"""
CrashPortfolioV1 — BTC 崩盘触发组合买入（RESEARCH 24.5 CRASHP 的 freqtrade 原型）

机制（TEST 20220101-20240828 · TOP10 池 · 事件级 t=+2.25 / p=0.025，全门禁通过）：
  BTC 24h 跌幅 <= -5% 时，买入池内自身 24h 跌幅最深的前 3 个非 BTC 品种，持 24h 定时离场。

与 CrashBuyV2 的差异（±24h 同品种重叠 37.8% < 70% = 独立新臂）：
  V2 等品种自身 16h 跌 >= 9% 且当根收阳确认才进（接已确认的底，可带 -8% 止损）；
  本策略不等确认直接接刀（更早、更便宜、尾部更重），反弹日 funding 深负时还能白收空头付费。

风险形态（RESEARCH 24.6，冻结结论，改前必读）：
  - 无止损：加 8-12% 止损会把事件级 t 从 2.25 杀到 1.0-1.2（反弹藏在「先再跌 8-12% 再 V 回」
    的路径里，止损正好砍掉能赢的路径）；
  - 风险控制靠小仓位（定版建议 $250-300/笔），不是止损；
  - TEST 段无止损形态最差单笔 -50.5%（SOL，FTX 2022-11-08 16:00 接刀）。

执行口径（与 idea_screen.py 仿真严格一致，勿改）：
  - 信号在 4h K 线收盘出（BTC r24 与各品种 r24 全用收盘价），下一根开盘进场；
    custom_exit 在满 24h 的那根 K 线开盘离场（回测 shift(1) 语义下与仿真 X=E+24h 精确等价）；
  - worst3 = 当根收盘各非 BTC 品种 r24 升序排名前 3（数据缺失品种 NaN 自动剔除，
    含 HYPE 上市前 / XMR 退市后）；
  - max_open_trades 不在类里定义（策略类值会覆盖 config），留给 config/CLI：
    验证口径 = 池内品种数（与仿真的品种级冷却等价），实盘 = 资金分散设计。

用法（TEST 复核，须带代理）：
  export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897
  ./.venv/Scripts/python.exe -m freqtrade backtesting --config user_data/config_perpetual.json \
    --strategy CrashPortfolioV1 --timerange 20220101-20240828 \
    --pairs BTC/USDT:USDT ETH/USDT:USDT BNB/USDT:USDT XRP/USDT:USDT SOL/USDT:USDT \
            TRX/USDT:USDT ZEC/USDT:USDT DOGE/USDT:USDT XMR/USDT:USDT \
    --cache none --export trades --max-open-trades 9 --stake-amount 1000 --dry-run-wallet 10800
"""

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


class CrashPortfolioV1(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "4h"
    startup_candle_count: int = 40

    stoploss = -0.99
    trailing_stop = False
    minimal_roi = {}
    use_exit_signal = True
    process_only_new_candles = True

    # CRASHP 参数（RESEARCH 24.5 TEST 段胜出形态，冻结勿调）
    btc_pair = "BTC/USDT:USDT"
    btc_trigger = -0.05
    worst_k = 3
    hold_hours = 24

    order_types = {
        "entry": "limit", "exit": "limit",
        "stoploss": "market", "stoploss_on_exchange": False,
    }

    def informative_pairs(self):
        if self.dp:
            return [(p, self.timeframe) for p in self.dp.current_whitelist()]
        return []

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]
        # 自身 24h 收益（当根收盘已实现，供排名与核查）
        dataframe["r24"] = dataframe["close"].pct_change(6)

        # BTC 触发腿：r24 <= -5%（同根收盘口径，无前视）
        # 注意：index 必须显式 DatetimeIndex——date.values 是 object 数组，map 会全 NaN（已踩坑）
        btc = self.dp.get_pair_dataframe(self.btc_pair, self.timeframe)
        btc_r24 = pd.Series(btc["close"].pct_change(6).values,
                            index=pd.DatetimeIndex(btc["date"]))
        dataframe["btc_r24"] = dataframe["date"].map(btc_r24)

        # 横截面：白名单内全部非 BTC 品种当根 r24 排名（1 = 跌最深；缺数据 = NaN 剔除）
        panel = {}
        for p in self.dp.current_whitelist():
            if p == self.btc_pair:
                continue
            pdf = self.dp.get_pair_dataframe(p, self.timeframe)
            panel[p] = pd.Series(pdf["close"].pct_change(6).values,
                                index=pd.DatetimeIndex(pdf["date"]))
        ranks = pd.DataFrame(panel).rank(axis=1, method="first")
        if pair in ranks.columns:
            dataframe["r24_rank"] = dataframe["date"].map(ranks[pair])
        else:
            dataframe["r24_rank"] = np.nan
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        sig = (
            dataframe["btc_r24"].le(self.btc_trigger)
            & dataframe["r24_rank"].le(self.worst_k)
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[sig, "enter_long"] = 1
        # enter_tag 只能在此处赋值（advise_entry 会先清空该列）
        dataframe.loc[sig, "enter_tag"] = "crashp"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair, trade: Trade, current_time, current_rate, current_profit, **kwargs):
        if (current_time - trade.open_date_utc).total_seconds() >= self.hold_hours * 3600:
            return "hold_24h"
        return None
