"""新数据方向 10 轮（2026-11-04，TEST-only）。

数据（见 fetch_altdata.py / user_data/data/external/）：
  FNG 恐惧贪婪指数、稳定币总供给、Deribit DVOL、宏观日线（DXY/SPX/NDX/黄金/VIX/US10Y）、
  BTC 链上（交易数/手续费/算力/活跃地址）。

10 个预注册假设（全部市场级，不做横截面；每个只用一种新数据或两种组合）：
  A1 FNG 极端反向：FNG≤20 做多、≥80 做空；持有 5/10 天
  A2 FNG 趋势/背离：FNG 7 天跌≥20 做多、涨≥20 做空；价格跌但 FNG 升→做多，反之做空
  A3 稳定币供给：30 天总供给增长 z≥+1 做多、≤−1 做空；持有 10/20 天
  A4 稳定币×FNG：供给增长>0 且 FNG<50 做多；供给收缩且 FNG>50 做空；持有 10 天
  A5 DVOL 分位：180 天分位 ≥80% 做多、≤20% 做空；持有 5/10 天（BTC/ETH）
  A6 波动风险溢价：DVOL−30 天已实现波动 z≥+1 做空、≤−1 做多；持有 5/10 天
  A7 宏观 risk-on：−z(DXY 20d) + z(NDX 20d) − z(VIX) 的均值 ≥+1 做多、≤−1 做空
  A8 宏观隔夜 lead-lag：NDX 上一交易日 ±1% → BTC 次日同向；DXY ±0.5% → BTC 反向
  A9 链上活跃度背离：z(30d 交易数增长) − z(30d 价格增长) ≥+1 做多、≤−1 做空
  A10 链上压力：手续费 7d/90d 中位 ≥3 倍做多；算力 90d 回撤 ≥10% 做多

纪律：TEST 20220101–20240828；外部值一律用「严格早于信号日」的最近值（merge_asof
allow_exact_matches=False），信号在当日 00:00 UTC 开盘执行；成本单边 0.1%/次（往返）；
同市场持仓期间不叠新仓；月度统计按自然月（空月记 0）；不跑 VAL、不改策略源码。
"""
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "user_data" / "data" / "binance" / "futures"
EXT = ROOT / "user_data" / "data" / "external"
SPLIT = pd.Timestamp("2024-08-28", tz="UTC")
START = pd.Timestamp("2022-01-01", tz="UTC")
GRID0 = pd.Timestamp("2021-06-01", tz="UTC")
COST = 0.001


def core_pairs():
    lines = io.open(ROOT / "user_data/universe/pairs_core.txt", encoding="utf-8").read().split("\n")
    return [l.strip().split("/")[0] for l in lines
            if l.strip() and not l.strip().startswith("#")]


def load_crypto():
    btc = pd.read_feather(DATA / "BTC_USDT_USDT-4h-futures.feather")
    grid = btc.loc[(btc["date"] >= GRID0) & (btc["date"] < SPLIT), "date"]
    grid = grid[grid.dt.hour == 0].reset_index(drop=True)
    opens = {}
    for p in core_pairs():
        try:
            k = pd.read_feather(DATA / f"{p}_USDT_USDT-4h-futures.feather")
            k = k.set_index("date").reindex(grid)
            if k["close"].notna().sum() < 800:
                continue
            opens[p] = k["open"]
        except FileNotFoundError:
            continue
    O = pd.DataFrame(opens, index=grid)
    O.index = O.index.tz_localize(None)
    return O


DOPEN = load_crypto()
DIDX = DOPEN.index
RET = DOPEN.shift(-1) / DOPEN - 1
BRET = RET.mean(axis=1)
BTC = DOPEN["BTC"]
ETH = DOPEN["ETH"]
print(f"日频: {len(DIDX)} 天 {DIDX[0].date()} → {DIDX[-1].date()}；品种 {DOPEN.shape[1]}")

# 组合净值（等权日再平衡），用于篮子事件收益
BIDX = (1 + BRET.fillna(0)).cumprod()


