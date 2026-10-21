"""
OIFlushV2Lab — OIFlushV2 参数敏感性实验台（**非正式策略，不得实盘/paper**）

来源：`OIFlushV2.py` 的逐行副本，默认参数与 OIFlushV2 完全一致
（oi_quantile=0.05 / oi_window=1080 / oi_min_periods=360 / crash_threshold=-0.05 /
hold_hours=48 / mom_window=180）。

用途：OIFlushV2 已定版（SHA 冻结），任何参数实验只能在本副本上做，
避免动到正式策略文件。实验方法：临时改本文件一个类属性 → 跑 TEST 段 → 记录 → 还原。

⚠️ 本文件仅用于研究，不得配置到 paper/live。

参数覆盖：可用环境变量 OILAB_<PARAM> 覆盖类属性，便于单轴扫描时不改文件、不产生
"忘了还原"的事故。例：OILAB_OI_QUANTILE=0.03 OILAB_HOLD_HOURS=72。

背景（RESEARCH 26.12）：OIFlushV2 此前只做过 6 点 A/B 网格（hold × oi_quantile），
且 n=44 极小，是两臂中证据最薄的一处。本节做三轴邻域扫描，全部只在 TEST 段。
"""

import os
from pathlib import Path

from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


def _env_float(name, default):
    v = os.environ.get(name)
    return float(v) if v not in (None, "") else default


def _env_int(name, default):
    v = os.environ.get(name)
    return int(v) if v not in (None, "") else default


def _metrics_dir() -> Path:
    here = Path(__file__).resolve()
    for base in [here, *here.parents]:
        cand = base / "user_data" / "data" / "binance" / "futures_metrics"
        if cand.is_dir():
            return cand
    return Path("user_data/data/binance/futures_metrics")


class OIFlushV2Lab(IStrategy):
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

    # 可调参数（默认 = OIFlushV2 定版值；OILAB_* 环境变量可覆盖，用于单轴扫描）
    oi_quantile = _env_float("OILAB_OI_QUANTILE", 0.05)
    oi_window = _env_int("OILAB_OI_WINDOW", 1080)            # 180d @ 4h
    oi_min_periods = _env_int("OILAB_OI_MIN_PERIODS", 360)
    crash_threshold = _env_float("OILAB_CRASH_THRESHOLD", -0.05)
    hold_hours = _env_int("OILAB_HOLD_HOURS", 48)
    mom_window = _env_int("OILAB_MOM_WINDOW", 180)           # 30d @ 4h（趋势过滤窗）

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
