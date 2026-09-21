#!/usr/bin/env python
"""单所跨品种套利研究台（币安 USDT 永续，市场中性口径）。

两条独立路子，共用同一套成本/费率结算：
  1) carry  横截面费率分散：做多"近期费率最负"的 k 个、做空"费率最正"的 k 个，
            吃两端费率，价格方向近似互相抵消（多空各半 → 净敞口 0）。
  2) pairs  协整价差回归：滚动 β 对冲（窗口 W），残差 z-score 越界进场、回归出场。

纪律：默认只跑 TEST 段（20220101-20240828），VAL 段留给定版候选一次性验证。
成本：双边 taker 费率 + 可选滑点；费率按结算事件逐 bar 计入。

用法:
  .venv/Scripts/python.exe user_data/scripts/cross_arb_research.py --mode both --pool top10
  ... --mode carry --pool core --k 3 --rebal 6 --lookback 18 --fee 0.0005
  ... --mode pairs --pool top10 --z-in 2.0 --z-out 0.5 --beta-win 180 --fee 0.0005
"""
import argparse
import glob
import os
import re
from itertools import combinations

import numpy as np
import pandas as pd

BASE = "user_data/data/binance/futures"
BARS_PER_YEAR = 365 * 6  # 4h


def fname(pair, kind, tf):
    return os.path.join(BASE, pair.replace("/", "_").replace(":", "_") + f"-{tf}-{kind}.feather")


def load_pool(pool):
    path = f"user_data/universe/pairs_{pool}.txt"
    pairs = []
    for line in open(path, encoding="utf-8"):
        s = line.split("#")[0].strip()
        if s and "/" in s:
            pairs.append(s)
    return pairs


def load_prices(pairs, tf="4h", start=None, end=None):
    """返回 close 面板（index=UTC datetime, columns=base symbol）。"""
    cols = {}
    for p in pairs:
        f = fname(p, "futures", tf)
        if not os.path.exists(f):
            continue
        df = pd.read_feather(f)[["date", "close"]]
        df["date"] = pd.to_datetime(df["date"], utc=True)
        s = df.set_index("date")["close"]
        if start:
            s = s[s.index >= pd.Timestamp(start, tz="UTC")]
        if end:
            s = s[s.index < pd.Timestamp(end, tz="UTC")]
        cols[p.split("/")[0]] = s
    return pd.DataFrame(cols).sort_index()


def load_funding(pairs, tf="4h", start=None, end=None):
    """返回逐 bar 已结算费率之和的面板（同价格 index/columns）。"""
    cols = {}
    for p in pairs:
        f = fname(p, "funding_rate", "1h")
        if not os.path.exists(f):
            continue
        df = pd.read_feather(f)[["date", "open"]].rename(columns={"open": "rate"})
        df["date"] = pd.to_datetime(df["date"], utc=True)
        if start:
            df = df[df["date"] >= pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=7)]
        if end:
            df = df[df["date"] < pd.Timestamp(end, tz="UTC")]
        s = df.set_index("date")["rate"]
        cols[p.split("/")[0]] = s
    if not cols:
        return pd.DataFrame()
    raw = pd.DataFrame(cols).sort_index()
    # 落到 4h 网格：每个 bar 内结算的费率求和
    return raw.resample(tf).sum(min_count=1)


def align(px, fr):
    idx = px.index
    fr = fr.reindex(idx)
    return px, fr


def metrics(ret, label):
    """ret = 逐 bar 组合收益率（按本金 1.0 计）。"""
    if ret.empty or ret.std() == 0:
        return {"label": label, "bars": 0}
    eq = ret.cumsum()
    dd = (eq - eq.cummax()).min()
    ann = ret.mean() * BARS_PER_YEAR * 100
    sharpe = ret.mean() / ret.std() * np.sqrt(BARS_PER_YEAR)
    yr = ret.groupby(ret.index.year).sum() * 100
    return {"label": label, "bars": len(ret), "net%": ret.sum() * 100, "ann%": ann,
            "sharpe": sharpe, "maxDD%": dd * 100, "neg_bar%": (ret < 0).mean() * 100,
            "yearly": " ".join(f"{y}:{v:+.1f}%" for y, v in yr.items())}