def align(df, cols):
    x = df[["date"] + cols].dropna(subset=cols, how="all").sort_values("date").copy()
    x["date"] = x["date"].astype("datetime64[us]")
    base = pd.DataFrame({"date": DIDX.astype("datetime64[us]")})
    out = pd.merge_asof(base, x, on="date", direction="backward",
                        allow_exact_matches=False)
    return out.set_index("date")[cols]


fng = pd.read_csv(EXT / "fear_greed.csv", parse_dates=["date"])
stable = pd.read_csv(EXT / "stablecoins.csv", parse_dates=["date"])
dvol_b = pd.read_csv(EXT / "dvol_btc.csv", parse_dates=["date"])
dvol_e = pd.read_csv(EXT / "dvol_eth.csv", parse_dates=["date"])
macro = pd.read_csv(EXT / "macro.csv", parse_dates=["date"])
onch = pd.read_csv(EXT / "btc_onchain.csv", parse_dates=["date"])

FNG = align(fng, ["value"])["value"].ffill()
ST = align(stable, ["total", "usdt"])[["total", "usdt"]].ffill()
DVOL_B = align(dvol_b, ["close"])["close"].ffill()
DVOL_E = align(dvol_e, ["close"])["close"].ffill()
MAC = align(macro, ["dxy", "spx", "ndx", "gold", "vix", "us10y"]).ffill()
ON = align(onch, ["n_tx", "fees_btc", "hash_rate", "unique_addr"]).ffill()

print("对齐后非空: FNG %d, stable %d, DVOL %d, macro %d, onchain %d" % (
    FNG.notna().sum(), ST["total"].notna().sum(), DVOL_B.notna().sum(),
    MAC["dxy"].notna().sum(), ON["n_tx"].notna().sum()))


def zs(s, win=365, minp=180):
    m = s.rolling(win, min_periods=minp).mean()
    sd = s.rolling(win, min_periods=minp).std()
    return (s - m) / sd


def pct(s, win=180, minp=120):
    return s.rolling(win, min_periods=minp).apply(
        lambda x: (x[-1] > x[:-1]).mean(), raw=True)


def simulate(sig, hold, price, ret, name, cost=COST):
    """sig 在 t 收盘已知；t 日 00:00 开盘进，持有 hold 天；同市场不叠仓。"""
    sig = sig.reindex(DIDX).fillna(0)
    ret = ret.reindex(DIDX)
    daily = pd.Series(0.0, index=DIDX)
    trades = []
    i = 0
    while i < len(DIDX) - 1:
        if DIDX[i] < pd.Timestamp("2022-01-01"):
            i += 1
            continue
        s = int(sig.iloc[i])
        end = i + hold
        if s != 0 and end < len(DIDX):
            seg = ret.iloc[i:end]
            if seg.notna().sum() >= max(1, hold - 1):
                daily.iloc[i:end] += s * seg.fillna(0).to_numpy()
                daily.iloc[i] -= cost
                trades.append(s * (price.iloc[end] / price.iloc[i] - 1) - cost)
                i = end
                continue
        i += 1
    m = daily.groupby(daily.index.to_period("M")).sum()
    full = pd.period_range("2022-01", "2024-08", freq="M")
    m = m.reindex(full, fill_value=0.0)
    sd = m.std(ddof=1)
    t = m.mean() / sd * np.sqrt(len(m)) if sd > 0 else np.nan
    sharpe = m.mean() / sd * np.sqrt(12) if sd > 0 else np.nan
    eq = daily.cumsum()
    dd = (eq - eq.cummax()).min()
    by = daily.groupby(daily.index.year).sum()
    tr = np.array(trades) if trades else np.array([np.nan])
    print(f"  {name:34s} 笔={len(trades):3d} 胜率={(tr > 0).mean()*100:3.0f}% "
          f"总=${daily.sum()*1000:6.0f} 年化={daily.mean()*365*100:+6.1f}% "
          f"月Sharpe={sharpe:+5.2f} 月t={t:+5.2f} 回撤=${dd*1000:6.0f} "
          f"逐年=" + " ".join(f"{y}:{v*1000:+.0f}" for y, v in by.items()))
    return daily


