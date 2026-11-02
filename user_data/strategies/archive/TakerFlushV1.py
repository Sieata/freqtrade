"""
TakerFlushV1 — 持仓结构卖压衰竭做多（RESEARCH 27，M4 OIFLOW 原型）

信号（4h，多，事件驱动）：
  - OI 24h 变化 ≤ 自身 180d p5（去杠杆急缩）
  - 收盘 24h 收益 ≤ -5%（缓跌，不是单根急跌）
  - taker 买卖比 24h 均值 ≤ 自身 180d p20（主动卖压极端）
次日开盘进场，持有 24h 定时离场；8% 止损。

与 OIFlushV2 的区别（RESEARCH 27.3）：OIFlush 吃「单根 4h 急跌 + 趋势过滤」，
本策略吃「24h 缓跌 + 卖压衰竭」，TEST 事件重叠仅 8%，是独立臂而非变体。

状态：TEST 事件研究候选（metrics_screen.py / metrics_followup.py）→ freqtrade 原型对账。
VAL 未消耗；TEST 门禁通过后才考虑一次性 VAL。

数据：futures_metrics/*-4h-metrics.feather（oi_usd / taker_ls），回测直读；
      live 依赖 oi_accumulate.py + metrics 增量，指标不全时不出信号。
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


class TakerFlushV1(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "4h"
    startup_candle_count: int = 360

    stoploss = -0.08
    trailing_stop = False
    minimal_roi = {}
    use_exit_signal = True
    process_only_new_candles = True

    # 预注册参数（RESEARCH 27.2/27.3）
    oi_quantile = 0.05
    oi_window = 1080            # 180d @ 4h
    oi_min_periods = 360        # 60d 后才出分位
    drop_24h = -0.05
    taker_quantile = 0.20
    hold_hours = 24

    order_types = {
        "entry": "limit", "exit": "limit",
        "stoploss": "market", "stoploss_on_exchange": False,
    }

    def informative_pairs(self):
        return []  # metrics 无 candle_type，feather 直读（仅回测）；live 走累积器

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        import pandas as pd

        slug = metadata["pair"].replace("/", "_").replace(":", "_")
        path = _metrics_dir() / f"{slug}-4h-metrics.feather"
        if not path.exists():
            dataframe["taker_flush"] = False
            return dataframe
        m = pd.read_feather(path)
        m = m.sort_values("date")
        m["oi_chg24"] = m["oi_usd"] / m["oi_usd"].shift(6) - 1
        m["oi_q"] = m["oi_chg24"].rolling(
            self.oi_window, min_periods=self.oi_min_periods
        ).quantile(self.oi_quantile)
        m["tk24"] = m["taker_ls"].rolling(6).mean()
        m["tk_q"] = m["tk24"].rolling(
            self.oi_window, min_periods=self.oi_min_periods
        ).quantile(self.taker_quantile)
        dataframe = dataframe.merge(
            m[["date", "oi_chg24", "oi_q", "tk24", "tk_q"]], on="date", how="left"
        )
        dataframe["ret24"] = dataframe["close"] / dataframe["close"].shift(6) - 1
        dataframe["taker_flush"] = (
            (dataframe["oi_chg24"] <= dataframe["oi_q"])
            & (dataframe["ret24"] <= self.drop_24h)
            & (dataframe["tk24"] <= dataframe["tk_q"])
        )
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            dataframe["taker_flush"].fillna(False).astype(bool)
            & (dataframe["volume"] > 0),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair, trade: Trade, current_time, current_rate, current_profit, **kwargs):
        if (current_time - trade.open_date_utc).total_seconds() >= self.hold_hours * 3600:
            return f"hold_{self.hold_hours}h"
        return None
