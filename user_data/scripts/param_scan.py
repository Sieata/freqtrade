"""全参数扫描 — 不同阈值 + 离场方式"""
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

def backtest_signal(df, entry_mask, stop_pct, trailing_act, roi_pct, ema_exit_col):
    """简化的回测：入场后尾随止损或回到均线离场"""
    entries = df[find_clusters(entry_mask)].index
    trades = []

    for entry_idx in entries:
        pos = df.index.get_loc(entry_idx)
        if pos + 1 >= len(df): continue
        entry_price = df['close'].iloc[pos]
        trail_active = False
        trail_high = entry_price * (1 + trailing_act)

        for i in range(pos+1, min(pos+48, len(df))):
            current = df['close'].iloc[i]
            profit = (current - entry_price) / entry_price

            # 止损
            if profit <= -stop_pct:
                trades.append({'return': -stop_pct, 'exit': 'stop'})
                break
            # 止盈
            if profit >= roi_pct:
                trades.append({'return': roi_pct, 'exit': 'roi'})
                break
            # 尾随止损
            if profit >= trailing_act:
                trail_active = True
                trail_high = max(trail_high, current)
            if trail_active and current < trail_high * (1 - 0.02):
                ret = (current - entry_price) / entry_price
                trades.append({'return': ret, 'exit': 'trail'})
                break
            # 回到均线
            if ema_exit_col and df[ema_exit_col].iloc[i] and df[ema_exit_col].iloc[i-1]:
                if current > entry_price:
                    trades.append({'return': profit, 'exit': 'ema'})
                    break
        else:
            profit = (df['close'].iloc[min(pos+47, len(df)-1)] - entry_price) / entry_price
            trades.append({'return': profit, 'exit': 'timeout'})

    return trades

# ── 扫描 ──────────────────────────────────────────────
results = []

for thresh in [7, 8, 9, 10, 11, 12]:
    for stop_pct in [0.12, 0.15, 0.18]:
        for trail_act in [0.04, 0.05, 0.06]:
            all_trades = []
            per_pair = {p: [] for p in PAIRS}

            for pair in PAIRS:
                df = load_pair_history(datadir=dl, timeframe="4h", pair=pair, data_format="feather", candle_type=CandleType.FUTURES)
                df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
                df['ret_4p'] = df['close'].pct_change(periods=4)

                crash = df['ret_4p'] < -thresh/100
                entry = crash.shift(1) & (df['close'] > df['open']) & ~crash & (df['volume'] > 0)

                # EMA 离场标记
                df['above_ema20'] = df['close'] > df['ema20']
                df['cross_ema'] = df['above_ema20'] & ~df['above_ema20'].shift(1).fillna(False)

                trades = backtest_signal(df, entry, stop_pct, trail_act, 0.25, 'cross_ema')
                all_trades.extend(trades)
                per_pair[pair] = trades

            if len(all_trades) < 10: continue

            returns = [t['return'] for t in all_trades]
            win_rate = sum(1 for r in returns if r > 0) / len(returns)
            mean_ret = np.mean(returns)
            total_ret = np.prod([1 + r for r in returns]) - 1

            # 各品种收益
            pair_rets = {}
            for p in PAIRS:
                tr = per_pair[p]
                if len(tr) > 0:
                    pair_rets[p.split('/')[0]] = np.prod([1 + t['return'] for t in tr]) - 1
                else:
                    pair_rets[p.split('/')[0]] = 0

            # 检查是否有品种大亏
            min_pair = min(pair_rets.values())
            max_pair = max(pair_rets.values())
            spread = max_pair - min_pair

            results.append({
                'thresh': thresh, 'stop': stop_pct, 'trail': trail_act,
                'n': len(all_trades), 'wr': win_rate, 'mean': mean_ret,
                'total': total_ret, 'spread': spread, 'min_pair': min_pair,
                **pair_rets
            })

# ── 排名 ──────────────────────────────────────────────
results.sort(key=lambda x: x['total'] / (x['spread'] + 0.01), reverse=True)

print(f"{'Th':>3s} {'Stop':>5s} {'Trail':>5s} {'N':>4s} {'WR':>6s} {'Total':>7s} {'Spread':>7s} {'Min':>7s} {'BTC':>7s} {'ETH':>7s} {'BNB':>7s} {'SOL':>7s} {'DOGE':>7s}")
print("-"*100)

for r in results[:20]:
    print(f"{r['thresh']:>3d}% {r['stop']:.0%} {r['trail']:.0%} {r['n']:>4d} {r['wr']:>5.1%} {r['total']:>7.1%} {r['spread']:>7.1%} {r['min_pair']:>7.1%} {r['BTC']:>7.1%} {r['ETH']:>7.1%} {r['BNB']:>7.1%} {r['SOL']:>7.1%} {r['DOGE']:>7.1%}")