def market_row(sig, hold, name, price=None, ret=None, mkt="BTC"):
    if price is None:
        price = BTC if mkt == "BTC" else (ETH if mkt == "ETH" else BIDX)
    if ret is None:
        ret = RET[mkt] if mkt in ("BTC", "ETH") else BRET
    return simulate(sig, hold, price, ret, name)


def baseline(name, mkt="BTC"):
    if mkt == "BTC":
        r = RET["BTC"].dropna()
    elif mkt == "ETH":
        r = RET["ETH"].dropna()
    else:
        r = BRET.dropna()
    r = r[(r.index >= pd.Timestamp("2022-01-01"))]
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    print(f"  {name:34s} 基线 总={eq.iloc[-1]-1:+.1%} 年化={(eq.iloc[-1]**(365/len(r))-1):+.1%} "
          f"Sharpe={r.mean()/r.std(ddof=1)*np.sqrt(365):+.2f} 回撤={dd:.1%}")


print("\n" + "=" * 80)
print("[A1] FNG 极端反向")
for lo, hi, lab in [(20, 80, "20/80"), (25, 75, "25/75")]:
    sig = pd.Series(0, index=DIDX)
    sig[FNG <= lo] = 1
    sig[FNG >= hi] = -1
    for hold in (5, 10):
        market_row(sig, hold, f"FNG {lab} 持{hold}d BTC", mkt="BTC")
        market_row(sig, hold, f"FNG {lab} 持{hold}d CORE", mkt="CORE")
print("  基线:"); baseline("BTC"); baseline("CORE", "CORE")

print("\n" + "=" * 80)
print("[A2] FNG 趋势 / 价格-FNG 背离")
chg7 = FNG - FNG.shift(7)
sig = pd.Series(0, index=DIDX)
sig[chg7 <= -20] = 1
sig[chg7 >= 20] = -1
for hold in (5, 10):
    market_row(sig, hold, f"FNG 7d 跌/涨20 持{hold}d BTC", mkt="BTC")
btc10 = BTC / BTC.shift(10) - 1
fng10 = FNG - FNG.shift(10)
sig2 = pd.Series(0, index=DIDX)
sig2[(btc10 < 0) & (fng10 > 0)] = 1
sig2[(btc10 > 0) & (fng10 < 0)] = -1
for hold in (10, 20):
    market_row(sig2, hold, f"价格跌/FNG升 持{hold}d BTC", mkt="BTC")

print("\n" + "=" * 80)
print("[A3] 稳定币总供给增长")
for col in ("total", "usdt"):
    g30 = ST[col] / ST[col].shift(30) - 1
    g7 = ST[col] / ST[col].shift(7) - 1
    for g, lab in [(g30, "30d"), (g7, "7d")]:
        z = zs(g)
        sig = pd.Series(0, index=DIDX)
        sig[z >= 1] = 1
        sig[z <= -1] = -1
        for hold in (10, 20):
            market_row(sig, hold, f"{col} {lab} z±1 持{hold}d CORE", mkt="CORE")

print("\n" + "=" * 80)
print("[A4] 稳定币 × FNG")
g30 = ST["total"] / ST["total"].shift(30) - 1
sig = pd.Series(0, index=DIDX)
sig[(g30 > 0) & (FNG < 50)] = 1
sig[(g30 < 0) & (FNG > 50)] = -1
for hold in (10, 20):
    market_row(sig, hold, f"供给增+恐惧 持{hold}d CORE", mkt="CORE")

print("\n" + "=" * 80)
print("[A5] DVOL 分位（BTC/ETH）")
for dvol, mkt, lab in [(DVOL_B, "BTC", "BTC"), (DVOL_E, "ETH", "ETH")]:
    p = pct(dvol)
    sig = pd.Series(0, index=DIDX)
    sig[p >= .8] = 1
    sig[p <= .2] = -1
    for hold in (5, 10):
        market_row(sig, hold, f"DVOL p80/p20 持{hold}d {lab}", mkt=mkt)
    sig_rev = -sig
    for hold in (5, 10):
        market_row(sig_rev, hold, f"DVOL 反向 持{hold}d {lab}", mkt=mkt)

