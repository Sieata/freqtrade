"""
FundingSqueezeV1L — FundingSqueezeV1 的 live/paper 就绪变体（参数与信号逻辑不变）。

与原版的三处差异（均为 live 就绪修复，非参数变更）：
  1. funding 读取走双路径：live/paper 用 informative_pairs 声明的 dp.get_pair_dataframe 缓存
     （交易所 fetch_funding_rate_history）；回测同 API 亦从磁盘加载（等价性已验证）；
     live 下缓存为空时显式告警并拒绝静默回退旧盘数据。
  2. startup_candle_count 600→2160：live informative 拉取深度 = startup_candle_count，
     90d 分位窗需 ≥2160 根 1h funding（原 600 只够 25d，实盘分位窗会被截断）。
     回测不受影响（funding 羽毛文件含全史，rolling 在合并后网格上计算）。
  3. populate_indicators 打点日志：funding 行数与最新结算时间，供 dry-run 验证数据链路。

验证清单（2026-08-29）：
  [x] 回测等价：CORE 11 品种 × TEST 与原版逐项一致（457 笔 / +$3,460 / PF 1.29）
  [x] dry-run live 取数：3 品种实跑，funding informative 正常拉取、指标非空（见 RESEARCH 11.7）
"""

import logging
from pathlib import Path

from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

logger = logging.getLogger(__name__)


class FundingSqueezeV1L(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "4h"
    # live informative 拉取深度 = 此值；90d funding 窗 = 2160 根 1h（见模块 docstring 差异 2）
    startup_candle_count: int = 2160

    stoploss = -0.10
    trailing_stop = False
    minimal_roi = {}
    use_exit_signal = True
    process_only_new_candles = True
    max_open_trades = 8

    fund_quantile = 0.02
    fund_window = 540
    hold_hours = 72

    order_types = {
        "entry": "limit", "exit": "limit",
        "stoploss": "market", "stoploss_on_exchange": False,
    }

    def informative_pairs(self):
        try:
            pairs = self.dp.current_whitelist()
        except Exception:
            pairs = []
        return [(p, "1h", "funding_rate") for p in pairs]

    def _funding(self, pair: str) -> DataFrame:
        f = None
        try:
            f = self.dp.get_pair_dataframe(pair, "1h", candle_type="funding_rate")
        except Exception as e:
            logger.warning(f"funding live-cache read failed for {pair}: {e}")
        if f is None or f.empty:
            f = self.dp.historic_ohlcv(pair, "1h", candle_type="funding_rate")
            if self.dp.runmode.value not in ("backtest", "hyperopt", "plot"):
                logger.warning(
                    f"funding live-cache EMPTY for {pair}, fell back to disk (STALE in live!)")
        return f

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        import pandas as pd

        fund = self._funding(metadata["pair"])
        fund = fund[fund["open"] != 0][["date", "open"]].rename(columns={"open": "fund_rate"})
        fund = fund.set_index("date")
        if self.dp.runmode.value == "dry_run":
            logger.info(
                f"[V1L] {metadata['pair']}: funding rows={len(fund)}, "
                f"latest={fund.index.max() if len(fund) else 'NONE'}")
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