def show(m, extra=""):
    if not m.get("bars"):
        print(f"  {m['label']}: 无数据")
        return
    print(f"  {m['label']:<18} bars={m['bars']:>5} 净{m['net%']:>+8.1f}%  年化{m['ann%']:>+7.1f}%  "
          f"Sharpe{m['sharpe']:>5.2f}  最大回撤{m['maxDD%']:>6.1f}%  负bar{m['neg_bar%']:>4.0f}%  {extra}")
    print(f"      逐年 {m['yearly']}")


# ---------------------------------------------------------------- carry
def run_carry(px, fr, k, rebal, lookback, fee, fund_shift=0):
    """横截面费率分散。权重 = ±0.5/k，多空各半 → 净敞口 0。

    fund_shift=1 时把费率结算整体后移一个 bar（保守口径）——若收益仅在这个
    错位下成立，说明靠的是结算瞬间对齐而非真实 carry。
    """
    rets = px.pct_change()
    fund = fr.reindex(px.index)
    if fund_shift:
        fund = fund.shift(fund_shift)
    syms = [c for c in px.columns]
    w = pd.Series(0.0, index=syms)
    out, logs = [], []
    fund_pnl_tot = price_pnl_tot = fee_tot = 0.0
    start_i = lookback
    for i in range(start_i + 1, len(px)):
        t = px.index[i]
        # 费率结算（当日已结算部分）
        f = fund.loc[t].reindex(syms)
        f = f.fillna(0.0)
        fp = float(-(w * f).sum())
        r = rets.iloc[i].reindex(syms).fillna(0.0)
        pp = float((w * r).sum())
        cost = 0.0
        if (i - start_i) % rebal == 0:
            # 用回看窗口费率排名重设权重
            win = fund.iloc[i - lookback:i].sum()
            valid = win.dropna()
            valid = valid[px.iloc[i].reindex(valid.index).notna()]
            if len(valid) >= 2 * k:
                lo = valid.nsmallest(k).index
                hi = valid.nlargest(k).index
                new = pd.Series(0.0, index=syms)
                new[lo] = 0.5 / k
                new[hi] = -0.5 / k
                cost = float((new - w).abs().sum()) * fee
                if (i - start_i) // rebal <= 1 or i == start_i + rebal:
                    logs.append((str(t)[:16], list(lo), list(hi)))
                w = new
        bar = pp + fp - cost
        fund_pnl_tot += fp
        price_pnl_tot += pp
        fee_tot += cost
        out.append((t, bar))
    s = pd.Series(dict(out))
    return s, {"funding": fund_pnl_tot * 100, "price": price_pnl_tot * 100, "fee": fee_tot * 100}, logs


