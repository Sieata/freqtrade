"""
bigmove_research.py — Phase 1:BTC/ETH 1d 2022+ 大波动后的前向统计

核心假设:日线大波动后,是延续(趋势)还是反转(回归)?
  延续 → 顺着大波动方向追(趋势捕捉)
  反转 → 反着大波动方向做(均值回归)
信号:单日 ±4/6/8%、3日累计 ±8/12%(取每段起点,低频)
前向:1/3/5/10 个交易日

输出每信号:  N(次数,看频率) | 延续胜率 | 信号方向前向均值 | 最大有利偏移(可抓幅度)

用法:python user_data/scripts/bigmove_research.py
"""

import os, sys
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import pandas as pd
from freqtrade.configuration import Configuration
from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType

config = Configuration.from_files(["user_data/config_perpetual.json"])
PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
START = pd.Timestamp("2022-01-01", tz="UTC")
HORIZONS = [1, 3, 5, 10]


def find_clusters(mask):
    return mask & (~mask.shift(1).fillna(False))


def forward_stats(df, mask, direction):
    """方向 continuation='同向'。返回 [(h, n, 延续胜率, 方向均值, 最大有利偏移)]"""
    events = find_clusters(mask)
    idxs = list(df.index[events])
    out = []
    for h in HORIZONS:
        rets, mfe = [], []
        for idx in idxs:
            pos = df.index.get_loc(idx)
            if pos + h >= len(df):
                continue
            base = df["close"].iloc[pos]
            rets.append((df["close"].iloc[pos + h] - base) / base * 100)
            win = df.iloc[pos + 1: pos + h + 1]
            if direction == "down":
                mfe.append((base - win["low"].min()) / base * 100)
            else:
                mfe.append((win["high"].max() - base) / base * 100)
        if not rets:
            continue
        rets = np.array(rets)
        cont = (rets < 0).mean() * 100 if direction == "down" else (rets > 0).mean() * 100
        sig_mean = -rets.mean() if direction == "down" else rets.mean()
        out.append((h, len(rets), cont, sig_mean, float(np.mean(mfe))))
    return out


def run(pair):
    df = load_pair_history(datadir=config["datadir"], timeframe="1d", pair=pair,
                           data_format="feather", candle_type=CandleType.FUTURES)
    df = df.reset_index(drop=True)
    df = df[df["date"] >= START].reset_index(drop=True)
    df["r1"] = df["close"].pct_change()
    df["r3"] = df["close"].pct_change(3)

    signals = [
        ("单日跌-4%", df["r1"] < -0.04, "down"),
        ("单日跌-6%", df["r1"] < -0.06, "down"),
        ("单日跌-8%", df["r1"] < -0.08, "down"),
        ("单日涨+4%", df["r1"] > 0.04, "up"),
        ("单日涨+6%", df["r1"] > 0.06, "up"),
        ("单日涨+8%", df["r1"] > 0.08, "up"),
        ("3日跌-8%", df["r3"] < -0.08, "down"),
        ("3日跌-12%", df["r3"] < -0.12, "down"),
        ("3日涨+8%", df["r3"] > 0.08, "up"),
        ("3日涨+12%", df["r3"] > 0.12, "up"),
    ]

    print(f"\n{'='*110}")
    print(f"  {pair.split('/')[0]} 1d {START.date()} → {df['date'].iloc[-1].date()} ({len(df)} 根)")
    print(f"{'='*110}")
    print(f"{'信号':<14s} {'N':>4s} | {'h':>2s} {'延续胜率':>7s} {'方向均值':>8s} {'最大有利偏移':>10s} | 解读")
    for name, mask, direction in signals:
        stats = forward_stats(df, mask, direction)
        if not stats:
            print(f"{name:<14s}  样本不足")
            continue
        n_ev = int(find_clusters(mask).sum())
        for (h, n, cont, sig_mean, mfe) in stats:
            if h == 1:
                head = f"{name:<14s} {n_ev:>4d} |"
            else:
                head = f"{'':<14s} {'':>4s} |"
            verdict = ""
            if cont > 55 and sig_mean > 0:
                verdict = "延续有优势"
            elif cont < 45 and sig_mean < 0:
                verdict = "反转有优势"
            print(f"{head} {h:>2d} {cont:>6.0f}% {sig_mean:>+7.2f}% {mfe:>9.2f}%  {verdict}")


if __name__ == "__main__":
    for pair in PAIRS:
        run(pair)
    print("\n判读:延续胜率>55% 且 方向均值>0 → 大波动后顺势有优势(趋势捕捉方向);延续胜率<45% 且 方向均值<0 → 反转有优势(低吸高抛方向)。")
    print("注意:仅 BTC/ETH 两个品种,无法满足工作流 ≥4/5 普适性门槛,结论需额外谨慎(可能只是两个品种的特性)。")
