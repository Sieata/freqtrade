"""跨所股票永续资金费套利研究（H10）：币安 vs Bybit/Bitget/OKX。

结构：同一股票在两所的永续，空高费率所 + 多低费率所 = delta 中性收费率差（无需现货腿）。
输出：每所每符号 funding APR 快照、跨所费率差（套利毛收益）、跨所价格基差（主要风险）、
      30 天费率历史（持续性检验）。
用法: .venv/bin/python user_data/scripts/cross_venue_funding.py
"""
import ccxt
import numpy as np
import pandas as pd

EQUITY = {"NVDA", "TSLA", "AAPL", "MSTR", "GOOGL", "META", "AMZN", "MSFT", "SPY", "QQQ",
          "PLTR", "COIN", "HOOD", "AMD", "INTC", "CRCL", "GME", "UBER", "BABA", "NFLX",
          "TSM", "SOFI", "OPENAI", "ANTHROPIC", "SPCX"}
VENUES = ["binanceusdm", "bybit", "bitget", "okx"]
PROXY = {"https_proxy": "http://127.0.0.1:7897", "http_proxy": "http://127.0.0.1:7897"}


def load_venue(name):
    ex = getattr(ccxt, name)({"enableRateLimit": True, "proxies": {
        "http": PROXY["http_proxy"], "https": PROXY["https_proxy"]}})
    mkts = ex.load_markets()
    hits = {m["base"]: m["symbol"] for m in mkts.values()
            if m.get("swap") and m.get("active") and m["base"] in EQUITY}
    return ex, hits


def main():
    """历史 realized 口径（30 天）：快照 predicted-rate 全 0 不可用，价格跨所口径不一致待归一。"""
    import time
    syms = ["NVDA/USDT:USDT", "MSTR/USDT:USDT", "HOOD/USDT:USDT", "TSLA/USDT:USDT", "SPY/USDT:USDT"]
    since = int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)).timestamp() * 1000)

    def venue_hist(ex_name):
        try:
            ex = getattr(ccxt, ex_name)({"enableRateLimit": True, "proxies": PROXY})
            ex.load_markets()
        except Exception as e:
            print(f"{ex_name}: 加载失败 {str(e)[:60]}")
            return {}
        out = {}
        for sym in syms:
            try:
                hist = ex.fetch_funding_rate_history(sym, since=since, limit=200)
                if not hist:
                    continue
                df = pd.DataFrame([{"t": pd.to_datetime(h["timestamp"], unit="ms", utc=True),
                                    "rate": float(h["fundingRate"])} for h in hist]).sort_values("t")
                interval_h = df["t"].diff().dt.total_seconds().median() / 3600
                apr = df["rate"].mean() * (24 / interval_h) * 365 * 100
                out[sym] = {"apr": apr, "n": len(df), "interval_h": interval_h}
            except Exception as e:
                out[sym] = {"err": str(e)[:50]}
        return out

    results = {}
    for v in VENUES:
        results[v] = venue_hist(v)

    print("
30 天 realized funding APR（按各自结算间隔年化）:")
    print(f"{'symbol':<20}" + "".join(f"{v:>14}" for v in results))
    for sym in syms:
        line = f"{sym:<20}"
        for v in results:
            r = results[v].get(sym, {})
            if "apr" in r:
                line += f"{r['apr']:>+9.1f}%({r['n']:>3})"
            elif "err" in r:
                line += f"{'ERR':>14}"
            else:
                line += f"{'—':>14}"
        print(line)
    print("
未决风险：跨所价格基差未量化（各所合约规格/预言机不同，快照价差 100% 为口径错位），")
    print("需逐所归一合约规格后才能评估双腿的保证金/基差风险。")


if __name__ == "__main__":
    main()
