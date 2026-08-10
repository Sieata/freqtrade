"""
synth_put_precrash.py — 盘前信号择时:能不能在暴跌前开仓

上一步(S4 暴跌延续)在暴跌开始后 1-4 根K才进场,且利润集中 2021。
本脚本测"暴跌前/暴跌刚起"的信号,用上了没测过的 mark 基差数据:

  S6  正基差(期货-标记 > 阈值)= 多头拥挤,杠杆蓄积
  S7  资金费持续为正 N 次 = 杠杆持续蓄积
  S8  周末 = 流动性低窗口(WeekendReverseV1 证明周末有独立微观结构)
  S9  拥挤组合(基差+资金费+乖离全高)
  S10 拥挤刚跌破:基差高 且 2根K跌 >2%(去杠杆级联的起点) ← 用户要的"暴跌前"
  对照:S4 暴跌延续(6% 延续)、基线

每个信号 × patience(12/24),报兑现率与每手 EV。
判据:兑现率是否显著高于基线且 EV>0。
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
from synth_put_backtest import N, L_L, L_S, TP_DROP, MAX_HOLD, FEE, LIQ_SLIPPAGE, MAINT, CAPITAL, load_pair
from synth_put_timed import simulate_timed, find_clusters

BASE_EVERY = 30


def load_mark_basis(pair):
    """1h 标记价重采样到 4h,算基差 = 期货/标记 - 1"""
    name = pair.replace("/", "_").replace(":", "_")
    path = Path(project_root) / "user_data/data/binance/futures" / f"{name}-1h-mark.feather"
    if not path.exists():
        return None
    m = pd.read_feather(path)[["date", "close"]]
    m = m.set_index("date").resample("4h", closed="left", label="left").last()
    m = m.reset_index()
    return m


def build(df, pair):
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
    df["atr50"] = df["atr"].rolling(50).mean()
    df["dev_atr"] = (df["close"] - df["ema50"]) / df["atr"]
    df["ret_2p"] = df["close"].pct_change(periods=2)
    df["ret_4p"] = df["close"].pct_change(periods=4)
    df["wd"] = df["date"].dt.dayofweek
    m = load_mark_basis(pair)
    if m is not None:
        m = m.rename(columns={"close": "mark"})
        df = df.merge(m, on="date", how="left")
        df["basis"] = (df["close"] / df["mark"] - 1) * 100
    else:
        df["basis"] = 0.0
    # 资金费持续为正的段数
    pos = df["funding"] > 0
    streak = pos.groupby((~pos).cumsum()).cumsum()
    df["fund_streak"] = streak
    return df


def main():
    for pair in PAIRS:
        df = build(load_pair(pair), pair)
        has_funding = bool((df["funding"] != 0).any())
        n = len(df)

        base = pd.Series((np.arange(n) % BASE_EVERY == 0), index=df.index)
        s4 = find_clusters(df["ret_4p"] < -0.06)
        s6a = find_clusters(df["basis"] > 0.5)
        s6b = find_clusters(df["basis"] > 1.0)
        s7 = find_clusters((df["fund_streak"] >= 4) & (df["funding"] > 0.0002))
        s8 = find_clusters(df["wd"] >= 5)
        s9 = find_clusters((df["basis"] > 0.5) & (df["funding"] > 0.0003) & (df["dev_atr"] > 1.5))
        s10 = find_clusters((df["ret_2p"] < -0.02) & (df["basis"].shift(1) > 0.3))
        s11 = find_clusters((df["ret_2p"] < -0.02) & (df["funding"].shift(1) > 0.0003))

        sigs = {
            "基线": base,
            "S4 暴跌延续": s4,
            "S6 正基差>0.5%": s6a,
            "S6 正基差>1%": s6b,
            "S7 资金费持续": s7,
            "S8 周末": s8,
            "S9 拥挤组合": s9,
            "S10 拥挤刚跌破": s10,
            "S11 高费刚跌破": s11,
        }
        print(f"\n{'='*100}\n  {pair.split('/')[0]:5s} | {df['date'].iloc[0]:%Y-%m-%d} → {df['date'].iloc[-1]:%Y-%m-%d} | 基线兑现率参考\n{'='*100}")
        print(f"  {'信号':<16s} {'pat':>3s} {'开仓':>5s} {'兑现':>5s} {'兑现率':>6s} {'弹回':>5s} {'平仓':>5s} {'净盈亏':>9s} {'/手':>7s}")
        for name, mask in sigs.items():
            if name == "基线":
                continue
            for P in (12, 24):
                r = simulate_timed(pair, df, mask, patience=P, use_funding=has_funding)
                if r["n_enter"] < 5:
                    print(f"  {name:<16s} {P:>3d} {r['n_enter']:>5d}  样本不足")
                    continue
                ev = r["realized"] / r["n_enter"]
                mark = "  <--" if (r["crash_rate"] > 3 and ev > 0) else ""
                print(f"  {name:<16s} {P:>3d} {r['n_enter']:>5d} {r['n_crash']:>5d} {r['crash_rate']:>5.0f}% {r['n_bounce']:>5d} {r['n_stand']:>5d} {r['realized']:>+9.0f} {ev:>+6.1f}U{mark}")


if __name__ == "__main__":
    from synth_put_backtest import PAIRS
    main()
