"""
通用策略研究 — 同一套参数跑 BTC/ETH/BNB/SOL/DOGE
"""

import os, sys
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))

import pandas as pd
import numpy as np
from freqtrade.configuration import Configuration
from freqtrade.data.history import load_pair_history
from freqtrade.enums import CandleType

config = Configuration.from_files(["user_data/config_perpetual.json"])
dl = config["datadir"]
PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT", "SOL/USDT:USDT", "DOGE/USDT:USDT"]

def calc_rsi(s, p=14):
    d = s.diff(); g = d.clip(lower=0); l = (-d).clip(lower=0)
    ag = g.ewm(alpha=1/p, adjust=False).mean(); al = l.ewm(alpha=1/p, adjust=False).mean()
    return 100 - (100/(1 + ag/al))

def find_clusters(mask):
    return mask & (~mask.shift(1).fillna(False))

def test_signal(name, df, entry_mask, horizons, direction='long'):
    """测试一个信号：返回胜率和平均收益"""
    events = find_clusters(entry_mask)
    if events.sum() < 5:
        return {"name": name, "n": events.sum(), "win": None, "mean": None}

    idxs = df[events].index
    results = []
    for h in horizons:
        vals = []
        for idx in idxs:
            pos = df.index.get_loc(idx)
            if pos + h < len(df):
                ret = (df['close'].iloc[pos+h] - df['close'].iloc[pos]) / df['close'].iloc[pos] * 100
                if direction == 'short': ret = -ret
                vals.append(ret)
        if vals:
            wr = (pd.Series(vals) > 0).mean() * 100
            results.append((h, len(vals), wr, np.mean(vals)))
    return {"name": name, "n": events.sum(), "results": results}


# ═══════════════════════════════════════════════════
# 对每个标的跑同一套信号
# ═══════════════════════════════════════════════════

SIGNALS = {
    "RSI<30做多":    lambda df: df['rsi'] < 30,
    "RSI>70做空":    lambda df: df['rsi'] > 70,
    "EMA金叉做多":   lambda df: (df['ema20'] > df['ema50']) & (df['ema20'].shift(1) <= df['ema50'].shift(1)),
    "EMA死叉做空":   lambda df: (df['ema20'] < df['ema50']) & (df['ema20'].shift(1) >= df['ema50'].shift(1)),
    "BB下轨做多":    lambda df: df['close'] < df['bb_lower'],
    "BB上轨做空":    lambda df: df['close'] > df['bb_upper'],
    "放量>2x做多":   lambda df: (df['vol_ratio'] > 2.0) & (df['close'] > df['open']),
    "暴跌4p>5%抄底": lambda df: df['ret_4p'] < -0.05,
    "暴涨4p>5%做空": lambda df: df['ret_4p'] > 0.05,
    "暴跌4p>10%抄底": lambda df: df['ret_4p'] < -0.10,
    "暴涨4p>10%做空": lambda df: df['ret_4p'] > 0.10,
}

for pair in PAIRS:
    df = load_pair_history(datadir=dl, timeframe="4h", pair=pair, data_format="feather", candle_type=CandleType.FUTURES)
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['rsi'] = calc_rsi(df['close'])
    df['atr'] = (df['high'] - df['low']).rolling(14).mean()
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']
    df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
    df['vol_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
    df['ret_4p'] = df['close'].pct_change(periods=4)

    print(f"\n{'='*50}")
    print(f"  {pair.split('/')[0]:6s} | {len(df)}c | ${df['close'].min():.0f}~${df['close'].max():.0f} | ATR {df['atr'].mean()/df['close'].mean()*100:.1f}%")
    print(f"{'='*50}")
    print(f"{'信号':<20s} {'N':>4s} {'8c胜率':>7s} {'8c均值':>7s} {'12c胜率':>7s} {'12c均值':>7s} {'24c胜率':>7s} {'24c均值':>7s}")
    print("-"*75)

    for name, mask_fn in SIGNALS.items():
        direction = 'short' if '做空' in name else 'long'
        mask = mask_fn(df)
        r = test_signal(name, df, mask, [8, 12, 24], direction)
        if r['n'] < 5:
            print(f"{name:<20s} {r['n']:>4d}  (样本不足)")
            continue
        for h, n, wr, mn in r['results']:
            pass  # just print the last pass
        parts = [f"{name:<20s} {r['n']:>4d}"]
        for h, n, wr, mn in r['results']:
            parts.append(f"{wr:>6.0f}% {mn:>6.2f}%")
        print("  ".join(parts))

print("\nDone. 搜索所有标的上胜率 >55% 且均值 >0 的信号。")
