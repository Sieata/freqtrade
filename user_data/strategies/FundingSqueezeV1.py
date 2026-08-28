"""
FundingSqueezeV1 — 资金费极端负值做多（原型）

假设: 资金费打到自身 90 天分位极端低位 = 空头拥挤,付费做空不可持续 → 未来 3 天正漂移（轧空燃料）。
      SynthPut 系列只证伪了做空侧(S11 高费做空),做多侧为全新信息源(衍生品持仓),与现有 4 策略互补。

Phase1 事件研究（newedge_phase1.py, TEST 20220101-20240828, 摩擦 0.1%）:
  fund<=p2(90d) hold=72h: n=3011 胜率 57.0% 均值 +0.913%/笔 品种净正 10/10 逐年全正(+0.52/+0.68/+1.04%)
  基线(无条件 72h) = -0.078%。长持更优(24h+0.36% → 72h+0.91%),故持有 72h。

实现要点:
  - funding 取数: dp.historic_ohlcv(pair, "1h", candle_type="funding_rate")（回测从磁盘读;
    live/paper 的 funding informative 取数未验证,上 paper 前需先解决）。
  - 过滤 open!=0: freqtrade 加载的 1h funding 网格在非结算小时填 0（ohlcv_load fill_missing）,
    必须滤掉才能还原真实事件序列;代价是 BNB 真实的 0 费率结算也被滤掉（BNB 费率被钳制频繁打印
    0.00000000,属其特殊机制）——对分位数影响极小,已在 Phase1 数据口径中核实。
  - 入场 = 进入"费率≤90d p2 分位"状态的过渡K线（state & ~state.shift(1),避免同段重复入场）。
  - 出场 = 持有 72h（custom_exit）;固定止损 -10%;无 ROI/无尾随（原型阶段纪律）。
  - 注意: 做多侧在负费率时段持仓,每 8h 还能**收**资金费,freqtrade 会建模（Phase1 未计入,偏保守）。

状态: 原型 → 待 A/B/C 过拟合检测 + 标准验证(TEST/VAL × core/volume)。
"""

from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


class FundingSqueezeV1(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "4h"
    # 90d 分位窗(540 根 4h) + 缓冲
    startup_candle_count: int = 600

    stoploss = -0.10
    trailing_stop = False
    minimal_roi = {}
    # 必须为 True: use_exit_signal=False 会连 custom_exit 一起禁用（72h 定期离场失效,
    # 2026-08-29 踩坑——持仓被拖到回测结束强平）。离场信号列保持为空即可。
    use_exit_signal = True
    process_only_new_candles = True
    max_open_trades = 8

    # 可调参数（B 参数稳定性扫描用）
    fund_quantile = 0.02        # 费率分位阈值
    fund_window = 540           # 90d @ 4h
    hold_hours = 72             # 持有时长

    order_types = {
        "entry": "limit", "exit": "limit",
        "stoploss": "market", "stoploss_on_exchange": False,
    }

    def informative_pairs(self):
        # funding_rate 数据随 futures 回测自动加载;显式声明以便 live/paper 补数
        try:
            pairs = self.dp.current_whitelist()
        except Exception:
            pairs = []
        return [(p, "1h", "funding_rate") for p in pairs]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        fund = self.dp.historic_ohlcv(metadata["pair"], "1h", candle_type="funding_rate")
        fund = fund[fund["open"] != 0][["date", "open"]].rename(columns={"open": "fund_rate"})
        fund = fund.set_index("date")
        dataframe = dataframe.merge(
            fund.reindex(dataframe["date"], method="ffill").rename(columns={"open": "fund_rate"}),
            left_on="date", right_index=True, how="left",
        )
        dataframe["fund_q"] = (
            dataframe["fund_rate"].rolling(self.fund_window, min_periods=200).quantile(self.fund_quantile)
        )
        dataframe["fund_state"] = dataframe["fund_rate"] <= dataframe["fund_q"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        state = dataframe["fund_state"].fillna(False).astype(bool)
        dataframe.loc[
            state & ~state.shift(1, fill_value=False) & (dataframe["volume"] > 0),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair, trade: Trade, current_time, current_rate, current_profit, **kwargs):
        if (current_time - trade.open_date_utc).total_seconds() >= self.hold_hours * 3600:
            return "hold_72h"
        return None
