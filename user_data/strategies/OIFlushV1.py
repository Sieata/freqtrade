"""
OIFlushV1 — 清算去杠杆尾声做多（原型，来自 RESEARCH 十二 H4）

假设: 4h 收益 ≤ -5% 且 OI(USD) 24h 变化进入自身 180d p5 极端收缩 = 多头强制平仓临近尾声,
      抛压来自清算而非新增空头 → 后续 48h 反弹漂移（Phase1-metrics 实测 +3.371%/笔, 9/9 品种,
      逐年 +0.08/+4.05/+6.15%, TEST 20220101-20240828, 摩擦 0.1%）。
      与 FundingSqueezeV1（资金费"价"维度）不同源: 信号重叠仅 7-9%; 2022 年互补（FS 强/OIFlush 死）。

实现要点:
  - 数据: user_data/data/binance/futures/{PAIR}-4h-metrics.feather（import_metrics_vision.py 生成,
    oi_usd 4h 末值）。回测直接读 feather; **live/paper 不可用**——metrics 无 freqtrade candle_type,
    且 binance openInterestHist API 仅 30 天历史, 180d 分位窗需自建累积, 上 paper 前必须先解决取数。
  - 指标在 metrics 全史上预计算（rolling 1080×4h, min 360）再 merge, 与 metrics_phase1.py 逐位一致,
    不受 startup_candle_count 截断影响。
  - 入场 = 进入"急跌+OI急缩"状态的过渡K线（state & ~state.shift(1)）, 同 FundingSqueeze 的防重复口径。
  - 出场 = 持有 48h（custom_exit）; 固定止损 -10%; 无 ROI/尾随（原型阶段纪律）。
  - 已知坑复用: use_exit_signal 必须为 True（False 会连 custom_exit 一起禁用, 见 FS V1 注释）。

状态: 原型 → 待 A/B 过拟合检测（hold/quantile/crash 阈值网格）+ 标准验证（VAL 未跑, 一次性纪律）。
"""

from pathlib import Path

from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

def _metrics_dir() -> Path:
    """metrics feather 目录；变体跑在临时目录时向上搜索 user_data（fsq_batch 模式兼容）。"""
    here = Path(__file__).resolve()
    for base in [here, *here.parents]:
        cand = base / "user_data" / "data" / "binance" / "futures_metrics"
        if cand.is_dir():
            return cand
    return Path("user_data/data/binance/futures_metrics")


class OIFlushV1(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "4h"
    startup_candle_count: int = 600

    stoploss = -0.10
    trailing_stop = False
    minimal_roi = {}
    use_exit_signal = True
    process_only_new_candles = True
    max_open_trades = 8

    # 可调参数（A/B 网格扫描用; 默认=Phase1-metrics 达标形态）
    oi_quantile = 0.05          # OI 24h 变化的 180d 分位阈值
    oi_window = 1080            # 180d @ 4h
    oi_min_periods = 360
    crash_threshold = -0.05     # 4h 收益急跌阈值
    hold_hours = 48             # 持有时长（Phase1 扫描选出, 预注册主形态 72h 未达标）

    order_types = {
        "entry": "limit", "exit": "limit",
        "stoploss": "market", "stoploss_on_exchange": False,
    }

    def informative_pairs(self):
        # metrics 无 candle_type, 数据在 populate_indicators 内直接读 feather（仅回测）
        return []

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        import pandas as pd

        m = pd.read_feather(_metrics_dir() / f"{metadata['pair'].replace('/', '_').replace(':', '_')}-4h-metrics.feather")
        m["oi_chg"] = m["oi_usd"] / m["oi_usd"].shift(6) - 1
        m["oi_q"] = m["oi_chg"].rolling(self.oi_window, min_periods=self.oi_min_periods).quantile(self.oi_quantile)
        m = m[["date", "oi_chg", "oi_q"]]

        dataframe = dataframe.merge(m, on="date", how="left")
        dataframe["ret4"] = dataframe["close"] / dataframe["close"].shift(1) - 1
        dataframe["flush"] = (dataframe["oi_chg"] <= dataframe["oi_q"]) & (
            dataframe["ret4"] <= self.crash_threshold
        )
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        state = dataframe["flush"].fillna(False).astype(bool)
        dataframe.loc[
            state & ~state.shift(1, fill_value=False) & (dataframe["volume"] > 0),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair, trade: Trade, current_time, current_rate, current_profit, **kwargs):
        if (current_time - trade.open_date_utc).total_seconds() >= self.hold_hours * 3600:
            return "hold_48h"
        return None
