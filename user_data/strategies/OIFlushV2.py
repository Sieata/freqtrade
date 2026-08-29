"""
OIFlushV2 — 清算去杠杆尾声做多 × 趋势过滤（OIFlushV1 复活版，RESEARCH 12.3/12.4）

与 V1 的差异（预注册复活实验的胜出形态）：
  - 新增趋势过滤：**30 天动量 > 0**（close > close[180 根 4h]）才允许接刀。
    依据 h4r_phase1（TEST）：过滤后均值 +3.10%→+6.41%/笔，2022 年 -0.30%→+0.70%（转正），
    品种净正 10/11，与 BigMove/CrashBuy K 线重叠 0%；MA200 过滤变体被 kill（2022 更差）。
  - hold 48h（预注册主形态）。
  机制解释：中期上升趋势中的急跌+OI 急缩 = 回调买点；下跌趋势中 = 下跌中继（V1 的死因）。

状态: 原型 → A/B 网格（hold × oi_quantile）→ 标准验证（TOP10 第一口径 + CORE50 参考，VAL 一次性）。
数据: metrics feather 直读（同 V1L 模式，路径向上搜索 user_data）；
     live 需 OI 历史累积器（openInterestHist 仅 30 天，oi_accumulate.py 滚动入库）。
"""

from pathlib import Path

from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


def _metrics_dir() -> Path:
    here = Path(__file__).resolve()
    for base in [here, *here.parents]:
        cand = base / "user_data" / "data" / "binance" / "futures_metrics"
        if cand.is_dir():
            return cand
    return Path("user_data/data/binance/futures_metrics")


class OIFlushV2(IStrategy):
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

    # 可调参数（A/B 网格扫描用；默认 = 预注册胜出形态）
    oi_quantile = 0.05
    oi_window = 1080            # 180d @ 4h
    oi_min_periods = 360
    crash_threshold = -0.05
    hold_hours = 48
    mom_window = 180            # 30d @ 4h（趋势过滤窗）

    order_types = {
        "entry": "limit", "exit": "limit",
        "stoploss": "market", "stoploss_on_exchange": False,
    }

    def informative_pairs(self):
        return []  # metrics 无 candle_type，feather 直读（仅回测）；live 走 OI 累积器

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        import pandas as pd

        slug = metadata["pair"].replace("/", "_").replace(":", "_")
        path = _metrics_dir() / f"{slug}-4h-metrics.feather"
        if not path.exists():
            # 无 OI 数据的品种（数据可用性边界）：零信号，不参与策略
            dataframe["flush"] = False
            return dataframe
        m = pd.read_feather(path)
        m["oi_chg"] = m["oi_usd"] / m["oi_usd"].shift(6) - 1
        m["oi_q"] = m["oi_chg"].rolling(self.oi_window, min_periods=self.oi_min_periods).quantile(self.oi_quantile)
        dataframe = dataframe.merge(m[["date", "oi_chg", "oi_q"]], on="date", how="left")
        dataframe["ret4"] = dataframe["close"] / dataframe["close"].shift(1) - 1
        # 趋势过滤: 30 天动量 > 0
        dataframe["mom30"] = dataframe["close"] / dataframe["close"].shift(self.mom_window) - 1
        dataframe["flush"] = (
            (dataframe["oi_chg"] <= dataframe["oi_q"])
            & (dataframe["ret4"] <= self.crash_threshold)
            & (dataframe["mom30"] > 0)
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