print("\n" + "=" * 80)
print("[A6] 波动风险溢价 VRP = DVOL − RV30")
rv30 = RET["BTC"].rolling(30).std() * np.sqrt(365) * 100
vrp = DVOL_B - rv30
zv = zs(vrp)
sig = pd.Series(0, index=DIDX)
sig[zv >= 1] = -1
sig[zv <= -1] = 1
for hold in (5, 10):
    market_row(sig, hold, f"VRP z±1 持{hold}d BTC", mkt="BTC")
dchg5 = DVOL_B / DVOL_B.shift(5) - 1
sig2 = pd.Series(0, index=DIDX)
sig2[dchg5 <= -.2] = 1
sig2[dchg5 >= .2] = -1
for hold in (5, 10):
    market_row(sig2, hold, f"DVOL 5d ±20% 持{hold}d BTC", mkt="BTC")

print("\n" + "=" * 80)
print("[A7] 宏观 risk-on 组合")
dxy20 = MAC["dxy"] / MAC["dxy"].shift(20) - 1
ndx20 = MAC["ndx"] / MAC["ndx"].shift(20) - 1
comp = -zs(dxy20) + zs(ndx20) - zs(MAC["vix"])
sig = pd.Series(0, index=DIDX)
sig[comp >= 1] = 1
sig[comp <= -1] = -1
for hold in (5, 10):
    market_row(sig, hold, f"宏观组合 ±1 持{hold}d CORE", mkt="CORE")
    market_row(sig, hold, f"宏观组合 ±1 持{hold}d BTC", mkt="BTC")
print("  单因子归因（持 10d CORE）:")
for s, lab in [(-zs(dxy20), "DXY 弱"), (zs(ndx20), "NDX 强"), (-zs(MAC["vix"]), "VIX 低")]:
    sg = pd.Series(0, index=DIDX)
    sg[s >= 1] = 1
    sg[s <= -1] = -1
    market_row(sg, 10, f"{lab} 持10d CORE", mkt="CORE")

print("\n" + "=" * 80)
print("[A8] 宏观隔夜 lead-lag（BTC）")
ndx1 = MAC["ndx"] / MAC["ndx"].shift(1) - 1
dxy1 = MAC["dxy"] / MAC["dxy"].shift(1) - 1
sig = pd.Series(0, index=DIDX)
sig[ndx1 >= .01] = 1
sig[ndx1 <= -.01] = -1
for hold in (1, 3):
    market_row(sig, hold, f"NDX ±1% 持{hold}d BTC", mkt="BTC")
sig2 = pd.Series(0, index=DIDX)
sig2[dxy1 >= .005] = -1
sig2[dxy1 <= -.005] = 1
for hold in (1, 3):
    market_row(sig2, hold, f"DXY ±0.5% 持{hold}d BTC", mkt="BTC")

print("\n" + "=" * 80)
print("[A9] 链上活跃度背离（BTC）")
for col, lab in [("n_tx", "交易数"), ("unique_addr", "活跃地址")]:
    z_act = zs(ON[col] / ON[col].shift(30) - 1)
    z_px = zs(BTC / BTC.shift(30) - 1)
    div = z_act - z_px
    sig = pd.Series(0, index=DIDX)
    sig[div >= 1] = 1
    sig[div <= -1] = -1
    for hold in (10, 20):
        market_row(sig, hold, f"{lab}背离 持{hold}d BTC", mkt="BTC")

print("\n" + "=" * 80)
print("[A10] 链上压力（BTC）")
fee_ratio = ON["fees_btc"].rolling(7).mean() / ON["fees_btc"].rolling(90).median()
sig = pd.Series(0, index=DIDX)
sig[fee_ratio >= 3] = 1
for hold in (5, 10):
    market_row(sig, hold, f"手续费 3x 持{hold}d BTC", mkt="BTC")
hash_dd = ON["hash_rate"] / ON["hash_rate"].rolling(90).max() - 1
sig2 = pd.Series(0, index=DIDX)
sig2[hash_dd <= -.10] = 1
for hold in (10, 20):
    market_row(sig2, hold, f"算力回撤10% 持{hold}d BTC", mkt="BTC")

print("\n完成。全部 TEST-only；未跑 VAL、未改策略源码。")
