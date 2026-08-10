"""
bigmove_stability.py — 大动量信号的 A 盲参 + B 参数稳定性检测

信号:3日累计涨 > T% → 次日开盘进场,持有 hold 天,价格须在 MA(ma_win)上方。
主参数(已调):T=12%, hold=10, ma_win=200
A 盲参:用完全不相关的参数组(20%/7d/MA150、8%/14d/MA250),5+ 品种盈利为过
B 参数稳定:T∈[8,16%] × hold∈[5,14] 与 ma_win∈[150,250] 全扫描,全部参数点盈利、无尖峰

用法:python user_data/scripts/bigmove_stability.py
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
import bigmove_validate as v
import bigmove_research as b

FEE = v.FEE


def backtest_params(df, T=0.12, hold=10, ma_win=200, use_ma=True):
    df = df.copy()
    if use_ma:
        df["ma"] = df["close"].rolling(ma_win).mean()
    sig = b.find_clusters(df["r3"] > T)
    trades = []
    for idx in df.index[sig]:
        pos = df.index.get_loc(idx)
        if pos + 1 + hold >= len(df):
            break
        if use_ma and (np.isnan(df["ma"].iloc[pos]) or df["close"].iloc[pos] <= df["ma"].iloc[pos]):
            continue
        entry = df["open"].iloc[pos + 1]
        exit_p = df["close"].iloc[pos + hold]
        trades.append((exit_p / entry - 1 - 2 * FEE) * 100)
    return np.array(trades)


def profitable_count(T, hold, ma_win):
    """返回 (盈利品种数, 排除BNB/XRP后的盈利数, 每品种净收益dict)"""
    nets = {}
    for pair in v.TOP10:
        df = v.load(pair)
        t = backtest_params(df, T, hold, ma_win)
        nets[pair.split("/")[0]] = t.sum() if len(t) else 0.0
    full = sum(1 for x in nets.values() if x > 0)
    excl = sum(1 for k, x in nets.items() if x > 0 and k not in ("BNB", "XRP"))
    return full, excl, nets


if __name__ == "__main__":
    # ── A 盲参 ──
    print("A 盲参:不相关参数组")
    for T, hold, ma in [(0.20, 7, 150), (0.08, 14, 250), (0.15, 5, 100)]:
        full, excl, nets = profitable_count(T, hold, ma)
        fmt = " ".join(f"{k}:{x:+.0f}" for k, x in nets.items())
        print(f"  参{T*100:.0f}%/持{hold}d/MA{ma}: 盈利 {full}/10 (排除BNB/XRP {excl}/8) | {fmt}")
    print("  通过标准:≥5 品种盈利 → 全部通过" if all(profitable_count(T, h, m)[0] >= 5 for T, h, m in [(0.20, 7, 150), (0.08, 14, 250), (0.15, 5, 100)]) else "")

    # ── B1 阈值 × 持有期 (MA200) ──
    print("\nB1 参数稳定:阈值 × 持有期 (MA200), 格值=盈利品种数/8(排除BNB/XRP)")
    print(f"{'T\\hold':>7s} " + "".join(f"{h:>6d}d" for h in [5, 7, 10, 14]))
    grid = {}
    for T in [0.08, 0.10, 0.12, 0.14, 0.16]:
        row = f"{T*100:>6.0f}%"
        for h in [5, 7, 10, 14]:
            full, excl, nets = profitable_count(T, h, 200)
            grid[(T, h)] = (full, excl, nets)
            row += f" {excl:>4d}/8"
        print(row)
    n_bad = sum(1 for (full, excl, nets) in grid.values() if excl < 6)
    print(f"  排除BNB/XRP后盈利<6/8 的参数点数: {n_bad}/{len(grid)}")

    # ── B2 MA窗口 × 持有期 (T=12%) ──
    print("\nB2 参数稳定:MA窗口 × 持有期 (T=12%), 格值=盈利品种数/8")
    print(f"{'MA\\hold':>7s} " + "".join(f"{h:>6d}d" for h in [5, 7, 10, 14]))
    for ma in [150, 180, 200, 220, 250]:
        row = f"{ma:>6d}"
        for h in [5, 7, 10, 14]:
            full, excl, nets = profitable_count(0.12, h, ma)
            row += f" {excl:>4d}/8"
        print(row)

    # 主参数点的尖峰检查:邻域波动
    print("\n尖峰检查:主参数(12%/10d/MA200)与相邻点的净收益差异")
    base = profitable_count(0.12, 10, 200)[2]
    for T, h, ma in [(0.10, 10, 200), (0.14, 10, 200), (0.12, 7, 200), (0.12, 14, 200), (0.12, 10, 180), (0.12, 10, 220)]:
        nets = profitable_count(T, h, ma)[2]
        diff = {k: nets[k] - base[k] for k in nets}
        print(f"  {T*100:.0f}%/{h}d/MA{ma} vs 主参数: " + " ".join(f"{k}:{d:+.0f}" for k, d in diff.items()))
