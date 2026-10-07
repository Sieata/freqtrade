"""扫描做空信号 — 涨超阈值后做空在所有5个标的上是否有效"""
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

def find_clusters(mask):
    return mask & (~mask.shift(1).fillna(False))

def test_signal(df, entry_mask, horizons):
    events = find_clusters(entry_mask)
    if events.sum() < 3: return None
    idxs = df[events].index
    results = []
    for h in horizons:
        vals = []
        for idx in idxs:
            pos = df.index.get_loc(idx)
            if pos + h < len(df):
                ret = (df['close'].iloc[pos+h] - df['close'].iloc[pos]) / df['close'].iloc[pos] * 100
                # 做空：价格下跌 = 赚钱
                vals.append(-ret)
        if vals:
            wr = (pd.Series(vals) > 0).mean() * 100
            results.append((h, len(vals), wr, np.mean(vals)))
    return {"n": events.sum(), "results": results}

print(f"{'Thr':>5s} {'BTC':>18s} {'ETH':>18s} {'BNB':>18s} {'SOL':>18s} {'DOGE':>18s}")
print("-"*100)

for thresh in [8, 10, 12, 15]:
    row = f"{thresh:>3d}% "
    for pair in PAIRS:
        df = load_pair_history(datadir=dl, timeframe="4h", pair=pair, data_format="feather", candle_type=CandleType.FUTURES)
        df['ret_4p'] = df['close'].pct_change(periods=4)
        # 涨超阈值 + 出现阴线
        pumped = df['ret_4p'].shift(1) > thresh/100
        bearish = df['close'] < df['open']
        not_pumped_now = df['ret_4p'] <= thresh/100
        entry = pumped & bearish & not_pumped_now & (df['volume'] > 0)
        r = test_signal(df, entry, [8])
        if r and r['n'] >= 3:
            wr = r['results'][0][2]
            mn = r['results'][0][3]
            row += f"{r['n']:>3d}笔 {wr:.0f}% {mn:+.1f}%   "
        else:
            row += f"{'--':>18s}"
    print(row)
