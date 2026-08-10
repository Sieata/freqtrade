"""
BigMoveV1 — 1d 大动量趋势延续(低频抓大机会)

入场:3日累计涨 >12% 且 收盘 > MA200 且 BTC(市场)> BTC MA200
出场:持有满 10 天 / 收盘跌破 MA200 / 止损 -18%
品种:8 个主流币(市值 Top10 除 BNB 自动不交易、LINK 稳定亏损,见 RESEARCH.md)

验证(2022+, 1d, freqtrade 回测):
  定版:62 笔(~13/年) / 总收益 +191.5% / 胜率 51.6% / 最大回撤 23.5%
  (演进:无过滤 86笔/+98.0%/回撤38.5% → 含市场过滤 67笔/+168.8%/27.8% → 排除LINK 62笔/+191.5%/23.5%)
  普适性 7/8 | 盲参通过 | 参数稳定(阈值8-16%×持有5-14天×MA150-250 平滑) | 逐年:2023-24 牛年赚 / 2025 亏(-14.5%)
  加品种验证:交易量榜新币(ZEC/BANK/HYPE)利润为单年 pump 集中(如 ZEC +2010% 全来自 2025),未纳入
"""

import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import IStrategy


class BigMoveV1(IStrategy):
    INTERFACE_VERSION = 3
    can_short: bool = False
    timeframe = "1d"
    startup_candle_count: int = 210          # MA200 需要 200 根 + 缓冲

    threshold = 0.12                         # 入场阈值:3 日累计涨幅
    btc_ma_window: int = 200                 # BTC 市场趋势过滤窗口

    # 宽止损封住单笔肥尾;不用 ROI 止盈,按持有期/趋势离场
    stoploss = -0.18
    trailing_stop = False
    minimal_roi = {}

    use_exit_signal = True
    process_only_new_candles = True
    # max_open_trades 由 config 控制(建议 3-8)

    order_types = {
        "entry": "limit", "exit": "limit",
        "stoploss": "market", "stoploss_on_exchange": False,
    }

    def informative_pairs(self):
        # 确保 BTC 数据被加载(市场过滤用),即使不在 whitelist
        return [("BTC/USDT:USDT", self.timeframe)]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ma200"] = ta.SMA(dataframe, timeperiod=200)
        dataframe["ret3"] = dataframe["close"].pct_change(3)
        # BTC 市场趋势过滤:风险期(市场弱)不进场
        btc = self.dp.get_pair_dataframe("BTC/USDT:USDT", self.timeframe)
        btc["btc_ma"] = ta.SMA(btc, timeperiod=self.btc_ma_window)
        btc = btc[["date", "close", "btc_ma"]].rename(columns={"close": "btc_close"})
        dataframe = dataframe.merge(btc, on="date", how="left")
        dataframe["btc_above"] = (dataframe["btc_close"] > dataframe["btc_ma"]).fillna(False)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["ret3"] > self.threshold)
            & (dataframe["close"] > dataframe["ma200"])
            & (dataframe["btc_above"])
            & (dataframe["volume"] > 0),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 趋势失效:收盘跌破 MA200
        dataframe.loc[
            (dataframe["close"] < dataframe["ma200"]) & (dataframe["volume"] > 0),
            "exit_long",
        ] = 1
        return dataframe

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        # 持有满 10 天离场
        if (current_time - trade.open_date_utc).days >= 10:
            return "hold_10d"
        return None
