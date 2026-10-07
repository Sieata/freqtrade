"""
synth_put_timed.py — 合成看跌期权"开仓时机优化"回测

always-on 版(synth_put_backtest.py)已证伪:93% 的爆仓周期弹回,每次交
维持差+滑点+费,五品种账户一年内归零。

本脚本测"择时开仓":只在信号触发时开双腿结构,patience 根内没触发强平
就平仓等下次。择时版把"开错"的代价压到很低:
  开错(未暴跌):      对冲态净值≈0,只付 4 单手续费 ≈ 16U    → 便宜
  开错(跌4.6%弹回):  权利金+维持差+滑点+费 ≈ -105U        → 弹回陷阱
  开对(跌4.6%续走):  兑现 ≈ +850U/次

关键问题:信号能否把"兑现率"抬到盈亏平衡线以上?
实验设计 = 信号择时 vs 无信号基线(每 N 根开一次),比兑现率与净盈亏。

信号(复用 fragile_short_research):
  S1 资金费 > 0.05%/8h (仅 BTC/ETH/SOL 有数据)
  S2 乖离 EMA50 > 2.0·ATR (五品种)
  S3 S1 且 S2

用法:python user_data/scripts/synth_put_timed.py
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
from synth_put_backtest import (N, L_L, L_S, TP_DROP, MAX_HOLD, FEE,
                                LIQ_SLIPPAGE, MAINT, CAPITAL, load_pair)

PATIENCE = 12          # 开仓后等待根数(2 天),未强平则平仓等下次
BASE_EVERY = 30        # 基线:每 30 根(≈5 天)开一次
F_THRESH = 0.0005      # 资金费阈值 0.05%/8h
DEV_K = 2.0            # 乖离倍数

liq_move_long = 1 / L_L - MAINT
liq_move_short = 1 / L_S - MAINT


def find_clusters(mask):
    return mask & (~mask.shift(1).fillna(False))


def simulate_timed(pair, df, entry_mask, patience=PATIENCE, use_funding=True):
    n = len(df)
    dates = df["date"].values
    highs, lows, closes = df["high"].values, df["low"].values, df["close"].values
    fr = df["funding"].values if use_funding else np.zeros(n)
    mask = entry_mask.values if hasattr(entry_mask, "values") else np.asarray(entry_mask)

    realized = 0.0
    entry_p = 0.0
    state = "WAIT"
    armed = True
    ep_start = 0
    naked_start = 0
    c_longliq = c_short = c_fee = c_funding = 0.0
    n_enter = n_liq = n_crash = n_bounce = n_time = n_stand = 0
    cycle_nets = []

    def pay_fee(o):
        nonlocal realized, c_fee
        f = FEE * N * o
        realized -= f
        c_fee += f

    def open_legs(p):
        nonlocal entry_p, state
        entry_p = p
        state = "OPEN"
        pay_fee(2)

    for i in range(n):
        h, l, c = highs[i], lows[i], closes[i]

        if state == "WAIT":
            if not mask[i]:
                armed = True
            elif armed and mask[i]:
                open_legs(c)
                ep_start = i
                n_enter += 1
                armed = False

        elif state == "OPEN":
            liq_p = entry_p * (1 - liq_move_long)
            if l <= liq_p:
                prem = N / L_L
                slip = LIQ_SLIPPAGE * N
                realized -= prem + slip
                c_longliq += -(prem + slip)
                pay_fee(1)
                n_liq += 1
                naked_start = i
                state = "NAKED"
            elif i - ep_start >= patience:
                pay_fee(2)          # 平多 + 平空(净值≈0)
                n_stand += 1
                state = "WAIT"

        elif state == "NAKED":
            f = fr[i] * 0.5 * N
            realized += f
            c_funding += f
            strike_p = entry_p * (1 - liq_move_long)
            target_p = strike_p * (1 - TP_DROP)
            short_liq_p = entry_p * (1 + liq_move_short)

            if l <= target_p:
                pnl = N * (entry_p - target_p) / entry_p
                realized += pnl
                c_short += pnl
                pay_fee(1)
                n_crash += 1
                cycle_nets.append(("crash", realized))
                state = "WAIT"
            elif h >= short_liq_p:
                loss = N / L_S
                realized -= loss
                c_short -= loss
                cycle_nets.append(("short_liq", realized))
                state = "BLOWN"
                break
            elif h >= strike_p:
                pnl = N * (entry_p - strike_p) / entry_p
                realized += pnl
                c_short += pnl
                pay_fee(1)
                n_bounce += 1
                cycle_nets.append(("bounce", realized))
                state = "WAIT"
            elif i - naked_start >= MAX_HOLD:
                pnl = N * (entry_p - c) / entry_p
                realized += pnl
                c_short += pnl
                pay_fee(1)
                n_time += 1
                cycle_nets.append(("time", realized))
                state = "WAIT"

    # 收尾:仍持仓按现价平
    if state == "OPEN":
        pay_fee(2)
        n_stand += 1
    elif state == "NAKED":
        pnl = N * (entry_p - closes[-1]) / entry_p
        realized += pnl
        c_short += pnl
        pay_fee(1)

    assert abs(c_longliq + c_short - c_fee + c_funding - realized) < 0.01
    n_attempts = n_enter
    crash_rate = n_crash / n_attempts * 100 if n_attempts else 0.0
    ev_per = realized / n_attempts if n_attempts else 0.0
    return {
        "pair": pair, "n_enter": n_enter, "n_liq": n_liq,
        "n_crash": n_crash, "n_bounce": n_bounce, "n_time": n_time, "n_stand": n_stand,
        "crash_rate": crash_rate, "realized": realized, "ev_per": ev_per,
        "c_longliq": c_longliq, "c_short": c_short, "c_fee": c_fee, "c_funding": c_funding,
        "state_end": state,
    }


def run_signal(name, pair, df, mask, has_funding):
    r = simulate_timed(pair, df, mask, use_funding=has_funding)
    if r["n_enter"] < 5:
        return f"{name:<10s} {r['n_enter']:>4d}次  样本不足"
    return (f"{name:<10s} {r['n_enter']:>4d}次 | 兑现{r['n_crash']:>3d}({r['crash_rate']:>4.0f}%) "
            f"弹回{r['n_bounce']:>3d} 平仓{r['n_stand']:>3d} | 净盈亏{r['realized']:>+8.0f} ({r['ev_per']:>+6.1f}U/手)")


if __name__ == "__main__":
    from synth_put_backtest import PAIRS
    print(f"择时回测:patience={PATIENCE} 根(2天) | 基线每 {BASE_EVERY} 根开一次 | 开错≈16U | 兑现≈+850U | 弹回≈-105U")
    print(f"盈亏平衡兑现率 ≈ {105/(850+105)*100:.0f}% (若弹回陷阱比例高则更高)")
    print()
    for pair in PAIRS:
        df = load_pair(pair)
        has_funding = bool((df["funding"] != 0).any())
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
        df["atr50"] = df["atr"].rolling(50).mean()
        df["dev_atr"] = (df["close"] - df["ema50"]) / df["atr"]
        df["ret_4p"] = df["close"].pct_change(periods=4)

        base_mask = pd.Series((np.arange(len(df)) % BASE_EVERY == 0), index=df.index)
        dev_mask = find_clusters(df["dev_atr"] > DEV_K)
        crash_mask = find_clusters(df["ret_4p"] < -0.06)          # S4 暴跌延续
        vol_mask = find_clusters(df["atr"] / df["atr50"] > 1.5)   # S5 波动率抬升
        lines = [f"  {pair.split('/')[0]:5s} | " + run_signal("基线", pair, df, base_mask, has_funding)]
        lines.append(f"        | " + run_signal("乖离S2", pair, df, dev_mask, has_funding))
        lines.append(f"        | " + run_signal("暴跌延续S4", pair, df, crash_mask, has_funding))
        lines.append(f"        | " + run_signal("波动抬升S5", pair, df, vol_mask, has_funding))
        if has_funding:
            fund_mask = find_clusters(df["funding"] > F_THRESH)
            comb_mask = find_clusters((df["funding"] > F_THRESH) & (df["dev_atr"] > DEV_K))
            lines.append(f"        | " + run_signal("资金费S1", pair, df, fund_mask, has_funding))
            lines.append(f"        | " + run_signal("组合S3", pair, df, comb_mask, has_funding))
        else:
            lines.append("        | 资金费S1/组合S3 (无 funding 数据)")
        print("\n".join(lines))
        print()

    print("解读:若各信号的兑现率≈基线、每手 EV 为负,说明信号不预测暴跌,择时救不了 always-on。")