# ---------------------------------------------------------------- pairs
def run_pairs(px, fr, z_in, z_out, beta_win, max_hold, fee, corr_min=0.0):
    """滚动 β 对冲残差的 z-score 均值回归。每对独立跑，返回逐 bar 收益的 DataFrame。

    corr_min>0 时用 t-1 收盘前的滚动收益相关做过滤（无前视）：只交易相关性够高的对，
    避免在走独立行情的两个品种上赌均值回归。
    """
    rets = px.pct_change()
    cols = {}
    pairs = list(combinations(px.columns, 2))
    for a, b in pairs:
        sub = px[[a, b]].dropna()
        if len(sub) < beta_win + 60:
            continue
        la, lb = np.log(sub[a]), np.log(sub[b])
        cov = la.rolling(beta_win).cov(lb)
        var = lb.rolling(beta_win).var()
        beta = (cov / var).shift(1)  # 用 t-1 的 β 防前视
        spread = la - beta * lb
        mu = spread.rolling(beta_win).mean()
        sd = spread.rolling(beta_win).std()
        z = ((spread - mu) / sd).shift(1)  # t 时刻可用信号
        if corr_min > 0:
            rc = rets[a].reindex(sub.index).rolling(beta_win).corr(rets[b].reindex(sub.index)).shift(1)
        else:
            rc = pd.Series(1.0, index=sub.index)
        pos = 0.0
        hold = 0
        bar_ret, bar_fund, bar_fee = [], [], []
        idx = []
        w_a = w_b = 0.0
        r_a, r_b = rets[a].reindex(sub.index), rets[b].reindex(sub.index)
        f_a = fr[a].reindex(sub.index).fillna(0.0) if a in fr.columns else pd.Series(0.0, index=sub.index)
        f_b = fr[b].reindex(sub.index).fillna(0.0) if b in fr.columns else pd.Series(0.0, index=sub.index)
        for i in range(beta_win + 1, len(sub)):
            zi = z.iloc[i]
            cost = 0.0
            beta_i = beta.iloc[i]
            if not np.isfinite(zi) or not np.isfinite(beta_i) or beta_i <= 0:
                idx.append(sub.index[i]); bar_ret.append(0.0); bar_fund.append(0.0); bar_fee.append(0.0)
                continue
            corr_ok = (rc.iloc[i] >= corr_min) if np.isfinite(rc.iloc[i]) else False
            want = pos
            if pos == 0:
                if zi > z_in and corr_ok:
                    want, hold = -1.0, 0
                elif zi < -z_in and corr_ok:
                    want, hold = 1.0, 0
            else:
                hold += 1
                if abs(zi) < z_out or hold >= max_hold:
                    want = 0.0
            if want != pos:
                scale = 1.0 / (1.0 + beta_i)
                nwa, nwb = (want * scale, -want * beta_i * scale) if want != 0 else (0.0, 0.0)
                cost = (abs(nwa - w_a) + abs(nwb - w_b)) * fee
                w_a, w_b, pos = nwa, nwb, want
            fund_pnl = -(w_a * f_a.iloc[i] + w_b * f_b.iloc[i])
            idx.append(sub.index[i])
            bar_ret.append(w_a * r_a.iloc[i] + w_b * r_b.iloc[i])
            bar_fund.append(fund_pnl)
            bar_fee.append(cost)
        df = pd.DataFrame({"price": bar_ret, "funding": bar_fund, "fee": bar_fee}, index=idx)
        df["net"] = df["price"] + df["funding"] - df["fee"]
        cols[f"{a}-{b}"] = df
    return cols


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["carry", "pairs", "both"], default="both")
    ap.add_argument("--pool", default="top10")
    ap.add_argument("--start", default="20220101")
    ap.add_argument("--end", default="20240828")
    ap.add_argument("--fee", type=float, default=0.0005, help="单边手续费（taker 0.05%）")
    # carry
    ap.add_argument("--k", type=int, default=2, help="每侧品种数")
    ap.add_argument("--rebal", type=int, default=6, help="再平衡间隔（bar，6=24h）")
    ap.add_argument("--lookback", type=int, default=18, help="费率回看窗口（bar，18=3天）")
    # pairs
    ap.add_argument("--z-in", type=float, default=2.0)
    ap.add_argument("--z-out", type=float, default=0.5)
    ap.add_argument("--beta-win", type=int, default=180, help="β 估计窗口（bar，180=30天）")
    ap.add_argument("--max-hold", type=int, default=60, help="最长持有（bar，60=10天）")
    ap.add_argument("--corr-min", type=float, default=0.0, help="配对的滚动收益相关下限（t-1 口径）")
    ap.add_argument("--fund-shift", type=int, default=0, help="费率结算后移 bar 数（保守检验用 1）")
    a = ap.parse_args()

    pairs = load_pool(a.pool)
    px = load_prices(pairs, start=a.start, end=a.end)
    fr = load_funding([p for p in pairs if p.split("/")[0] in px.columns], start=a.start, end=a.end)
    px, fr = align(px, fr)
    print(f"池={a.pool}（{len(pairs)} 请求 / {px.shape[1]} 有数据）  "
          f"区间 {px.index[0]:%Y-%m-%d}~{px.index[-1]:%Y-%m-%d}  bars={len(px)}  "
          f"费率面板 {fr.notna().sum().sum()}/{fr.size} 有效  费率={a.fee*100:.3f}%/边\n")

    if a.mode in ("carry", "both"):
        print(f"=== ① 横截面费率分散  k={a.k}  rebal={a.rebal}bar  lookback={a.lookback}bar ===")
        for k in sorted({max(1, a.k - 1), a.k, a.k + 1}):
            if k * 2 > px.shape[1]:
                continue
            s, br, logs = run_carry(px, fr, k, a.rebal, a.lookback, a.fee, a.fund_shift)
            m = metrics(s, f"carry k={k}")
            show(m, f"费率{br['funding']:>+7.1f}% 价格{br['price']:>+7.1f}% 费{br['fee']:>6.1f}%")
            if k == a.k:
                btc = px["BTC"].pct_change().reindex(s.index).shift(0)
                cc = np.corrcoef(s.values, btc.fillna(0).values)[0, 1]
                print(f"      与 BTC 逐bar相关 {cc:+.3f}（|r|<0.3 才算真中性）")
                print(f"      首期持仓 多{logs[0][1]} 空{logs[0][2]}" if logs else "")
        # 费率口径敏感性
        print("  手续费敏感性:")
        for fee in (0.0, 0.0002, 0.0005, 0.001):
            s, br, _ = run_carry(px, fr, a.k, a.rebal, a.lookback, fee, a.fund_shift)
            m = metrics(s, f"fee={fee*100:.2f}%/边")
            show(m)
        print()

    if a.mode in ("pairs", "both"):
        print(f"=== ② 协整价差回归  z_in={a.z_in} z_out={a.z_out} "
              f"β窗口={a.beta_win}bar 最长持有={a.max_hold}bar 相关下限={a.corr_min} ===")
        res = run_pairs(px, fr, a.z_in, a.z_out, a.beta_win, a.max_hold, a.fee, a.corr_min)
        if not res:
            print("  无可用配对")
        else:
            rows = []
            for name, df in res.items():
                net = df["net"]
                rows.append({"pair": name, "net%": net.sum() * 100,
                             "sharpe": net.mean() / net.std() * np.sqrt(BARS_PER_YEAR) if net.std() else 0,
                             "funding%": df["funding"].sum() * 100,
                             "feecount": (df["fee"] > 0).sum()})
            r = pd.DataFrame(rows).sort_values("net%", ascending=False)
            print(f"  配对数 {len(r)}  中位净收益 {r['net%'].median():+.1f}%  "
                  f"正收益占比 {(r['net%'] > 0).mean() * 100:.0f}%")
            print(f"{'pair':<12}{'净%':>9}{'Sharpe':>8}{'费率贡献%':>11}{'换手次数':>9}")
            for _, x in pd.concat([r.head(6), r.tail(4)]).iterrows():
                print(f"{x['pair']:<12}{x['net%']:>+9.1f}{x['sharpe']:>8.2f}"
                      f"{x['funding%']:>+11.1f}{int(x['feecount']):>9}")
            # 可实施版本：全配对等权（无选择偏差）
            allnet = pd.DataFrame({k: v["net"] for k, v in res.items()}).fillna(0.0)
            port = allnet.mean(axis=1)
            m = metrics(port, "全配对等权组合")
            show(m, f"实际同时开仓上限≈{len(res)} 对")
            btc = px["BTC"].pct_change().reindex(port.index).fillna(0)
            print(f"      与 BTC 逐bar相关 {np.corrcoef(port.values, btc.values)[0,1]:+.3f}")


if __name__ == "__main__":
    main()
