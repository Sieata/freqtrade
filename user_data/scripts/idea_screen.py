"""新策略族第一轮快筛（TEST 段、TOP10 池）—— RESEARCH 第 24 节配套脚本。

已证伪方向（不重测）：做空暴涨/SynthPutV1、时序 CTA（23 节）、协整价差回归（21.3）、
费率横截面的价格腿（21.3 符号随池翻转）、币本位交割x永续（22 节）、凌晨闪崩、FreqAI。
幸存臂共性 = 短持仓、事件驱动、均值回归/拥挤度反转、多头侧。本轮新增测四个维度：

  F1 XSREV   横截面反转：TOP10 内按 lb 日收益排名，做多最弱 k 个（long-only），持 H 日
             变体：neg=只买绝对下跌者；wknd=只在周六/日 UTC 进场（V2 信号密度探针）
  F2 LEADLAG BTC 先动、中盘跟随：BTC 单根/24h 大涨后下一根开盘买全部非 BTC，持 2-12 根 4h
             （空头腿仅作不对称对照，预期不成立——"做空暴跌"侧）
  F4 XSMOM   横截面动量（F1 对照组）：做多最强 k 个
  F3 CAL     时段x星期 4h 收益分桶扫描（描述性，找 V2 窗口外的新时段坑）

第二轮（2026-09-21，由 F2 CTRL 空头对照腿的失败方向驱动：BTC 24h<=-5% 后做空平均亏
-1.24%/24h、单笔 t=-4.8 -> 反向=崩盘反弹在组合层面成立）：

  F5 CRASHP  BTC 24h 崩盘触发 -> 组合买入中盘（多头反弹）。网格 = 触发 {-3%,-5%} x
             选币 {all, worst3(自身 24h 跌最深 3 个)} x funding 条件 {any, negf(最近已
             结算费率<0=空头拥挤付费)} x 持有 {6b,12b}，共 16 变体，全量打印。
             预注册门禁（先于运行写定）：事件数>=60 且 事件t>=2.0 才过第一关 ->
             三刀 + 费用压力；另测与 CrashBuyV2 的事件独立性（重叠>70%=同臂变体非新臂）。

口径：
  - 4h 网格；信号在 t 收盘出，t+1 根开盘进场（无前视，进场价=下一根 4h 开盘）；
  - funding 结算记入 (E, X] 持有区间（cta_research 同款保守口径，多头付正费率）；
  - fee 0.05%/边 taker；胜出者加 0.1%/边压力；
  - 只跑 TEST 20220101-20240828，VAL 一根手指都不碰；
  - 网格全量打印（非挑选后汇报），多重检验用 t 阈值 + 晋级三刀控制。

预注册晋级规则：族内「事件级 t」最高且事件数>=120 者为该族代表 ->
集中度（剔最赚 20 笔）/ 逐年 / 品种宽度三刀，任一不过即不晋级 freqtrade 原型。

用法:
  ./.venv/Scripts/python.exe user_data/scripts/idea_screen.py --fam f3
  ./.venv/Scripts/python.exe user_data/scripts/idea_screen.py --fam f1
  ./.venv/Scripts/python.exe user_data/scripts/idea_screen.py --fam f2
  ./.venv/Scripts/python.exe user_data/scripts/idea_screen.py --fam f4
  ./.venv/Scripts/python.exe user_data/scripts/idea_screen.py --fam f5
  ./.venv/Scripts/python.exe user_data/scripts/idea_screen.py --fam all --detail 2
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from scipy.stats import norm as _norm

    def pval(t):
        return float(_norm.sf(abs(t)) * 2) if np.isfinite(t) else np.nan
except Exception:  # pragma: no cover
    def pval(t):
        return np.nan

FD = Path("user_data/data/binance/futures")
FEE = 0.0005
FEE_STRESS = 0.0010
TEST = (pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2024-08-28", tz="UTC"))
# TOP10 池（HYPE 2025-05 上市 -> TEST 段无数据，本轮不参与；XMR 2024 中退市 -> 自动按数据截断）
SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "ZEC", "DOGE", "XMR"]
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 250)

H4 = pd.Timedelta(hours=4)
D1 = pd.Timedelta(days=1)


# ---------------------------------------------------------------- 数据
def load(sym):
    f4 = pd.read_feather(FD / f"{sym}_USDT_USDT-4h-futures.feather")
    f4["date"] = pd.to_datetime(f4["date"], utc=True)
    b = f4.set_index("date")[["open", "close"]].sort_index()
    b = b[~b.index.duplicated(keep="last")]
    fr = pd.read_feather(FD / f"{sym}_USDT_USDT-1h-funding_rate.feather")
    fr["date"] = pd.to_datetime(fr["date"], utc=True)
    rate = pd.Series(pd.to_numeric(fr["open"], errors="coerce").values,
                     index=pd.DatetimeIndex(fr["date"])).sort_index().dropna()
    rate = rate[rate.index.hour.isin([0, 8, 16])]
    sett = rate.groupby(level=0).sum()
    u = b.index.union(sett.index)
    cum_at = (sett.reindex(u).fillna(0.0).sort_index().cumsum()
              .reindex(b.index).ffill().fillna(0.0))
    rate_at = rate.reindex(b.index).ffill()  # bar 时点已知的最近一次已结算费率
    return b, cum_at, rate_at


def build_all():
    bars, cums, rats, posmap = {}, {}, {}, {}
    for s in SYMS:
        b, c, r8 = load(s)
        bars[s], cums[s], rats[s] = b, c, r8
        posmap[s] = {t: i for i, t in enumerate(b.index)}
    return bars, cums, rats, posmap


def sim_symbol(sym, sel_times, hold, posmap, bars, cums, fee=FEE, side=1):
    """sel_times: 被选中的信号时刻（=进场 bar 开始时刻 E，已在 TEST 内）。
    返回 trades: (E, sym, net, price_leg, fund, fee_paid)。空腿: 价格腿取负、funding 变收入。"""
    out = []
    if not len(sel_times):
        return out
    O, pos = bars[sym]["open"], posmap[sym]
    cum, free = cums[sym], None
    for E in sel_times:
        if free is not None and E < free:
            continue
        X = E + hold
        iE, iX = pos.get(E), pos.get(X)
        if iE is None or iX is None:
            continue
        ep, xp = O.iloc[iE], O.iloc[iX]
        if not (np.isfinite(ep) and np.isfinite(xp)):
            continue
        fund = cum.iloc[iX] - cum.iloc[iE]
        pr = xp / ep - 1.0
        net = (pr - fund - 2 * fee) if side > 0 else (-pr + fund - 2 * fee)
        out.append((E, sym, net, pr, fund, 2 * fee))
        free = X
    return out


# ---------------------------------------------------------------- 统计
def tstat(x):
    x = pd.Series(x).dropna()
    if len(x) < 5 or x.std(ddof=1) == 0:
        return np.nan
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))


def eventize(trades):
    """按进场时刻聚合为事件（等权均值）。"""
    if not trades:
        return pd.Series(dtype=float)
    df = pd.DataFrame(trades, columns=["E", "sym", "net", "pr", "fund", "fee"])
    return df.groupby("E")["net"].mean()


def row_of(label, trades):
    ev = eventize(trades)
    nets = pd.Series([t[2] for t in trades])
    if len(nets) == 0:
        return None
    up, dn = nets[nets > 0].sum(), -nets[nets < 0].sum()
    return {
        "配置": label,
        "笔数": len(nets),
        "事件数": len(ev),
        "均值%": nets.mean() * 100,
        "胜率%": (nets > 0).mean() * 100,
        "PF": up / dn if dn > 0 else np.inf,
        "笔t": tstat(nets),
        "事件t": tstat(ev),
        "p(事件)": pval(tstat(ev)),
        "Sn%": nets.sum() * 100,
    }


def detail(trades, title):
    """胜出者细节：逐年 / 逐品种 / 集中度。"""
    print(f"\n{'-' * 100}\n### {title}\n{'-' * 100}")
    df = pd.DataFrame([{"E": t[0], "sym": t[1], "net": t[2]} for t in trades])
    if df.empty:
        print("无交易")
        return
    yr = df.assign(y=df["E"].dt.year).groupby("y").agg(
        n=("net", "size"), Sum=("net", lambda x: round(x.sum() * 100, 1)))
    print("逐年（Σ净%/笔，每笔 $1,000 名义 -> 逐年收益率 = Σ% x 10）:")
    print(yr.to_string())
    ps = df.groupby("sym").agg(n=("net", "size"),
                               mean=("net", lambda x: round(x.mean() * 100, 3)),
                               Sum=("net", lambda x: round(x.sum() * 100, 1)))
    print("\n逐品种（n / 均值% / Σ%）:")
    print(ps.to_string())
    srt = df.sort_values("net", ascending=False)
    tot = srt["net"].sum()
    if len(srt) > 40 and tot > 0:
        t20 = srt["net"].head(20).sum()
        rest = srt["net"].iloc[20:].sum()
        print(f"\n集中度: 最赚 20 笔贡献 {t20 * 100:+.1f}%（占 Σ {t20 / tot * 100:.0f}%），"
              f"剔除后 Σ {rest * 100:+.1f}%（{'转负=死' if rest <= 0 else '未转负'}）")


def stress(cfg, ctx):
    """费用压力：fee=0.1%/边 重跑。cfg = ('xs', sel_dict, hold) 或 ('lag', times, hold, side)。"""
    posmap, bars, cums = ctx
    kind = cfg[0]
    if kind == "xs":
        _, sel, hold = cfg
        trades = []
        for s in sel:
            trades += sim_symbol(s, sorted(sel[s]), hold, posmap, bars, cums, fee=FEE_STRESS)
    else:
        _, times, hold, side = cfg
        trades = []
        for s in [x for x in SYMS if x != "BTC"]:
            trades += sim_symbol(s, sorted(times), hold, posmap, bars, cums, fee=FEE_STRESS, side=side)
    nets = pd.Series([t[2] for t in trades])
    ev = eventize(trades)
    print(f"   费用压力 fee=0.1%/边: 均值 {nets.mean() * 100:+.3f}%  "
          f"事件t {tstat(ev):+.2f}  Σ {nets.sum() * 100:+.1f}%")


# ---------------------------------------------------------------- F3 时段x星期
def fam_cal(bars):
    rows = []
    for s, b in bars.items():
        r = (b["close"] / b["close"].shift(1) - 1).dropna()
        r = r[(r.index >= TEST[0]) & (r.index < TEST[1])]
        rows.append(pd.DataFrame({"ret": r, "dw": r.index.dayofweek, "hr": r.index.hour}))
    d = pd.concat(rows)
    print(f"\n{'=' * 100}\nF3 时段x星期扫描（TEST，{len(d)} 品种-4h bars，收益=close/close[-1]）\n{'=' * 100}")
    print("\n--- 按小时（4h bar 开始时刻）---")
    g = d.groupby("hr")["ret"]
    print(pd.DataFrame({"n": g.size(), "均值bps": g.mean() * 1e4,
                        "t": g.mean() / (g.std() / np.sqrt(g.size()))}).round(2).to_string())
    print("\n--- 按星期（bar 开始时刻）---")
    g = d.groupby("dw")["ret"]
    print(pd.DataFrame({"n": g.size(), "均值bps": g.mean() * 1e4,
                        "t": g.mean() / (g.std() / np.sqrt(g.size()))}).round(2).to_string())
    print("\n--- 星期x小时 均值 bps（仅 n>=1500 的格子，其余空白）---")
    p = d.pivot_table(index="dw", columns="hr", values="ret", aggfunc=["mean", "size"])
    means = (p["mean"] * 1e4).where(p["size"] >= 1500)
    print(means.round(1).to_string())
    print("\n--- 结算 bar vs 非结算 bar（结算 bar = 收盘时刻恰为 00/08/16 结算点）---")
    m = d["hr"].isin([20, 4, 12])
    for name, mm in (("结算bar", m), ("非结算bar", ~m)):
        r = d.loc[mm, "ret"]
        print(f"{name}: n={len(r)} 均值 {r.mean() * 1e4:+.2f}bps  t={tstat(r):.2f}")


# ---------------------------------------------------------------- F1/F4 横截面
def daily_panel(bars):
    """每日 00:00 收盘面板（信号时刻 = 00:00 = 20:00 起始 bar 的收盘）。"""
    P = {}
    for s, b in bars.items():
        c = b.loc[b.index.hour == 20, "close"]
        P[s] = pd.Series(c.values, index=c.index + H4)
    return pd.DataFrame(P)


def fam_xs(bars, cums, posmap, side="rev"):
    P = daily_panel(bars)
    sig_times = P.index[(P.index >= TEST[0]) & (P.index < TEST[1])]
    lbs = [1, 2] if side == "rev" else [3, 7, 14]
    rows, store = [], {}
    for lb in lbs:
        R = P / P.shift(lb) - 1.0
        for k in (2, 3):
            for hdays in (1, 2, 3):
                for neg in ((False, True) if side == "rev" else (False,)):
                    for wknd in ((False, True) if side == "rev" else (False,)):
                        sel = {s: [] for s in SYMS}
                        for t in sig_times:
                            if wknd and t.dayofweek not in (5, 6):
                                continue
                            r = R.loc[t].dropna()
                            if len(r) < 6:
                                continue
                            cand = r[r < 0] if neg else r
                            if not len(cand):
                                continue
                            picks = cand.nsmallest(k) if side == "rev" else r.nlargest(k)
                            for s in picks.index:
                                sel[s].append(t)
                        trades = []
                        for s in SYMS:
                            trades += sim_symbol(s, sorted(sel[s]), hdays * D1, posmap, bars, cums)
                        label = (f"{'XSREV' if side == 'rev' else 'XSMOM'} lb={lb}d k={k} H={hdays}d"
                                 + (" neg" if neg else "") + (" wknd" if wknd else ""))
                        r = row_of(label, trades)
                        if r:
                            rows.append(r)
                            store[label] = {"sel": sel, "hold": hdays * D1, "trades": trades}
    t = pd.DataFrame(rows).sort_values("事件t", ascending=False)
    print(f"\n{'=' * 110}\n{'F1 XSREV 横截面反转' if side == 'rev' else 'F4 XSMOM 横截面动量'}"
          f"（做多{'最弱' if side == 'rev' else '最强'} k 个 · TEST · 含 funding · fee 0.05%/边）\n{'=' * 110}")
    print(t.round(3).to_string(index=False))
    return store, t


# ---------------------------------------------------------------- F2 BTC 领先
def fam_leadlag(bars, cums, posmap):
    bb = bars["BTC"]
    r1 = bb["close"] / bb["close"].shift(1) - 1.0
    r24 = bb["close"] / bb["close"].shift(6) - 1.0
    grids = []
    for thr in (0.015, 0.025, 0.035):
        for nb in (2, 6, 12):
            grids.append((f"bar>={thr * 100:.1f}% H={nb}b", r1.index[r1 >= thr] + H4, nb, 1))
    for thr in (0.03, 0.05):
        for nb in (6, 12):
            grids.append((f"24h>={thr * 100:.0f}% H={nb}b", r24.index[r24 >= thr] + H4, nb, 1))
    grids.append(("CTRL bar<=-2.5% H=6b 空", r1.index[r1 <= -0.025] + H4, 6, -1))
    grids.append(("CTRL 24h<=-5% H=6b 空", r24.index[r24 <= -0.05] + H4, 6, -1))
    rows, store = [], {}
    for label, times, nb, side in grids:
        times = [t for t in times if TEST[0] <= t < TEST[1]]
        trades = []
        for s in SYMS:
            if s == "BTC":
                continue
            trades += sim_symbol(s, times, nb * H4, posmap, bars, cums, side=side)
        r = row_of(label, trades)
        if r:
            rows.append(r)
            store[label] = {"times": times, "hold": nb * H4, "side": side, "trades": trades}
    t = pd.DataFrame(rows).sort_values("事件t", ascending=False)
    print(f"\n{'=' * 110}\nF2 LEADLAG BTC 先动中盘跟（多头跟随；CTRL=空头对照，预期不成立）"
          f"\nTEST · 含 funding · fee 0.05%/边\n{'=' * 110}")
    print(t.round(3).to_string(index=False))
    return store, t


# ---------------------------------------------------------------- F5 BTC 触发组合崩盘买入
def fam_crashp(bars, cums, posmap, rats):
    """第二轮：BTC 24h 崩盘触发 -> 组合买中盘。决策时点 = 触发 bar 收盘 = E（进场 bar 开始），
    选币用 E-H4 行的自身 24h 收益（无前视：R24.loc[E-4h] 在 E 时刻已实现），
    funding 条件用 rats 在 E 时点的最近已结算费率（结算即时点已知，且 (E,X] 记账不含它）。"""
    bb = bars["BTC"]
    r24b = bb["close"] / bb["close"].shift(6) - 1.0
    alts = [s for s in SYMS if s != "BTC"]
    C = pd.DataFrame({s: bars[s]["close"] for s in alts})
    R24 = C / C.shift(6) - 1.0
    rows, store = [], {}
    for thr in (-0.03, -0.05):
        trig = [t for t in (r24b.index[r24b <= thr] + H4) if TEST[0] <= t < TEST[1]]
        for sel_name in ("all", "worst3"):
            for fcond in ("any", "negf"):
                for nb in (6, 12):
                    sel = {s: [] for s in alts}
                    for E in trig:
                        tau = E - H4
                        if tau not in R24.index:
                            continue
                        row = R24.loc[tau].dropna()
                        if not len(row):
                            continue
                        picks = (list(row.nsmallest(3).index) if len(row) >= 3
                                 else list(row.index)) if sel_name == "worst3" else list(row.index)
                        if fcond == "negf":
                            picks = [s for s in picks
                                     if np.isfinite(rats[s].get(E, np.nan)) and rats[s].get(E, np.nan) < 0]
                        for s in picks:
                            sel[s].append(E)
                    trades = []
                    for s in alts:
                        trades += sim_symbol(s, sorted(sel[s]), nb * H4, posmap, bars, cums)
                    label = (f"CRASHP 24h<={abs(thr) * 100:.0f}% {sel_name}"
                             f" {'negf' if fcond == 'negf' else 'any '} H={nb}b")
                    r = row_of(label, trades)
                    if r:
                        rows.append(r)
                        store[label] = {"sel": sel, "hold": nb * H4, "trades": trades}
    t = pd.DataFrame(rows).sort_values("事件t", ascending=False)
    print(f"\n{'=' * 110}\nF5 CRASHP BTC 24h 崩盘触发组合买入（多头反弹 · 第二轮，动机=F2 CTRL 失败方向）"
          f"\nTEST · 含 funding · fee 0.05%/边 · 预注册门禁：事件数>=60 且 事件t>=2.0\n{'=' * 110}")
    print(t.round(3).to_string(index=False))
    return store, t


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fam", default="all", choices=["all", "f1", "f2", "f3", "f4", "f5"])
    a = ap.parse_args()

    bars, cums, rats, posmap = build_all()
    print("数据覆盖（4h futures feather）:")
    for s in SYMS:
        b = bars[s]
        n_test = len(b[(b.index >= TEST[0]) & (b.index < TEST[1])])
        print(f"  {s:5s} {str(b.index[0])[:10]} -> {str(b.index[-1])[:10]}  bars={len(b)}  TEST bars={n_test}")

    if a.fam in ("all", "f3"):
        fam_cal(bars)

    if a.fam in ("all", "f1"):
        store, t = fam_xs(bars, cums, posmap, "rev")
        cand = t[(t["事件数"] >= 120) & t["事件t"].notna()]
        for i in range(min(1, len(cand))):
            label = cand.iloc[i]["配置"]
            print(f"\n>>> XSREV 代表（预注册规则：事件t 最高且事件数>=120）: {label}")
            detail(store[label]["trades"], f"XSREV 代表: {label}")
            stress(("xs", store[label]["sel"], store[label]["hold"]), (posmap, bars, cums))

    if a.fam in ("all", "f4"):
        store, t = fam_xs(bars, cums, posmap, "mom")
        cand = t[(t["事件数"] >= 120) & t["事件t"].notna()]
        for i in range(min(1, len(cand))):
            label = cand.iloc[i]["配置"]
            print(f"\n>>> XSMOM 代表: {label}")
            detail(store[label]["trades"], f"XSMOM 代表: {label}")
            stress(("xs", store[label]["sel"], store[label]["hold"]), (posmap, bars, cums))

    if a.fam in ("all", "f2"):
        store, t = fam_leadlag(bars, cums, posmap)
        cand = t[(t["事件数"] >= 120) & t["事件t"].notna()]
        for i in range(min(1, len(cand))):
            label = cand.iloc[i]["配置"]
            cfg = store[label]
            print(f"\n>>> LEADLAG 代表: {label}")
            detail(cfg["trades"], f"LEADLAG 代表: {label}")
            stress(("lag", cfg["times"], cfg["hold"], cfg["side"]), (posmap, bars, cums))

    if a.fam in ("all", "f5"):
        store, t = fam_crashp(bars, cums, posmap, rats)
        cand = t[(t["事件数"] >= 60) & (t["事件t"] >= 2.0)]
        for i in range(min(2, len(cand))):
            label = cand.iloc[i]["配置"]
            cfg = store[label]
            print(f"\n>>> CRASHP 过门禁代表（事件数>=60 且 事件t>=2.0）: {label}")
            detail(cfg["trades"], f"CRASHP 代表: {label}")
            stress(("xs", cfg["sel"], cfg["hold"]), (posmap, bars, cums))
        if not len(cand):
            print("\n>>> CRASHP 无变体过预注册门禁（事件数>=60 且 事件t>=2.0）-> 族关闭")


if __name__ == "__main__":
    main()
