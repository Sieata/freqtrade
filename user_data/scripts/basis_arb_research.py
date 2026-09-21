#!/usr/bin/env python
"""单所套利研究台（币安内部，跨工具口径）。

模式:
  1) spotperp  期现资金费套利：多现货 N + 空永续 N，delta 中性，吃永续资金费。
               收益 = N·(现货收益 − 永续收益) + N·资金费 − 双边手续费。
  2) calendar  交割基差套利：多现货 N + 空季度 N，到期自动收敛（F_T = S_T）。
               建仓即锁定收益 = F_0 − S_0，年化 = (F_0/S_0 − 1)·365/剩余天数。

口径说明:
  - spotperp 报两个分母：per 1x 名义（gross notional）与 per 占用资本（现货全额 + 永续保证金）。
  - calendar 到期收益与名义无关，按"每 1 现货名义"计。
  - 手续费默认：现货 taker 0.10%、永续 taker 0.05%；建仓+平仓各一次（buy&hold 不滚动）。

用法:
  .venv/Scripts/python.exe user_data/scripts/basis_arb_research.py --mode both
  ... --mode spotperp --coins BTC ETH SOL --fee-spot 0.001 --fee-perp 0.0005
  ... --mode calendar --thresh 0.05 --horizon 30
"""
import argparse
import glob
import os
import re

import numpy as np
import pandas as pd

FUT = "user_data/data/binance/futures"
SPOT = "user_data/data/binance"
QUART = "user_data/data/binance/quarterly"
BARS_PER_YEAR = 365 * 6


def load_spot(coin, tf="4h"):
    f = f"{SPOT}/{coin}_USDT-{tf}.feather"
    if not os.path.exists(f):
        return None
    df = pd.read_feather(f)[["date", "close"]]
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.set_index("date")["close"]


def load_perp(coin, tf="4h"):
    f = f"{FUT}/{coin}_USDT_USDT-{tf}-futures.feather"
    if not os.path.exists(f):
        return None
    df = pd.read_feather(f)[["date", "close"]]
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.set_index("date")["close"]


def load_funding(coin):
    f = f"{FUT}/{coin}_USDT_USDT-1h-funding_rate.feather"
    if not os.path.exists(f):
        return None
    df = pd.read_feather(f)[["date", "open"]].rename(columns={"open": "rate"})
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.set_index("date")["rate"].resample("4h").sum(min_count=1)


def metrics(ret, label):
    if ret is None or len(ret) == 0 or ret.std() == 0:
        return {"label": label, "bars": 0}
    eq = ret.cumsum()
    return {"label": label, "bars": len(ret), "net%": ret.sum() * 100,
            "ann%": ret.mean() * BARS_PER_YEAR * 100,
            "sharpe": ret.mean() / ret.std() * np.sqrt(BARS_PER_YEAR),
            "maxDD%": (eq - eq.cummax()).min() * 100,
            "yearly": " ".join(f"{y}:{v * 100:+.1f}%" for y, v in ret.groupby(ret.index.year).sum().items())}


def show(m, extra=""):
    if not m.get("bars"):
        print(f"  {m['label']}: 无数据"); return
    print(f"  {m['label']:<16} {m['net%']:>+8.1f}%  年化{m['ann%']:>+7.1f}%  Sharpe{m['sharpe']:>5.2f}  "
          f"回撤{m['maxDD%']:>6.1f}%  {extra}")
    print(f"      逐年 {m['yearly']}")


# ---------------------------------------------------------- 期现资金费
def spotperp_one(coin, fee_spot, fee_perp, start, end, lev, rule=None, win=18,
                 enter_bp=2.0, exit_bp=0.0):
    """rule=None 一直持有；rule='funding_pos' 仅当滚动费率>0 时持有（t-1 决策，无前视）。"""
    sp, pp, fu = load_spot(coin), load_perp(coin), load_funding(coin)
    if sp is None or pp is None or fu is None:
        return None
    df = pd.DataFrame({"spot": sp, "perp": pp}).dropna()
    df = df[(df.index >= pd.Timestamp(start, tz="UTC")) & (df.index < pd.Timestamp(end, tz="UTC"))]
    if len(df) < 100:
        return None
    fu = fu.reindex(df.index).fillna(0.0)
    rs = df["spot"].pct_change().fillna(0.0)
    rp = df["perp"].pct_change().fillna(0.0)
    funding = fu
    basis = rs - rp
    if rule == "funding_pos":
        # 滞回：滚动均值年化 > enter_bp 才进场，< exit_bp 才离场（都用 t-1 数据决策）
        roll = fu.rolling(win).mean() * 1095 * 100          # 年化 %
        raw = pd.Series(0.0, index=df.index)
        state = 0.0
        for i in range(len(df)):
            v = roll.iloc[i - 1] if i > 0 and np.isfinite(roll.iloc[i - 1]) else np.nan
            if np.isfinite(v):
                if state == 0 and v > enter_bp:
                    state = 1.0
                elif state == 1 and v < exit_bp:
                    state = 0.0
            raw.iloc[i] = state
        sig = raw
    else:
        sig = pd.Series(1.0, index=df.index)
    switch = sig.diff().abs().fillna(sig.iloc[0])
    fee = switch * (fee_spot + fee_perp)
    pnl = sig * (funding + basis) - fee
    cap = 1.0 + 1.0 / lev
    return {"coin": coin, "pnl": pnl, "funding": (sig * funding).sum() * 100,
            "basis": (sig * basis).sum() * 100, "fee": fee.sum() * 100, "cap": cap,
            "bars": len(df), "exposure": sig.mean() * 100, "switches": int(switch.sum())}


