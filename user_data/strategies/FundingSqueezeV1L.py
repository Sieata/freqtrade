"""
FundingSqueezeV1L — FundingSqueezeV1 的 live/paper 就绪变体（参数与信号逻辑不变）。

与原版的三处差异（均为 live 就绪修复，非参数变更）：
  1. funding 读取走双路径：live/paper 用 informative_pairs 声明的 dp.get_pair_dataframe 缓存
     （交易所 fetch_funding_rate_history）；回测下 get_pair_dataframe 无预载缓存 → 自动回退
     feather 直读全史（与原版 historic_ohlcv 等价，已验证）。
  2. startup_candle_count 600→2160：live informative 拉取深度 = startup_candle_count，
     90d 分位窗需 ≥2160 根 1h funding（原 600 只够 25d，实盘分位窗会被截断）。
  3. informative_pairs 按运行模式分流（差异 2 的必要配套）：回测返回空 —— freqtrade 回测会按
     startup_candle_count(1h) 预载/裁剪 informative 数据，funding 历史 < 2160h 的新上市品种
     会被整对静默丢弃（KAS/ONDO/JUP 等根因）；回测 feather 直读不受影响。
  4. populate_indicators 打点日志：funding 行数与最新结算时间，供 dry-run 验证数据链路。

回测口径说明：本变体回测 = 全史 feather + df 起点前移（startup 2160×4h），分位窗自 2022 年起
即为完整 90d 口径（原版 V1 在 2021Q4 存在窗口截断，2022 年初信号有边界级联差异，±1-3 笔/对）。
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

    # startup 按运行模式取值（关键坑，2026-08-29）：
    #   live/paper 2160 —— informative 拉取深度 = startup_candle_count，90d 分位窗需足量
    #   1h funding（dry-run 实测 625 条结算，覆盖 ≥90d）；
    #   回测 600×4h —— startup 同时作用于主 K 线暖机：2160×4h=360d 会把每个品种 K 线历史的
    #   头一年变成不可交易暖机，历史 < 360d 的新上市品种被整体丢弃（KAS/ONDO/JUP 等根因），
    #   且 1h funding informative 预载会被裁剪引发 2022 边界级联。回测的 funding 走 feather
    #   全史直读（informative_pairs 已按模式分流），无需深暖机。
    #   判别用 config['runmode']：解析期 dp 未挂载而 config 已赋值；极早期读取（无 config）
    #   按 2160 兜底，仅影响 OHLCV 调用次数告警，无行为差异。
    @property
    def startup_candle_count(self) -> int:
        cfg = getattr(self, "config", None) or {}
        rm = str(cfg.get("runmode", ""))
        return 600 if rm in ("backtest", "hyperopt", "plot") else 2160

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
        # 回测不声明 funding informative：freqtrade 会按 startup_candle_count(1h) 预载并裁剪
        # informative 数据，funding 历史 < 2160h 的新品种会被整对丢弃（2026-08-29 KAS 等 9 对
        # 静默 0 交易根因）；回测走 _funding() 的 feather 直读全史路径。
        # live/paper 必须声明：交易所 informative 拉取是 funding 数据的唯一来源。
        if self.dp.runmode.value in ("backtest", "hyperopt", "plot"):
            return []
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
