"""高离散度方向：10 轮深化检验（2026-11-04，TEST-only）。

上一轮（RESEARCH 30.2）发现：V2 交易在「9 品种 24h 收益横截面标准差」的滚动 180d
分位 >p80 时，每笔 $19.05、9/9 品种正、三年全正；硬过滤把总利润从 $6,093 砍到 $3,029。
本脚本围绕这一个结构做 10 轮压力测试，规则先写后跑：

  D1 分位单调性 + 阈值/回看窗稳健性（p50–p90 × 90/180/365d）
  D2 有效样本：高离散交易聚集在几个 episode？月份块 bootstrap + 标签置换 p 值
  D3 混杂控制：同跌幅/同 ATR 分层内的高低离散差 + 多元回归
  D4 市场 beta 分解：高离散收益是不是「抄底后市场反弹」的 beta？
  D5 替代定义：std4h / range / IQR / MAD / 下行半差 / 下跌宽度
  D6 离散度 vs 宽度：2×2 看是不是「普跌」而非「分化」在起作用
  D7 高离散信号的持有期/入场延迟衰减
  D8 时间稳定性：逐年/逐季、expanding 分位、2024 单独复核
  D9 泛化：所有周末接刀信号、OIFlushV2、以及全市场「高离散日买入」基线
  D10 组合与仓位：高离散子集 + OI 的月度指标、三档仓位规则

纪律：TEST 段 20220101–20240828；滚动分位只用信号日之前的历史；不跑 VAL；
不改在用策略源码。所有分档/阈值都是预注册的固定值，全量打印，不挑格。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import idea_round10 as base  # noqa: E402

SPLIT = base.SPLIT
FEE = base.FEE
D = {p: base.load(p) for p in base.PAIRS}
RET24 = pd.DataFrame({p: d.set_index("date")["ret24"] for p, d in D.items()})
RET4 = pd.DataFrame({p: d.set_index("date")["ret"] for p, d in D.items()})
RET24 = RET24[(RET24.index >= pd.Timestamp("2022-01-01", tz="UTC")) & (RET24.index < SPLIT)]
RET4 = RET4.reindex(RET24.index)
DISP = RET24.std(axis=1)
ATR = pd.DataFrame({p: d.set_index("date")["atr14"] / d.set_index("date")["close"]
                    for p, d in D.items()}).mean(axis=1).reindex(RET24.index)


def rpct(s, win=1080, minp=360):
    return s.rolling(win, min_periods=minp).apply(
        lambda x: (x[-1] > x[:-1]).mean(), raw=True).shift(1)


PCT = rpct(DISP)
APCT = rpct(ATR)


def v2_trades():
    from research_lib import read_result, trades_frame
    _, tr = read_result("backtest-result-2026-10-31_01-44-39.zip")
    v = trades_frame(tr)
    v = v[v["open_dt"] < SPLIT].copy()
    v["sig"] = v["open_dt"] - pd.Timedelta(hours=4)
    v["pct"] = v["sig"].map(PCT)
    v["apct"] = v["sig"].map(APCT)
    rets = {p: d.set_index("date")["ret"] for p, d in D.items()}
    v["drop"] = [rets[r.pair_base].get(r.sig - pd.Timedelta(hours=4), np.nan)
                 for r in v.itertuples()]
    return v


V = v2_trades()


def describe(g, name):
    if len(g) == 0:
        print(f"  {name}: n=0")
        return
    eq = g.sort_values("close_dt")["profit$"].cumsum()
    dd = (eq - eq.cummax()).min() if len(eq) else 0
    yrs = g.groupby(g["sig"].dt.year)["profit$"].mean()
    print(f"  {name:24s} n={len(g):3d} 均值=${g['profit$'].mean():6.2f} "
          f"合计=${g['profit$'].sum():6.0f} 胜率={(g['profit$']>0).mean()*100:4.1f}% "
          f"品种正={(g.groupby('pair_base')['profit$'].mean()>0).mean()*100:3.0f}% "
          f"顺序回撤=${dd:5.0f} 逐年=" + " ".join(f"{y}:{x:+.1f}" for y, x in yrs.items()))


def ols(y, X, names, robust=True):
    X = np.column_stack([np.ones(len(X)), X])
    names = ["const"] + list(names)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    inv = np.linalg.pinv(X.T @ X)
    if robust:
        cov = inv @ ((X * resid[:, None] ** 2).T @ X) @ inv * n / (n - k)
    else:
        cov = inv * (resid @ resid) / (n - k)
    se = np.sqrt(np.diag(cov))
    print("    " + "  ".join(f"{nm}={b:+.3f}(t={b/s:+.2f})" for nm, b, s in zip(names, beta, se)))


print("=" * 70)
print("[D1] 分位单调性 / 阈值 / 回看窗")
v = V.dropna(subset=["pct"]).copy()
dec = pd.qcut(v["pct"], 10, duplicates="drop")
t = v.groupby(dec, observed=True)["profit$"].agg(["size", "mean", "sum"])
t["win"] = v.groupby(dec, observed=True)["profit$"].apply(lambda s: (s > 0).mean() * 100)
print(t.round(1).to_string())
print("  连续回归 profit$ ~ pct:")
ols(v["profit$"].to_numpy(), v[["pct"]].to_numpy(), ["pct"])
print("  阈值敏感性:")
for q in (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9):
    hi = v[v.pct >= q]
    print(f"   p{q:.2f}: n={len(hi):3d} 均值=${hi['profit$'].mean():6.2f} "
          f"合计=${hi['profit$'].sum():6.0f} 剔20=${hi.sort_values('profit$', ascending=False).iloc[20:]['profit$'].mean():6.2f}")
print("  回看窗（高>p80）:")
for win, lab in [(540, "90d"), (1080, "180d"), (2190, "365d")]:
    p2 = rpct(DISP, win, min(200, win // 3))
    v["p2"] = v["sig"].map(p2)
    describe(v[v.p2 >= 0.8], f"lookback {lab}")

print("\n" + "=" * 70)
print("[D2] 有效样本：episode / bootstrap / 置换")
hi = v[v.pct >= 0.8].copy()
hs = PCT[PCT >= 0.8].index
ep_id, cur = [], 0
prev = None
for dt in hs:
    if prev is not None and (dt - prev) > pd.Timedelta(days=3):
        cur += 1
    ep_id.append(cur)
    prev = dt
ep = pd.Series(ep_id, index=hs)
hi["ep"] = hi["sig"].map(ep)
print(f"  高离散期共 {ep.nunique()} 个 episode；含交易的 episode: {hi['ep'].nunique()}")
e = hi.groupby("ep").agg(n=("profit$", "size"), tot=("profit$", "sum"),
                         start=("sig", "min"), end=("sig", "max"))
print(e.to_string())
base_mean = hi["profit$"].mean()
loo = []
for k in e.index:
    rest = hi[hi.ep != k]["profit$"].mean()
    loo.append((k, rest))
print(f"  leave-one-episode-out 均值范围: ${min(x for _, x in loo):.2f} ~ ${max(x for _, x in loo):.2f}"
      f"（全样本 ${base_mean:.2f}）")
rng = np.random.default_rng(42)
months = v["sig"].dt.strftime("%Y-%m")
mids = months.unique()
diffs = []
for _ in range(2000):
    pick = rng.choice(mids, len(mids), replace=True)
    idx = np.concatenate([np.flatnonzero(months.to_numpy() == m) for m in pick])
    b = v.iloc[idx]
    h, r = b[b.pct >= .8]["profit$"], b[b.pct < .8]["profit$"]
    diffs.append(h.mean() - r.mean())
diffs = np.array(diffs)
print(f"  月份块 bootstrap: 高-低 均值差=${hi['profit$'].mean()-v[v.pct<.8]['profit$'].mean():.2f} "
      f"CI=[{np.percentile(diffs,2.5):.2f}, {np.percentile(diffs,97.5):.2f}] "
      f"p(<=0)={np.mean(diffs<=0):.3f}")
perm = []
pp = v["pct"].to_numpy()
pr = v["profit$"].to_numpy()
pairs_ = v["pair_base"].to_numpy()
obs = hi["profit$"].mean() - v[v.pct < .8]["profit$"].mean()
for _ in range(2000):
    sh = pp.copy()
    for p in np.unique(pairs_):
        m = pairs_ == p
        sh[m] = rng.permutation(sh[m])
    perm.append(pr[sh >= .8].mean() - pr[sh < .8].mean())
perm = np.array(perm)
print(f"  品种内标签置换: 观测差=${obs:.2f}, 置换 p={np.mean(perm >= obs):.3f}")

print("\n" + "=" * 70)
print("[D3] 混杂：同跌幅 / 同 ATR 分层")
v["hi"] = (v.pct >= .8).astype(int)
v["drop_b"] = pd.cut(v["drop"], [-1, -.05, -.04, -.03, -.02, 1],
                     labels=["<-5%", "-5~-4%", "-4~-3%", "-3~-2%", ">-2%"])
g = v.groupby(["drop_b", "hi"], observed=True)["profit$"].agg(["size", "mean"])
print(g.round(1).to_string())
v["atr_b"] = pd.qcut(v["apct"], 3, labels=["低ATR", "中ATR", "高ATR"])
print(v.groupby(["atr_b", "hi"], observed=True)["profit$"].agg(["size", "mean"]).round(1).to_string())
print("  多元回归 profit$ ~ hi + drop + apct + 年固定效应:")
yr = pd.get_dummies(v["sig"].dt.year, prefix="y", drop_first=True).to_numpy().astype(float)
X = np.column_stack([v["hi"].to_numpy(float), v["drop"].fillna(0).to_numpy(float),
                     v["apct"].fillna(.5).to_numpy(float), yr])
ols(v["profit$"].to_numpy(), X, ["hi", "drop", "apct", "y23", "y24"])
print("  分类回归（跌幅档 + ATR 档 + 年固定效应）:")
cat = pd.concat([pd.get_dummies(v["drop_b"], prefix="d", drop_first=True),
                 pd.get_dummies(v["atr_b"], prefix="a", drop_first=True),
                 pd.get_dummies(v["sig"].dt.year, prefix="y", drop_first=True)], axis=1)
cat["hi"] = v["hi"].to_numpy(float)
ols(v["profit$"].to_numpy(), cat.to_numpy(float), list(cat.columns))

print("\n" + "=" * 70)
print("[D4] 市场 beta 分解")
opens = pd.DataFrame({p: d.set_index("date")["open"] for p, d in D.items()}).reindex(RET24.index)
basket = {}
for r in v.itertuples():
    a, b = r.open_dt, r.open_dt + pd.Timedelta(hours=24)
    if a in opens.index and b in opens.index:
        basket[r.Index] = (opens.loc[b] / opens.loc[a] - 1).mean()
v["mkt"] = pd.Series(basket)
vv = v.dropna(subset=["mkt"]).copy()
print("  分档市场 24h 收益: " + " | ".join(
    f"pct{'>p80' if k else '<=p80'}: ${g['mkt'].mean()*100:+.2f}% (n={len(g)})"
    for k, g in vv.groupby(vv.hi)))
print("  profit_ratio ~ mkt + hi + mkt:hi（HC1）:")
X = np.column_stack([vv["mkt"], vv["hi"], vv["mkt"] * vv["hi"]])
ols(vv["profit_ratio"].to_numpy(), X, ["mkt", "hi", "mkt*hi"])

print("\n" + "=" * 70)
print("[D5] 替代离散度定义（全部高>p80）")
alts = {
    "std24(基准)": DISP,
    "std4h": RET4.std(axis=1),
    "range24": RET24.max(axis=1) - RET24.min(axis=1),
    "IQR24": RET24.quantile(.75, axis=1) - RET24.quantile(.25, axis=1),
    "MAD24": (RET24.sub(RET24.median(axis=1), axis=0)).abs().median(axis=1),
    "下行半差": np.sqrt((RET24.clip(upper=0) ** 2).mean(axis=1)),
    "下跌宽度(≤-2%)": (RET24 <= -0.02).mean(axis=1),
}
for nm, s in alts.items():
    p = rpct(s)
    v[nm] = v["sig"].map(p)
    g = v[v[nm] >= .8]
    print(f"  {nm:14s} n={len(g):3d} 均值=${g['profit$'].mean():6.2f} "
          f"合计=${g['profit$'].sum():6.0f} 胜率={(g['profit$']>0).mean()*100:4.1f}% "
          f"品种正={(g.groupby('pair_base')['profit$'].mean()>0).mean()*100:3.0f}% "
          f"与基准Jaccard={len(set(g.index)&set(hi.index))/len(set(g.index)|set(hi.index)):.2f}")
print("  各定义在信号日的 Spearman 相关（基准列）:")
print(v[list(alts)].corr(method="spearman").iloc[:, [0]].round(2).to_string())

print("\n" + "=" * 70)
print("[D6] 离散度 × 下跌宽度")
v["breadth"] = v["sig"].map((RET24 <= -0.02).mean(axis=1))
v["bpct"] = v["sig"].map(rpct((RET24 <= -0.02).mean(axis=1)))
v["dhi"] = v.pct >= .8
v["bhi"] = v.bpct >= .8
print(v.groupby(["dhi", "bhi"])["profit$"].agg(["size", "mean", "sum"]).round(1).to_string())
print("  corr(disp_pct, breadth_pct) =", round(v["pct"].corr(v["bpct"], method="spearman"), 2))

print("\n" + "=" * 70)
print("[D7] 高离散信号：持有期 / 入场延迟")
sig_rows = []
for p, d in D.items():
    win = (d["dow"] >= 5) | ((d["dow"] == 0) & (d["bj_h"] <= 21))
    m = (d["ret"].shift(1) < -0.02) & (d["ret"] >= -0.02) & (d["ret"] > 0) & win
    dd = d.copy()
    dd["pct"] = dd["date"].map(PCT)
    sig_rows.append(dd.loc[m & (dd.pct >= .8), ["date", "pair"]])
HS = pd.concat(sig_rows).drop_duplicates()
print(f"  高离散信号总数 {len(HS)}")
for hold in (3, 6, 12, 18):
    for delay in (0, 1, 2):
        vals = []
        for p, d in D.items():
            d = d.reset_index(drop=True)
            pcts = d["date"].map(PCT)
            m = ((d["dow"] >= 5) | ((d["dow"] == 0) & (d["bj_h"] <= 21))) \
                & (d["ret"].shift(1) < -0.02) & (d["ret"] >= -0.02) & (d["ret"] > 0) \
                & (pcts >= .8)
            last = -1
            for i in np.flatnonzero(m.to_numpy()):
                j = i + 1 + delay + hold
                if j >= len(d) or d["date"].iloc[j] >= SPLIT or i + 1 + delay <= last:
                    continue
                if d["date"].iloc[i] < pd.Timestamp("2022-01-01", tz="UTC"):
                    continue
                r = d["open"].iloc[j] / d["open"].iloc[i + 1 + delay] - 1 - FEE
                vals.append(r)
                last = j
        vals = np.array(vals)
        if len(vals):
            tt = vals.mean() / vals.std(ddof=1) * np.sqrt(len(vals)) if len(vals) > 1 else np.nan
            print(f"  hold={hold*4:2d}h delay={delay}: n={len(vals):3d} "
                  f"均值={vals.mean()*100:+.2f}% t={tt:+.2f} 胜率={(vals>0).mean()*100:.0f}%")

print("\n" + "=" * 70)
print("[D8] 时间稳定性")
print(v.assign(yr=v.sig.dt.year).groupby(["yr", "hi"])["profit$"].agg(
    ["size", "mean", "sum"]).round(1).to_string())
print("  2024 逐季:")
q = v[v.sig.dt.year == 2024].assign(q=lambda x: x.sig.dt.quarter)
print(q.groupby(["q", "hi"])["profit$"].agg(["size", "mean", "sum"]).round(1).to_string())
pexp = DISP.expanding(min_periods=360).apply(lambda x: (x[-1] > x[:-1]).mean(), raw=True).shift(1)
v["pexp"] = v["sig"].map(pexp)
describe(v[v.pexp >= .8], "expanding p80")
print("  2024 用 2022–23 定阈值（p80 固定）:")
g24 = v[(v.sig.dt.year == 2024) & (v.pct >= .8)]
describe(g24, "2024 高离散")

print("\n" + "=" * 70)
print("[D9] 泛化")
print("  所有周末接刀信号（高 vs 低离散，24h）:")
vals = {1: [], 0: []}
for p, d in D.items():
    d = d.reset_index(drop=True)
    pcts = d["date"].map(PCT)
    m = ((d["dow"] >= 5) | ((d["dow"] == 0) & (d["bj_h"] <= 21))) \
        & (d["ret"].shift(1) < -0.02) & (d["ret"] >= -0.02) & (d["ret"] > 0)
    last = -1
    for i in np.flatnonzero(m.to_numpy()):
        j = i + 7
        if j >= len(d) or d["date"].iloc[j] >= SPLIT or i + 1 <= last:
            continue
        if d["date"].iloc[i] < pd.Timestamp("2022-01-01", tz="UTC") or pd.isna(pcts.iloc[i]):
            continue
        r = d["open"].iloc[j] / d["open"].iloc[i + 1] - 1 - FEE
        vals[int(pcts.iloc[i] >= .8)].append(r)
        last = j
for k, lab in [(1, "高离散"), (0, "低离散")]:
    a = np.array(vals[k])
    tt = a.mean() / a.std(ddof=1) * np.sqrt(len(a))
    print(f"    {lab}: n={len(a):3d} 均值={a.mean()*100:+.2f}% t={tt:+.2f} 胜率={(a>0).mean()*100:.0f}%")
print("  OIFlushV2:")
from research_lib import read_result, trades_frame  # noqa: E402
_, tr = read_result("backtest-result-2026-10-31_02-12-31.zip")
o = trades_frame(tr)
o = o[o.open_dt < SPLIT].copy()
o["sig"] = o.open_dt - pd.Timedelta(hours=4)
o["pct"] = o.sig.map(PCT)
o = o.dropna(subset=["pct"])
print(f"    n={len(o)} 高离散 n={int((o.pct>=.8).sum())}；"
      f"高均值=${o[o.pct>=.8]['profit$'].mean():.1f} 低均值=${o[o.pct<.8]['profit$'].mean():.1f}")
print("  全市场基线：所有 bar 按离散度分位的前瞻 24h 等权组合收益:")
fwd_basket = (opens.shift(-7) / opens.shift(-1) - 1).mean(axis=1) - FEE
tmp = pd.DataFrame({"pct": PCT, "fwd": fwd_basket}).dropna()
tmp = tmp[tmp.index < SPLIT - pd.Timedelta(hours=24)]
tmp["b"] = pd.qcut(tmp.pct, 5, labels=["p0-20", "p20-40", "p40-60", "p60-80", "p80-100"],
                   duplicates="drop")
print(tmp.groupby("b", observed=True)["fwd"].agg(["size", "mean", lambda s: (s > 0).mean() * 100]).round(3).to_string())

print("\n" + "=" * 70)
print("[D10] 组合与仓位")
from research_lib import monthly_series, sharpe_m  # noqa: E402
lo = pd.Timestamp("2022-01-01", tz="UTC")


def port(g, name, weight=None):
    x = g.copy()
    if weight is not None:
        x["profit$"] = x["profit$"] * weight(x)
    mth = monthly_series(x, lo, SPLIT)
    eq = mth.cumsum()
    dd = (eq - eq.cummax()).min()
    print(f"  {name:26s} 笔={len(x):3d} 合计=${x['profit$'].sum():6.0f} "
          f"月Sharpe={sharpe_m(mth):4.2f} 最差月=${mth.min():6.0f} 月回撤=${dd:6.0f} "
          f"总/回撤={x['profit$'].sum()/abs(dd) if dd else np.nan:4.2f}")


port(V, "V2 全部")
port(V[V.pct >= .8], "V2 高离散")
port(V[V.pct < .8], "V2 低离散")
port(V, "V2 三档仓位", lambda x: np.where(x.pct >= .8, 1.0, np.where(x.pct >= .5, .5, 0.0)))
port(V, "V2 两档仓位", lambda x: np.where(x.pct >= .8, 1.0, 0.0))
port(V, "V2 半仓低离散", lambda x: np.where(x.pct >= .8, 1.0, .5))
# 与 OI 合并
_, tr = read_result("backtest-result-2026-10-31_02-12-31.zip")
ot = trades_frame(tr)
ot = ot[ot.open_dt < SPLIT]
for g, lab in [(V, "V2全+OI"), (V[V.pct >= .8], "V2高+OI")]:
    a = monthly_series(g, lo, SPLIT)
    b = monthly_series(ot, lo, SPLIT)
    mth = pd.DataFrame({"V2": a, "OI": b}).fillna(0)
    eq = (mth.sum(axis=1)).cumsum()
    dd = (eq - eq.cummax()).min()
    print(f"  {lab:10s} 合计=${mth.values.sum():6.0f} 月Sharpe={sharpe_m(mth.sum(axis=1)):4.2f} "
          f"月回撤=${dd:6.0f} 相关={mth.V2.corr(mth.OI):+.2f}")