def run_spotperp(coins, fee_spot, fee_perp, start, end, lev, rule=None, win=18,
                 enter_bp=2.0, exit_bp=0.0):
    tag = ("一直持有" if rule is None
           else f"费率滞回（滚动{win}bar；年化>{enter_bp:.0f}% 进、<{exit_bp:.0f}% 出）")
    print(f"=== ① 期现资金费套利（多现货 + 空永续，保证金 {1/lev:.0%} → 占用资本 {1+1/lev:.2f}x）｜{tag} ===")
    print(f"{'coin':<6}{'净%':>9}{'年化%':>8}{'年化%/资本':>11}{'Sharpe':>8}{'费率%':>9}"
          f"{'基差%':>8}{'手续费%':>9}{'在场%':>7}{'开关':>5}")
    rows = []
    for c in coins:
        r = spotperp_one(c, fee_spot, fee_perp, start, end, lev, rule, win, enter_bp, exit_bp)
        if not r:
            print(f"{c:<6}  无数据"); continue
        m = metrics(r["pnl"], c)
        rows.append((c, r, m))
        print(f"{c:<6}{m['net%']:>+9.1f}{m['ann%']:>+8.1f}{m['ann%']/r['cap']:>+11.1f}"
              f"{m['sharpe']:>8.2f}{r['funding']:>+9.1f}{r['basis']:>+8.1f}{-r['fee']:>+9.1f}"
              f"{r['exposure']:>7.0f}{r['switches']:>5}")
    if rows:
        port = pd.concat([r["pnl"] for _, r, _ in rows], axis=1).mean(axis=1)
        mm = metrics(port, "等权组合")
        avg_cap = float(np.mean([r["cap"] for _, r, _ in rows]))
        show(mm, f"占用资本 {avg_cap:.2f}x → 资本年化 {mm['ann%']/avg_cap:+.1f}%")
        print(f"      分币年化（%/资本）: " + "  ".join(
            f"{c}:{m['ann%']/r['cap']:+.1f}" for c, r, m in rows))
    return rows


# ---------------------------------------------------------- 交割基差
def load_quarterly():
    out = []
    for f in sorted(glob.glob(f"{QUART}/*.feather")):
        n = os.path.basename(f).replace(".feather", "")
        m = re.match(r"([A-Z0-9]+)_(\d{6})", n)
        if not m:
            continue
        coin, exp = m.group(1), m.group(2)
        df = pd.read_feather(f)[["date", "close"]]
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df.set_index("date")["close"].dropna()
        expiry = pd.Timestamp(f"20{exp[:2]}-{exp[2:4]}-{exp[4:6]}", tz="UTC") + pd.Timedelta(hours=8)
        out.append({"file": n, "coin": coin, "expiry": expiry, "px": df})
    return out


