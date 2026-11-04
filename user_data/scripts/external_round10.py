"""外部策略学习后的 10 轮验证（2026-11-04，TEST-only，CORE50 广度池）。

外部线索（详见 RESEARCH 32 节）：
  1. 2026 论文「Reversal in Cryptocurrency Returns」：8–10 周 formation 的横截面反转，
     周频 long losers / short winners，高波动条件下 Sharpe 1.19–1.35（t 至 3.31），
     剔除大市值后 1.38；小币/流动性溢价是主要载体。
  2. 「Cross-Sectional Trend in Cryptocurrency Markets」(Zenodo 2026)：Elastic-Net 29 个技术
     指标，周频多空 3.82%/周、Sharpe 4.54；前 15 个系数 12 个为负（短期反转），
     trending regime Sharpe≈1、dispersive regime >7。
  3. 2025–26 文献共识：funding carry 的 Sharpe 从 6.45（2020–25）降到 4.06（2024 起），
     2025 转负；简单 spot-perp carry 不再值得做，资金费更适合当横截面情绪因子。
  4. TSMOM 在 2020–2025 优于横截面动量（31.96% vs 14.59% 年化），波动率缩放进一步提升 Sharpe。
  5. 当下行情（外部资料，2026-10 末）：BTC $77–81k，较 2025-10 高点 $126k 回撤 35–40%，
     6 月低点 $59k，9 月假突破后二次下行；funding 仍温和为正（~0.01%/8h），OI 从高位
     缩水（~$53B），ETF 净流出，长仓被清洗——熊市/震荡去杠杆，不是资金费 carry 环境。

纪律：TEST 20220101–20240828；横截面策略需要广度，主口径用 CORE50 中有 2021-06 起
连续数据的 26 个品种（TOP10 只有 9 个，分位统计力不足，列为参照）；周频信号在周一
00:00 bar 收盘确认、04:00 开盘进、持有 7 天；long/short 各 $1,000，往返成本按单边
0.05% × 两腿 × 两次 = 0.2%（每笔 spread 扣 0.2%）；不跑 VAL，不改在用策略源码。
"""
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "user_data" / "data" / "binance" / "futures"
MET = ROOT / "user_data" / "data" / "binance" / "futures_metrics"
SPLIT = pd.Timestamp("2024-08-28", tz="UTC")
START = pd.Timestamp("2022-01-01", tz="UTC")
COST = 0.002
TOP10 = ["BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "ZEC", "DOGE", "XMR"]


def core_pairs():
    lines = io.open(ROOT / "user_data/universe/pairs_core.txt", encoding="utf-8").read().split("\n")
    return [l.strip().split("/")[0] for l in lines if l.strip() and not l.strip().startswith("#")]


def load_all():
    btc = pd.read_feather(DATA / "BTC_USDT_USDT-4h-futures.feather")
    grid = btc.loc[(btc["date"] >= pd.Timestamp("2021-06-01", tz="UTC"))
                   & (btc["date"] < SPLIT), "date"].reset_index(drop=True)
    P = {}
    for p in core_pairs():
        k = pd.read_feather(DATA / f"{p}_USDT_USDT-4h-futures.feather")
        k = k.set_index("date").reindex(grid)
        if k["close"].notna().sum() < 3000:
            continue
        k["pair"] = p
        k["dvol"] = k["volume"] * k["close"]
        m = pd.read_feather(MET / f"{p}_USDT_USDT-4h-metrics.feather")
        m = m.set_index("date").reindex(grid)
        k["oi"] = m["oi_usd"]
        mk = pd.read_feather(DATA / f"{p}_USDT_USDT-1h-mark.feather")
        mk = mk.set_index("date")["close"].reindex(grid, method="ffill")
        k["mark"] = mk
        f = pd.read_feather(DATA / f"{p}_USDT_USDT-1h-funding_rate.feather")
        f = f[f["open"] != 0].set_index("date")["open"]
        k["fund"] = f.reindex(grid, method="ffill")
        P[p] = k
    return grid, P


GRID, P = load_all()
OPEN = pd.DataFrame({p: d["open"] for p, d in P.items()}, index=GRID)
CLOSE = pd.DataFrame({p: d["close"] for p, d in P.items()}, index=GRID)
DVOL = pd.DataFrame({p: d["dvol"] for p, d in P.items()}, index=GRID)
RET = CLOSE / CLOSE.shift(1) - 1
FWD = OPEN.shift(-43) / OPEN.shift(-1) - 1          # 周一 04:00 进 → 7 天后 04:00 出
FUND = pd.DataFrame({p: d["fund"] for p, d in P.items()}, index=GRID)
BASIS = pd.DataFrame({p: d["mark"] / d["close"] - 1 for p, d in P.items()}, index=GRID)
OI = pd.DataFrame({p: d["oi"] for p, d in P.items()}, index=GRID)

WK = [i for i in range(len(GRID))
      if GRID.iloc[i].dayofweek == 0 and GRID.iloc[i].hour == 0
      and GRID.iloc[i] >= START and i + 43 < len(GRID) and GRID.iloc[i + 43] < SPLIT]
WDATE = [GRID.iloc[i] for i in WK]
print(f"池: {len(P)} 品种；周频点: {len(WK)}")

# 市场状态（信号日已知）
MKT_PAST = (CLOSE / CLOSE.shift(42) - 1).mean(axis=1).loc[WDATE]
VOL8 = MKT_PAST.rolling(8).std()
VOLPCT = VOL8.rolling(52).apply(lambda x: (x[-1] > x[:-1]).mean(), raw=True).shift(1)
BTC_MA = CLOSE["BTC"].rolling(1200).mean()
REGIME = (CLOSE["BTC"] > BTC_MA).loc[WDATE]
WKRET = (CLOSE / CLOSE.shift(42) - 1).loc[WDATE]
WDISP = WKRET.std(axis=1)
DPCT = WDISP.rolling(52).apply(lambda x: (x[-1] > x[:-1]).mean(), raw=True).shift(1)
print(f"高波动周占比: {(VOLPCT >= .5).mean():.2f}；BTC>200d 占比: {REGIME.mean():.2f}")


def xs(score, long_low=True, cond=None, k_frac=0.2, min_n=12, sub=None):
    """周频横截面多空。score 在被排序行上；long_low=True 表示做多低分位。"""
    rows = []
    for r in WK:
        if cond is not None and not bool(cond.get(GRID.iloc[r], False)):
            continue
        s = score.iloc[r].dropna()
        f = FWD.iloc[r]
        if sub is not None:
            s = s[[p for p in s.index if p in sub]]
        s = s[f.reindex(s.index).notna()]
        if len(s) < min_n:
            continue
        q = s.rank(pct=True)
        lo = s.index[q <= k_frac]
        hi = s.index[q >= 1 - k_frac]
        if len(lo) == 0 or len(hi) == 0:
            continue
        lr = f[lo].mean()
        hr = f[hi].mean()
        rows.append((GRID.iloc[r], lr, hr, (lr - hr if long_low else hr - lr) - COST, len(s),
                     lr if long_low else hr, hr if long_low else lr))
    return pd.DataFrame(rows, columns=["date", "long_leg", "short_leg", "spread",
                                       "n", "leg_long", "leg_short"])


def rep(name, df, ann=True):
    if df.empty or len(df) < 10:
        print(f"  {name}: 样本不足 (n={len(df)})")
        return
    s = df["spread"]
    t = s.mean() / s.std(ddof=1) * np.sqrt(len(s))
    sr = s.mean() / s.std(ddof=1) * np.sqrt(52) if s.std(ddof=1) > 0 else np.nan
    ex = s.sort_values(ascending=False).iloc[max(1, int(len(s) * .05)):].mean()
    by = df.groupby(df.date.dt.year)["spread"].mean()
    print(f"  {name:34s} 周={len(df):3d} 均值={s.mean()*100:+.3f}% "
          f"年化≈{s.mean()*52*100:+6.1f}% t={t:+.2f} Sharpe={sr:+.2f} "
          f"胜率={(s>0).mean()*100:3.0f}% 剔最好5%周={ex*100:+.3f}% "
          f"平均池={df['n'].mean():.0f}")
    print(f"      多头腿={df['leg_long'].mean()*100:+.3f}% 空头腿={df['leg_short'].mean()*100:+.3f}% "
          f"逐年: " + " ".join(f"{y}:{v*100:+.3f}%" for y, v in by.items()))


def zscore(s):
    sd = s.std(ddof=1)
    return (s - s.mean()) / sd if sd > 0 else s * 0


def volcond(thr=0.5):
    return pd.Series((VOLPCT >= thr).to_numpy(), index=WDATE)


def regime_cond(above=True):
    return pd.Series((REGIME == above).to_numpy(), index=WDATE)


print("\n" + "=" * 74)
print("[E1] 8 周横截面反转（外部论文主形态）")
form8 = CLOSE / CLOSE.shift(8 * 42) - 1
rep("CORE 全部", xs(form8, long_low=True))
rep("CORE 高波动周", xs(form8, long_low=True, cond=volcond(.5)))
rep("CORE 低波动周", xs(form8, long_low=True, cond=volcond(.5).apply(lambda x: not x)))
sub10 = set(TOP10)
rep("TOP10 全部", xs(form8, long_low=True, min_n=7, sub=sub10))
rep("CORE 剔除 TOP10", xs(form8, long_low=True, sub=[p for p in P if p not in sub10]))

print("\n" + "=" * 74)
print("[E2] formation 窗口敏感性（CORE，周频，多空）")
for w in (1, 2, 3, 4, 6, 8, 10, 12):
    f = CLOSE / CLOSE.shift(w * 42) - 1
    rep(f"{w} 周 formation", xs(f, long_low=True))

print("\n" + "=" * 74)
print("[E3] 资金费横截面（过去 7 天均值；低 funding 多 / 高 funding 空）")
fund7 = FUND.rolling(42).mean()
rep("CORE funding", xs(fund7, long_low=True))
rep("CORE ex-TOP10 funding", xs(fund7, long_low=True, sub=[p for p in P if p not in sub10]))
rep("CORE 高波动周 funding", xs(fund7, long_low=True, cond=volcond(.5)))

print("\n" + "=" * 74)
print("[E4] basis 横截面（过去 7 天 mark 溢价均值；低溢价多 / 高溢价空）")
basis7 = BASIS.rolling(42).mean()
rep("CORE basis", xs(basis7, long_low=True))
rep("CORE ex-TOP10 basis", xs(basis7, long_low=True, sub=[p for p in P if p not in sub10]))

print("\n" + "=" * 74)
print("[E5] OI 7 日变化横截面（两种方向都打印）")
oi7 = OI / OI.shift(42) - 1
rep("OI 低→多 / 高→空", xs(oi7, long_low=True))
rep("OI 高→多 / 低→空", xs(oi7, long_low=False))

print("\n" + "=" * 74)
print("[E6] funding × OI 共振事件（外部实战指南的 confluence）")
FP = {}
for p, d in P.items():
    f = d["fund"]
    q90 = f.rolling(1080, min_periods=360).quantile(.9)
    q10 = f.rolling(1080, min_periods=360).quantile(.1)
    oi_chg = (d["oi"] / d["oi"].shift(6) - 1).to_numpy()
    arr_hi = (f >= q90).to_numpy()
    arr_lo = (f <= q10).to_numpy()
    FP[p] = (arr_hi, arr_lo, oi_chg)


def events(mask_fn, hold, short=False):
    rows = []
    for p, d in P.items():
        hi, lo, oi = FP[p]
        m = mask_fn(hi, lo, oi)
        last = -1
        op = OPEN[p].to_numpy()
        for i in np.flatnonzero(m):
            j = i + 1 + hold
            if GRID.iloc[i] < START or j >= len(GRID) or GRID.iloc[j] >= SPLIT or i + 1 <= last:
                continue
            r = op[j] / op[i + 1] - 1
            if short:
                r = -r
            rows.append((GRID.iloc[i], p, r - 0.001))
            last = j
    return pd.DataFrame(rows, columns=["date", "pair", "ret"])


def erep(name, ev):
    if ev.empty:
        print(f"  {name}: 0 笔")
        return
    per = ev.groupby("date")["ret"].mean()
    t = per.mean() / per.std(ddof=1) * np.sqrt(len(per)) if len(per) > 1 else np.nan
    print(f"  {name:38s} 笔={len(ev):3d} 日={len(per):3d} 均值={ev.ret.mean()*100:+.2f}% "
          f"t日={t:+.2f} 胜率={(ev.ret>0).mean()*100:.0f}% "
          f"品种正={(ev.groupby('pair').ret.mean()>0).mean()*100:.0f}%")


for h, lab in [(6, "24h"), (12, "48h")]:
    erep(f"fund>=p90 & OI+3% 空{lab}", events(lambda hi, lo, oi: hi & (oi >= .03), h, True))
    erep(f"fund<=p10 & OI-3% 多{lab}", events(lambda hi, lo, oi: lo & (oi <= -.03), h, False))
    erep(f"fund>=p90 空{lab}（单因子）", events(lambda hi, lo, oi: hi, h, True))
    erep(f"OI+3% 空{lab}（单因子）", events(lambda hi, lo, oi: oi >= .03, h, True))

print("\n" + "=" * 74)
print("[E7] 时间序列动量 + 波动率缩放（BTC/ETH）")


def tsmom(pair, look, target=None, cap=(0.3, 2.0)):
    c = CLOSE[pair]
    sig = np.sign(c / c.shift(look * 42) - 1)
    wk_ret = FWD[pair].loc[WDATE]
    sigw = sig.loc[WDATE]
    rv = RET[pair].rolling(4 * 42).std().loc[WDATE] * np.sqrt(42 * 52)  # 年化
    if target is None:
        expo = pd.Series(1.0, index=rv.index)
    else:
        expo = (target / rv).clip(*cap).fillna(0)
    r = sigw * expo * wk_ret - 0.001
    r = r.dropna()
    t = r.mean() / r.std(ddof=1) * np.sqrt(len(r))
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    ann = eq.iloc[-1] ** (52 / len(r)) - 1
    print(f"  {pair} look={look}w {'volscaled' if target else 'raw':9s} 周={len(r):3d} "
          f"年化={ann*100:+6.1f}% Sharpe={r.mean()/r.std(ddof=1)*np.sqrt(52):+.2f} "
          f"t={t:+.2f} 最大回撤={dd*100:+.1f}% 平均仓位={expo.mean():.2f}")


for pair in ("BTC", "ETH"):
    for look in (2, 3, 4, 5, 6, 8):
        tsmom(pair, look)
        tsmom(pair, look, target=0.40)

print("  --- 全池等权 TSMOM（每品种独立 sign×仓位，等权组合）---")


def tsmom_basket(look, target=None, cap=(0.3, 2.0)):
    sig = np.sign(CLOSE / CLOSE.shift(look * 42) - 1).loc[WDATE]
    rv = RET.rolling(4 * 42).std().loc[WDATE] * np.sqrt(42 * 52)
    expo = pd.Series(1.0, index=rv.index) if target is None else (target / rv).clip(*cap).fillna(0)
    contrib = sig.mul(expo, axis=0) * FWD.loc[WDATE]
    r = contrib.mean(axis=1) - 0.001
    r = r.dropna()
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    ann = eq.iloc[-1] ** (52 / len(r)) - 1
    t = r.mean() / r.std(ddof=1) * np.sqrt(len(r))
    print(f"  CORE basket look={look}w {'volscaled' if target else 'raw':9s} 周={len(r):3d} "
          f"年化={ann*100:+6.1f}% Sharpe={r.mean()/r.std(ddof=1)*np.sqrt(52):+.2f} "
          f"t={t:+.2f} 回撤={dd*100:+.1f}% 平均仓位={float(expo.mean().mean()):.2f}")


for look in (2, 4, 8):
    tsmom_basket(look)
    tsmom_basket(look, target=0.40)

print("\n" + "=" * 74)
print("[E8] 横截面反转的 regime 依赖（8 周 formation）")
rep("高波动周", xs(form8, long_low=True, cond=volcond(.5)))
rep("低波动周", xs(form8, long_low=True, cond=volcond(.5).apply(lambda x: not x)))
rep("BTC>200d", xs(form8, long_low=True, cond=regime_cond(True)))
rep("BTC<200d", xs(form8, long_low=True, cond=regime_cond(False)))
print("  横截面离散度 regime（外部论文称 dispersive regime 最强）:")
for thr in (0.5, 0.6, 0.7, 0.8):
    rep(f"高离散>p{int(thr*100)} 反转", xs(
        form8, long_low=True,
        cond=pd.Series((DPCT >= thr).to_numpy(), index=WDATE)))
rep("高离散>p80 动量（反向）", xs(
    form8, long_low=False, cond=pd.Series((DPCT >= .8).to_numpy(), index=WDATE)))
print("  高离散>p80 动量的 formation 敏感性:")
for w in (4, 6, 8, 10, 12):
    f = CLOSE / CLOSE.shift(w * 42) - 1
    rep(f"高离散>p80 动量 {w}w", xs(
        f, long_low=False, cond=pd.Series((DPCT >= .8).to_numpy(), index=WDATE)))

print("\n" + "=" * 74)
print("[E9] 流动性/规模因子（CORE ex-TOP10）")
dvol = np.log(DVOL.rolling(42).mean().replace(0, np.nan))
amihud = np.log((RET.abs() / DVOL.replace(0, np.nan)).rolling(42).mean())
rep("log 成交额 低→多 / 高→空", xs(dvol, long_low=True, sub=[p for p in P if p not in sub10]))
rep("log 成交额 高→多 / 低→空", xs(dvol, long_low=False, sub=[p for p in P if p not in sub10]))
rep("Amihud 高→多 / 低→空", xs(amihud, long_low=False, sub=[p for p in P if p not in sub10]))

print("\n" + "=" * 74)
print("[E10] 复合因子（反转 + funding + OI + basis + 流动性 z 均值）")
FEATS = {
    "rev": (form8, -1),
    "fund": (fund7, -1),
    "oi": (oi7, -1),
    "basis": (basis7, -1),
    "illiq": (amihud, +1),
}
comp = []
for nm, (f, sgn) in FEATS.items():
    z = f.apply(lambda row: zscore(row), axis=1)
    comp.append(z * sgn)
COMP = pd.concat(comp).groupby(level=0).mean()
rep("复合因子 全部", xs(COMP, long_low=False))
rep("复合因子 高波动周", xs(COMP, long_low=False, cond=volcond(.5)))
rep("复合因子 ex-TOP10", xs(COMP, long_low=False, sub=[p for p in P if p not in sub10]))
