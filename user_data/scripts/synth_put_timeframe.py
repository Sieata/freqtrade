"""
synth_put_timeframe.py — 跨周期对比:同一套暴跌信号在 1h / 4h / 1d 上的表现

4h 的结论(S4/S11 全期利润 2021,样本外 2022-2026 全负)是否在别的周期成立?
关键:所有参数按真实时间归一化,否则跨周期不可比。
  - 信号窗口:S4 = 24h 内跌 >6%;S11 = 前 8h 资金费>0.03% 且 8h 内跌 >2%
  - 开仓等待 patience = 48h 的 K 数;裸空时间止损 max_hold = 192h 的 K 数
  - 信号语义不变,只有 K 粒度变

用法:python user_data/scripts/synth_put_timeframe.py
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
import synth_put_backtest as sb
import synth_put_timed as st

config = Configuration.from_files(["user_data/config_perpetual.json"])
PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT", "SOL/USDT:USDT", "DOGE/USDT:USDT"]
TFS = ["1h", "4h", "1d"]
TF_HOURS = {"1h": 1, "4h": 4, "1d": 24}
F_THRESH = 0.0003
DROP4 = 0.06          # S4:24h 跌超 6%
DROP11 = 0.02         # S11:8h 跌超 2%


def find_clusters(mask):
    return mask & (~mask.shift(1).fillna(False))


def load_pair_tf(pair, tf):
    df = load_pair_history(datadir=config["datadir"], timeframe=tf, pair=pair,
                           data_format="feather", candle_type=CandleType.FUTURES)
    df = df.reset_index(drop=True)
    funding = sb.load_funding(pair)
    if funding is not None:
        merged = funding.reindex(funding.index.union(df["date"])).ffill().reindex(df["date"])
        df["funding"] = merged.values
    else:
        df["funding"] = 0.0
    return df


def run(pair, tf, df):
    th = TF_HOURS[tf]
    # 按真实时间归一化模拟参数(48h 等待 / 192h 裸空止损 / 24h·8h 信号窗)
    patience = max(1, round(48 / th))
    max_hold = max(1, round(192 / th))
    win24 = max(1, round(24 / th))
    win8 = max(1, round(8 / th))

    st.PATIENCE = patience
    sb.MAX_HOLD = max_hold
    st.MAX_HOLD = max_hold

    df = df.copy()
    df["ret24"] = df["close"].pct_change(periods=win24)
    df["ret8"] = df["close"].pct_change(periods=win8)
    df["year"] = df["date"].dt.year

    has_funding = bool((df["funding"] != 0).any())
    s4 = find_clusters(df["ret24"] < -DROP4)
    s11 = find_clusters((df["funding"].shift(1) > F_THRESH) & (df["ret8"] < -DROP11))

    out = {}
    for name, mask in [("S4", s4), ("S11", s11)]:
        r = st.simulate_timed(pair, df, mask, patience=patience, use_funding=has_funding)
        # 样本外 2022-2026
        sub = df[df["year"] >= 2022].reset_index(drop=True)
        sm = pd.Series(mask.values[df["year"].values >= 2022])
        ro = st.simulate_timed(pair, sub, sm, patience=patience, use_funding=has_funding)
        out[name] = (r, ro)
    return out


if __name__ == "__main__":
    print("跨周期对比 | 全期净盈亏 / 兑现率 | [样本外2022-2026净盈亏]")
    print("参数归一化:等待48h / 裸空止损192h / S4=24h跌6% / S11=前8h高费+8h跌2%")
    print()
    for tf in TFS:
        print(f"\n{'='*100}\n  周期 {tf}  (1 根 = {TF_HOURS[tf]}h)\n{'='*100}")
        print(f"  {'':>5s} | {'S4 全期':>16s} {'S4 兑现率':>8s} {'S4 OOS':>10s} | {'S11 全期':>16s} {'S11 兑现率':>8s} {'S11 OOS':>10s}")
        for pair in PAIRS:
            df = load_pair_tf(pair, tf)
            res = run(pair, tf, df)
            row = f"  {pair.split('/')[0]:>5s} |"
            for name in ["S4", "S11"]:
                r, ro = res[name]
                row += f" {r['realized']:>+10.0f} ({r['crash_rate']:>3.0f}%) {ro['realized']:>+9.0f} |"
            print(row)
    print("\n解读:若 1h/1d 的样本外(OOS)也不为正,说明暴跌可预测性退化是跨周期现象,换周期救不了。")