def run_calendar(thresh, horizon, fee_round):
    """对每个到期合约：剩余 <= horizon 时若年化基差 >= thresh 则建仓持有到期。

    合约之间不合并（ETHUSDT 与 ETHUSD 是两个产品，同一到期共存）。
    """
    print(f"\n=== ② 交割基差套利（多现货 + 空季度；年化门槛 {thresh:.0%}，窗口 T−{horizon}天）===")
    items = load_quarterly()
    base_coin = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "ETHUSD": "ETH"}
    rows = []
    seen = set()
    for it in items:
        coin = base_coin.get(it["coin"])
        if coin is None:
            continue
        spot = load_spot(coin)
        if spot is None:
            continue
        s = spot.reindex(it["px"].index).ffill()
        d = pd.DataFrame({"F": it["px"], "S": s}).dropna()
        if len(d) < 50:
            continue
        days_left = (it["expiry"] - d.index).total_seconds() / 86400
        ok = (days_left > 0.5) & (days_left <= horizon)
        sub = d[ok]
        if sub.empty:
            continue
        ann = (sub["F"] / sub["S"] - 1) * 365 / days_left[ok]
        hit = ann[ann >= thresh]
        if hit.empty:
            continue
        t0 = hit.index[0]
        key = (it["file"], str(t0))
        if key in seen:
            continue
        seen.add(key)
        F0, S0 = float(d.loc[t0, "F"]), float(d.loc[t0, "S"])
        dleft = float(days_left[d.index.get_loc(t0)])
        gross = (F0 - S0) / S0
        net = gross - fee_round
        rows.append({"contract": it["coin"], "coin": coin, "expiry": str(it["expiry"])[:10],
                     "entry": str(t0)[:10], "days": dleft, "gross%": gross * 100,
                     "net%": net * 100, "ann_real%": net * 365 / dleft * 100})
    if not rows:
        print("  无符合条件的建仓机会"); return
    r = pd.DataFrame(rows).sort_values(["contract", "expiry", "entry"])
    print(f"  建仓次数 {len(r)}  正收益 {(r['net%'] > 0).mean() * 100:.0f}%  "
          f"平均持有 {r['days'].mean():.0f} 天  覆盖 {r['entry'].min()} ~ {r['entry'].max()}")
    print(f"{'合约':<10}{'到期':<12}{'建仓':<12}{'持有天':>7}{'毛%':>8}{'净%':>8}{'实际年化%':>10}")
    for _, x in r.iterrows():
        print(f"{x['contract']:<10}{x['expiry']:<12}{x['entry']:<12}{x['days']:>7.0f}"
              f"{x['gross%']:>8.2f}{x['net%']:>8.2f}{x['ann_real%']:>10.1f}")
    print("\n  汇总（每次建仓 = 1 现货名义，多现货+空季度）：")
    print(f"    平均毛 {r['gross%'].mean():.2f}%  平均净 {r['net%'].mean():.2f}%  "
          f"实际年化 均值{r['ann_real%'].mean():.1f}% 中位{r['ann_real%'].median():.1f}%  "
          f"最差{r['ann_real%'].min():.1f}%")
    for c, g in r.groupby("contract"):
        print(f"    {c:<10} n={len(g):>2}  正收益{(g['net%'] > 0).mean() * 100:>3.0f}%  "
              f"平均净{g['net%'].mean():>+6.2f}%  年化均值{g['ann_real%'].mean():>+6.1f}%")
    r.to_csv("user_data/reports/calendar_basis_trades.csv", index=False)
    print("    明细已存 user_data/reports/calendar_basis_trades.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["spotperp", "calendar", "both"], default="both")
    ap.add_argument("--coins", nargs="*", default=["BTC", "ETH", "BNB", "XRP", "SOL", "DOGE", "TRX", "ZEC"])
    ap.add_argument("--start", default="20220101")
    ap.add_argument("--end", default="20240828")
    ap.add_argument("--fee-spot", type=float, default=0.001)
    ap.add_argument("--fee-perp", type=float, default=0.0005)
    ap.add_argument("--lev", type=float, default=3.0, help="永续腿杠杆（决定保证金占用）")
    ap.add_argument("--thresh", type=float, default=0.05, help="交割基差年化门槛")
    ap.add_argument("--horizon", type=int, default=30, help="建仓窗口（到期前天数）")
    ap.add_argument("--rule", choices=["hold", "funding_pos"], default="hold",
                    help="期现规则：hold=一直持有；funding_pos=仅费率转正持有")
    ap.add_argument("--win", type=int, default=18, help="费率滚动窗口（bar，18=3天）")
    ap.add_argument("--enter-bp", type=float, default=2.0, help="进场阈值（滚动费率年化%）")
    ap.add_argument("--exit-bp", type=float, default=0.0, help="离场阈值（滚动费率年化%）")
    a = ap.parse_args()
    if a.mode in ("spotperp", "both"):
        run_spotperp(a.coins, a.fee_spot, a.fee_perp, a.start, a.end, a.lev,
                     None if a.rule == "hold" else a.rule, a.win, a.enter_bp, a.exit_bp)
    if a.mode in ("calendar", "both"):
        run_calendar(a.thresh, a.horizon, a.fee_spot + a.fee_perp)


if __name__ == "__main__":
    main()
