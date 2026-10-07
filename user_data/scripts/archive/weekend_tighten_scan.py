"""WeekendReverseV1 tightening scan (fixed version)

Answers:
  A. How tight can stoploss go (-10% -> ?) without breaking universality?
  B. How many more trades does lowering entry threshold give?

Fixed vs naive scan:
  - per-pair single-position constraint (max_open_trades=1) so trade count
    reflects signals swallowed during holding period.
  - additive (sum) return + mean-per-trade, NOT compounding prod.
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
PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT", "BNB/USDT:USDT",
         "ZEC/USDT:USDT", "HOME/USDT:USDT", "BANK/USDT:USDT", "CYS/USDT:USDT", "HYPE/USDT:USDT"]

MAX_HOLD = 48
ROI = 0.08

def load(pair):
    df = load_pair_history(datadir=dl, timeframe="4h", pair=pair,
                           data_format="feather", candle_type=CandleType.FUTURES)
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ret_1p"] = df["close"].pct_change(periods=1)
    tss = pd.to_datetime(df["date"])
    bj_h = (tss.dt.hour + 8) % 24
    dow = tss.dt.dayofweek
    df["wknd"] = (dow >= 5) | ((dow == 0) & (bj_h <= 21))
    df["cross_ema"] = (df["close"] > df["ema20"]) & (df["close"].shift(1) <= df["ema20"].shift(1))
    return df

DATA = {p.split("/")[0]: load(p) for p in PAIRS}

def entry_mask_for(df, thresh):
    return (
        df["wknd"]
        & (df["ret_1p"].shift(1) < -thresh)
        & (df["close"] > df["open"])
        & (df["ret_1p"] >= -thresh)
        & (df["volume"] > 0)
    )

def find_clusters(mask):
    return mask & (~mask.shift(1).fillna(False))

def backtest(df, entries, stop, trail_act, trail_step):
    """Event-driven, single position per pair."""
    close = df["close"].to_numpy()
    cross = df["cross_ema"].to_numpy()
    ent = entries.to_numpy()
    n = len(df)
    trades = []
    i = 0
    while i < n - 1:
        if ent[i]:
            entry_price = close[i]
            trail_active = False
            trail_high = entry_price
            exit_pos = min(i + MAX_HOLD, n - 1)
            ret = None
            for j in range(i + 1, min(i + MAX_HOLD, n)):
                cur = close[j]
                profit = (cur - entry_price) / entry_price
                if profit <= -stop:
                    ret, exit_pos = -stop, j; break
                if profit >= ROI:
                    ret, exit_pos = ROI, j; break
                if profit >= trail_act:
                    trail_active = True
                    trail_high = max(trail_high, cur)
                if trail_active and cur < trail_high * (1 - trail_step):
                    ret, exit_pos = profit, j; break
                if cross[j]:
                    ret, exit_pos = profit, j; break
            if ret is None:
                last = close[min(i + MAX_HOLD - 1, n - 1)]
                ret = (last - entry_price) / entry_price
                exit_pos = min(i + MAX_HOLD - 1, n - 1)
            trades.append(ret)
            i = exit_pos + 1
        else:
            i += 1
    return trades

def summarize(thresh, stop, trail_act, trail_step):
    all_ret = []
    pair_rets = {}
    for sym, df in DATA.items():
        entries = find_clusters(entry_mask_for(df, thresh))
        rets = backtest(df, entries, stop, trail_act, trail_step)
        all_ret.extend(rets)
        pair_rets[sym] = float(np.sum(rets)) if rets else 0.0
    if len(all_ret) < 10:
        return None
    arr = np.array(all_ret)
    return {
        "thresh": thresh, "stop": stop, "trail_act": trail_act, "trail_step": trail_step,
        "n": len(arr), "wr": float((arr > 0).mean()), "mean": float(arr.mean()),
        "sum": float(arr.sum()), "n_pos": sum(1 for v in pair_rets.values() if v > 0),
        "min_pair": min(pair_rets.values()), "pair_rets": pair_rets,
    }

BASELINE = dict(thresh=0.02, stop=0.10, trail_act=0.012, trail_step=0.003)

print("=" * 92)
print("Part A: threshold fixed 2%, tighten stoploss only (n stays, stop -10% -> -3%)")
print("=" * 92)
print(f"{'Stop':>6s} {'N':>5s} {'WR':>6s} {'Mean':>7s} {'Sum':>9s} {'PosPairs':>8s} {'MinPair':>8s}")
print("-" * 92)
for stop in [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10]:
    r = summarize(BASELINE["thresh"], stop, BASELINE["trail_act"], BASELINE["trail_step"])
    if r:
        tag = "  <== baseline" if stop == 0.10 else ""
        print(f"{stop:>6.0%} {r['n']:>5d} {r['wr']:>6.1%} {r['mean']:>7.2%} {r['sum']:>9.1%} {r['n_pos']:>5d}/10 {r['min_pair']:>8.1%}{tag}")

print()
print("=" * 92)
print("Part B: stoploss fixed -10%, lower threshold only (more trades, 2% -> 1%)")
print("=" * 92)
print(f"{'Thresh':>7s} {'N':>5s} {'WR':>6s} {'Mean':>7s} {'Sum':>9s} {'PosPairs':>8s} {'MinPair':>8s}")
print("-" * 92)
for thresh in [0.01, 0.015, 0.02, 0.025, 0.03]:
    r = summarize(thresh, BASELINE["stop"], BASELINE["trail_act"], BASELINE["trail_step"])
    if r:
        tag = "  <== baseline" if thresh == 0.02 else ""
        print(f"{thresh:>7.1%} {r['n']:>5d} {r['wr']:>6.1%} {r['mean']:>7.2%} {r['sum']:>9.1%} {r['n_pos']:>5d}/10 {r['min_pair']:>8.1%}{tag}")

print()
print("=" * 92)
print("Part C: full grid, ranked by (posPairs>=8, mean-per-trade)")
print("=" * 92)
results = []
for thresh in [0.01, 0.015, 0.02, 0.025, 0.03]:
    for stop in [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10]:
        for trail_act in [0.006, 0.008, 0.012, 0.02]:
            r = summarize(thresh, stop, trail_act, trail_act / 4)
            if r:
                results.append(r)

results.sort(key=lambda x: (x["n_pos"] >= 8, x["mean"]), reverse=True)
print(f"{'Thresh':>7s} {'Stop':>6s} {'Act':>5s} {'Step':>5s} {'N':>5s} {'WR':>6s} {'Mean':>7s} {'Sum':>8s} {'Pos':>4s} {'Min':>7s}")
print("-" * 100)
for r in results[:30]:
    print(f"{r['thresh']:>7.1%} {r['stop']:>6.0%} {r['trail_act']:>5.1%} {r['trail_step']:>5.2%} "
          f"{r['n']:>5d} {r['wr']:>6.1%} {r['mean']:>7.2%} {r['sum']:>8.1%} {r['n_pos']:>3d}/10 {r['min_pair']:>7.1%}")

print("\nDone.")
