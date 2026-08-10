"""
bigmove_validate.py — 大动量信号(3日涨≥12% → 持有10天)的 C 逐年滚动 + Top10 普适性

来自 bigmove_research.py 的发现:2022 年至今 1d,3日累计大涨后 5-10 日续涨。
本脚本做工作流的 C 逐年滚动 与 4.1 市值 Top10 验证。

用法:python user_data/scripts/bigmove_validate.py
"""

import os, sys
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "user_data" / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd
from freqtrade.configuration import Configuration
from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType
import bigmove_research as b

config = Configuration.from_files(["user_data/config_perpetual.json"])
TOP10 = ["BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
         "DOGE/USDT:USDT", "ADA/USDT:USDT", "AVAX/USDT:USDT", "DOT/USDT:USDT", "LINK/USDT:USDT"]
THRESH = 0.12
HOLD = 10
FEE = 0.0004


def load(pair):
    df = load_pair_history(datadir=config["datadir"], timeframe="1d", pair=pair,
                           data_format="feather", candle_type=CandleType.FUTURES)
    df = df.reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df[df["date"] >= b.START].reset_index(drop=True)
    df["r3"] = df["close"].pct_change(3)
    df["year"] = df["date"].dt.year
    return df


def backtest(df, T=THRESH, hold=HOLD, stop=0.0):
    sig = b.find_clusters(df["r3"] > T)
    trades = []
    for idx in df.index[sig]:
        pos = df.index.get_loc(idx)
        if pos + 1 + hold >= len(df):
            break
        entry = df["open"].iloc[pos + 1]
        exit_price = df["close"].iloc[pos + hold]
        if stop:
            for k in range(1, hold + 1):
                if df["low"].iloc[pos + k] <= entry * (1 - stop):
                    exit_price = entry * (1 - stop)
                    break
        trades.append((exit_price / entry - 1 - 2 * FEE) * 100)
    return np.array(trades)


def stats(trades):
    if len(trades) < 1:
        return "0笔"
    return f"{len(trades)}笔 {(trades>0).mean()*100:.0f}% {trades.sum():+7.1f}%"


if __name__ == "__main__":
    print("=" * 96)
    print("  C 逐年滚动:BTC / ETH 固定12%逐年 + 前年定参→次年验证")
    print("=" * 96)
    for pair in ["BTC/USDT:USDT", "ETH/USDT:USDT"]:
        df = load(pair)
        years = sorted(df["year"].unique())
        fixed = {y: backtest(df[df["year"] == y]) for y in years}
        print(f"\n{pair.split('/')[0]} 固定12%逐年:")
        print(f"  " + " | ".join(f"{y}: {stats(fixed[y])}" for y in years))
        print(f"  滚动(前N年定参→次年):")
        for T in years[1:]:
            train = df[df["year"] < T]
            test = df[df["year"] == T]
            best_t, best_m = THRESH, -1e9
            for cand in [0.08, 0.10, 0.12, 0.14, 0.16]:
                t = backtest(train, T=cand)
                if len(t) >= 3 and t.mean() > best_m:
                    best_m, best_t = t.mean(), cand
            tt = backtest(test, T=best_t)
            chosen = f"定参{best_t*100:.0f}%" if best_t else "样本不足"
            print(f"    →{T}年: {chosen} → {stats(tt)}")

    print("\n" + "=" * 96)
    print("  普适性:市值 Top10 固定12%,2022 年至今")
    print("=" * 96)
    n_win = 0
    for pair in TOP10:
        t = backtest(load(pair))
        s = stats(t)
        win = "✅" if len(t) and t.sum() > 0 else "❌"
        if len(t) and t.sum() > 0:
            n_win += 1
        # 含 2022 排除版
        df = load(pair)
        t_no22 = backtest(df[df["year"] >= 2023])
        print(f"  {pair.split('/')[0]:>5s}: 全期 {s} {win}  | 2023+(排除2022) {stats(t_no22)}")
    print(f"\nTop10 盈利品种: {n_win}/10  (工作流门槛 ≥8/10)")
