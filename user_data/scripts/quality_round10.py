"""更高质量数据方向 10 轮（2026-11-04，TEST-only）。

数据（fetch_quality_data.py 抓取）：
  - CFTC COT TFF：CME 全尺寸 BTC/ETH 的 asset manager / leveraged money 多空占 OI 百分比
  - Binance premiumIndexKlines 日线：真实永续溢价指数（相对现货指数）
  - CME 期货日线（BTC=F/ETH=F）：CME 基差与隔夜 lead-lag

10 个预注册假设：
  C1 COT 资产管理人净多占比分位：高→多、低→空（BTC，5/10d）
  C2 COT 杠杆基金净空占比分位：高→空、低→多（反向，BTC，5/10d）
  C3 COT 资产管理人净持仓周变化 z：增→多、减→空（BTC，5/10d）
  C4 COT 杠杆基金净持仓周变化 z：增→空、减→多（反向，BTC，5/10d）
  C5 COT OI 4 周变化 − 价格 4 周变化：OI 增/价跌→多，OI 减/价涨→空（BTC）
  C6 ETH COT：资产管理人净多分位（多/空）；杠杆基金净空分位（反向）（ETH）
  C7 真实溢价指数 30d 均值 z：高→空、低→多（BTC/ETH，5/10d）
  C8 溢价指数 7d 变化 z：升→多、降→空（BTC/ETH，5/10d）
  C9 CME 基差 z：CME 溢价高→空、低→多（BTC，5/10d）
  C10 CME 隔夜 lead-lag：CME BTC 日收益 ±1% → Binance BTC 次日同向（1/3d）

纪律：TEST 20220101–20240828；COT 用报告日+4 天作为可用日，再取严格早于信号日的
最近值；premium/CME 用前一日值；信号当日 00:00 UTC 开盘执行；成本单边 0.1%/次；
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
G0 = pd.Timestamp("2021-06-01", tz="UTC")
COST = 0.001


def core_pairs():
    lines = io.open(ROOT / "user_data/universe/pairs_core.txt", encoding="utf-8").read().split("\n")
    return [l.strip().split("/")[0] for l in lines
            if l.strip() and not l.strip().startswith("#")]


def load_crypto():
    btc = pd.read_feather(DATA / "BTC_USDT_USDT-4h-futures.feather")
    grid = btc.loc[(btc["date"] >= G0) & (btc["date"] < SPLIT), "date"]
    g0 = grid[grid.dt.hour == 0].reset_index(drop=True)
    g20 = grid[grid.dt.hour == 20].reset_index(drop=True)
    opens, closes = {}, {}
    for p in core_pairs():
        try:
            k = pd.read_feather(DATA / f"{p}_USDT_USDT-4h-futures.feather")
            k = k.set_index("date")
            k0 = k.reindex(g0)
            k20 = k.reindex(g20)
            if k0["close"].notna().sum() < 800:
                continue
            opens[p] = k0["open"]
            closes[p] = k20["close"]
        except FileNotFoundError:
            continue
    O = pd.DataFrame(opens, index=g0)
    C20 = pd.DataFrame(closes, index=g20)
    O.index = O.index.tz_localize(None)
    C20.index = C20.index.tz_localize(None)
    return O, C20


DOPEN, DCLOSE20 = load_crypto()
DIDX = DOPEN.index
RET = DOPEN.shift(-1) / DOPEN - 1
BRET = RET.mean(axis=1)
BIDX = (1 + BRET.fillna(0)).cumprod()
BTC, ETH = DOPEN["BTC"], DOPEN["ETH"]
MON = DIDX.dayofweek == 0
print(f"日频: {len(DIDX)} 天 {DIDX[0].date()} → {DIDX[-1].date()}；品种 {DOPEN.shape[1]}")


def align_daily(df, cols, datecol="date"):
    x = df[[datecol] + cols].dropna(subset=cols, how="all").sort_values(datecol).copy()
    x[datecol] = x[datecol].astype("datetime64[us]")
    base = pd.DataFrame({"date": DIDX.astype("datetime64[us]")})
    out = pd.merge_asof(base, x, left_on="date", right_on=datecol,
                        direction="backward", allow_exact_matches=False)
    return out.set_index("date")[cols]


def zs(s, win=180, minp=120):
    m = s.rolling(win, min_periods=minp).mean()
    sd = s.rolling(win, min_periods=minp).std()
    return (s - m) / sd


def pct(s, win=104, minp=52):
    return s.rolling(win, min_periods=minp).apply(
        lambda x: (x[-1] > x[:-1]).mean(), raw=True)


def simulate(sig, hold, price, ret, name):
    sig = sig.reindex(DIDX).fillna(0)
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
                daily.iloc[i] -= COST
                trades.append(s * (price.iloc[end] / price.iloc[i] - 1) - COST)
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
    print(f"  {name:40s} 笔={len(trades):3d} 胜={(tr > 0).mean()*100:3.0f}% "
          f"总=${daily.sum()*1000:6.0f} 年化={daily.mean()*365*100:+6.1f}% "
          f"月Sharpe={sharpe:+5.2f} 月t={t:+5.2f} 回撤=${dd*1000:6.0f} "
          f"年=" + " ".join(f"{y}:{v*1000:+.0f}" for y, v in by.items()))


def base(name, mkt="BTC"):
    r = (RET[mkt] if mkt in ("BTC", "ETH") else BRET).dropna()
    r = r[r.index >= pd.Timestamp("2022-01-01")]
    eq = (1 + r).cumprod()
    print(f"  {name:40s} 基线 总={eq.iloc[-1]-1:+.1%} "
          f"年化={(eq.iloc[-1]**(365/len(r))-1):+.1%} "
          f"Sharpe={r.mean()/r.std(ddof=1)*np.sqrt(365):+.2f} "
          f"回撤={(eq/eq.cummax()-1).min():.1%}")


# ---------- COT ----------
cot = pd.read_csv(EXT / "cftc_cot.csv", parse_dates=["date", "avail_date"])
cot["am_net"] = cot["am_long_pct"] - cot["am_short_pct"]
cot["lev_net"] = cot["lev_long_pct"] - cot["lev_short_pct"]


def cot_panel(asset):
    x = cot[cot.asset == asset].sort_values("date").copy()
    x["am_pct"] = pct(x["am_net"])
    x["lev_pct"] = pct(x["lev_net"])
    x["am_chg"] = x["am_net"].diff()
    x["lev_chg"] = x["lev_net"].diff()
    x["am_chg_z"] = zs(x["am_chg"], win=52, minp=26)
    x["lev_chg_z"] = zs(x["lev_chg"], win=52, minp=26)
    x["oi4"] = x["oi"] / x["oi"].shift(4) - 1
    x["oi4_z"] = zs(x["oi4"], win=104, minp=52)
    return x


CB = cot_panel("BTC")
CE = cot_panel("ETH")
AM_PCT_B = align_daily(CB, ["am_pct"], "avail_date")["am_pct"]
LEV_PCT_B = align_daily(CB, ["lev_pct"], "avail_date")["lev_pct"]
AM_CHG_B = align_daily(CB, ["am_chg_z"], "avail_date")["am_chg_z"]
LEV_CHG_B = align_daily(CB, ["lev_chg_z"], "avail_date")["lev_chg_z"]
OI4_B = align_daily(CB, ["oi4_z"], "avail_date")["oi4_z"]
AM_PCT_E = align_daily(CE, ["am_pct"], "avail_date")["am_pct"]
LEV_PCT_E = align_daily(CE, ["lev_pct"], "avail_date")["lev_pct"]


def monday_sig(mask_long, mask_short):
    s = pd.Series(0, index=DIDX)
    s[mask_long & MON] = 1
    s[mask_short & MON] = -1
    return s


print("\n" + "=" * 82)
print("[C1] COT 资产管理人净多占比分位（BTC）")
for th in (0.8, 0.7):
    sig = monday_sig(AM_PCT_B >= th, AM_PCT_B <= 1 - th)
    for hold in (5, 10):
        simulate(sig, hold, BTC, RET["BTC"], f"AM净多 p{int(th*100)}/{int((1-th)*100)} 持{hold}d")
print("  反向（极端多→空）:")
for th in (0.8,):
    sig = monday_sig(AM_PCT_B <= 1 - th, AM_PCT_B >= th)
    for hold in (5, 10):
        simulate(sig, hold, BTC, RET["BTC"], f"AM反向 p{int(th*100)} 持{hold}d")

print("\n" + "=" * 82)
print("[C2] COT 杠杆基金净持仓分位（BTC，反向）")
for th in (0.8, 0.7):
    sig = monday_sig(LEV_PCT_B <= 1 - th, LEV_PCT_B >= th)
    for hold in (5, 10):
        simulate(sig, hold, BTC, RET["BTC"], f"LEV反向 p{int(th*100)} 持{hold}d")
print("  顺势（净多高→多）:")
for th in (0.8,):
    sig = monday_sig(LEV_PCT_B >= th, LEV_PCT_B <= 1 - th)
    for hold in (5, 10):
        simulate(sig, hold, BTC, RET["BTC"], f"LEV顺势 p{int(th*100)} 持{hold}d")

print("\n" + "=" * 82)
print("[C3] COT 资产管理人净持仓周变化 z（BTC）")
for th in (1.0, 0.5):
    sig = monday_sig(AM_CHG_B >= th, AM_CHG_B <= -th)
    for hold in (5, 10):
        simulate(sig, hold, BTC, RET["BTC"], f"AM变化 z{th} 持{hold}d")

print("\n" + "=" * 82)
print("[C4] COT 杠杆基金净持仓周变化 z（BTC，反向）")
for th in (1.0, 0.5):
    sig = monday_sig(LEV_CHG_B <= -th, LEV_CHG_B >= th)
    for hold in (5, 10):
        simulate(sig, hold, BTC, RET["BTC"], f"LEV变化反向 z{th} 持{hold}d")

print("\n" + "=" * 82)
print("[C5] COT OI 4 周变化 − 价格 4 周变化（BTC）")
px4 = BTC / BTC.shift(20) - 1
PX4_Z = zs(px4)
DIV = OI4_B - PX4_Z
for th in (1.0, 0.5):
    sig = monday_sig(DIV >= th, DIV <= -th)
    for hold in (10, 20):
        simulate(sig, hold, BTC, RET["BTC"], f"OI−价 z差 {th} 持{hold}d")

print("\n" + "=" * 82)
print("[C6] ETH COT")
for th in (0.8, 0.7):
    sig = monday_sig(AM_PCT_E >= th, AM_PCT_E <= 1 - th)
    for hold in (5, 10):
        simulate(sig, hold, ETH, RET["ETH"], f"ETH AM净多 p{int(th*100)} 持{hold}d")
for th in (0.8,):
    sig = monday_sig(LEV_PCT_E <= 1 - th, LEV_PCT_E >= th)
    for hold in (5, 10):
        simulate(sig, hold, ETH, RET["ETH"], f"ETH LEV反向 p{int(th*100)} 持{hold}d")

# ---------- 真实溢价指数 ----------
PB = pd.read_csv(EXT / "premium_index_btc.csv", parse_dates=["date"])
PE = pd.read_csv(EXT / "premium_index_eth.csv", parse_dates=["date"])
PREM_B = align_daily(PB, ["close"])["close"].ffill()
PREM_E = align_daily(PE, ["close"])["close"].ffill()
prem30_b = PREM_B.rolling(30).mean()
prem30_e = PREM_E.rolling(30).mean()
z_b = zs(prem30_b)
z_e = zs(prem30_e)
chg_b = zs(prem30_b - prem30_b.shift(7))
chg_e = zs(prem30_e - prem30_e.shift(7))

print("\n" + "=" * 82)
print("[C7] 真实溢价指数 30d 均值 z（高→空、低→多）")
for z, price, ret, lab in [(z_b, BTC, RET["BTC"], "BTC"), (z_e, ETH, RET["ETH"], "ETH")]:
    sig = pd.Series(0, index=DIDX)
    sig[z >= 1] = -1
    sig[z <= -1] = 1
    for hold in (5, 10):
        simulate(sig, hold, price, ret, f"{lab} 溢价z 反向 持{hold}d")
    sig2 = -sig
    for hold in (5, 10):
        simulate(sig2, hold, price, ret, f"{lab} 溢价z 顺势 持{hold}d")

print("\n" + "=" * 82)
print("[C8] 溢价指数 7d 变化 z（升→多、降→空）")
for z, price, ret, lab in [(chg_b, BTC, RET["BTC"], "BTC"), (chg_e, ETH, RET["ETH"], "ETH")]:
    sig = pd.Series(0, index=DIDX)
    sig[z >= 1] = 1
    sig[z <= -1] = -1
    for hold in (5, 10):
        simulate(sig, hold, price, ret, f"{lab} 溢价动量 持{hold}d")

# ---------- CME ----------
CME = pd.read_csv(EXT / "cme_futures.csv", parse_dates=["date"])
# CME 基差：同日 CME 收盘 / Binance 20:00 收盘
bc = pd.DataFrame({"date": DCLOSE20.index.normalize(),
                   "bin_close": DCLOSE20["BTC"].to_numpy()})
cm = CME[["date", "btc_cme"]].merge(bc, on="date", how="inner")
cm["basis"] = cm["btc_cme"] / cm["bin_close"] - 1
cm["ret1"] = cm["btc_cme"] / cm["btc_cme"].shift(1) - 1
BASIS_CME = align_daily(cm, ["basis"])["basis"].ffill()
CME_RET = align_daily(cm, ["ret1"])["ret1"].ffill()
zb = zs(BASIS_CME)

print("\n" + "=" * 82)
print("[C9] CME 基差 z（高→空、低→多，BTC）")
sig = pd.Series(0, index=DIDX)
sig[zb >= 1] = -1
sig[zb <= -1] = 1
for hold in (5, 10):
    simulate(sig, hold, BTC, RET["BTC"], f"CME基差反向 持{hold}d")
sig2 = -sig
for hold in (5, 10):
    simulate(sig2, hold, BTC, RET["BTC"], f"CME基差顺势 持{hold}d")

print("\n" + "=" * 82)
print("[C10] CME 隔夜 lead-lag（BTC）")
for th in (0.01, 0.005):
    sig = pd.Series(0, index=DIDX)
    sig[CME_RET >= th] = 1
    sig[CME_RET <= -th] = -1
    for hold in (1, 3):
        simulate(sig, hold, BTC, RET["BTC"], f"CME ±{th*100:g}% 持{hold}d")

print("\n  基线:")
base("BTC", "BTC")
base("ETH", "ETH")
base("CORE", "CORE")


# ---------- 事后稳健性：只查最接近门禁的格子 ----------
def daily_series(sig, hold, ret):
    sig = sig.reindex(DIDX).fillna(0)
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
                daily.iloc[i] -= COST
                trades.append((DIDX[i], s))
                i = end
                continue
        i += 1
    return daily, trades


def mstat(d):
    m = d.groupby(d.index.to_period("M")).sum().reindex(
        pd.period_range("2022-01", "2024-08", freq="M"), fill_value=0.0)
    sd = m.std(ddof=1)
    return m, m.mean() / sd * np.sqrt(len(m)) if sd > 0 else np.nan


def diag(sig, hold, price, ret, mkt_ret, name):
    d, trades = daily_series(sig, hold, ret)
    m, t = mstat(d)
    m3 = m.drop(m.nlargest(3).index)
    t3 = m3.mean() / m3.std(ddof=1) * np.sqrt(len(m3))
    mm = mkt_ret.reindex(d.index).fillna(0).to_numpy()
    dd = d.to_numpy()
    X = np.column_stack([np.ones(len(mm)), mm])
    b = np.linalg.lstsq(X, dd, rcond=None)[0]
    resid = dd - X @ b
    se = np.sqrt(np.sum(resid ** 2) / (len(mm) - 2) * np.linalg.inv(X.T @ X)[0, 0])
    beta, t_alpha = b[1], b[0] / se
    nlong = sum(1 for _, s in trades if s > 0)
    nshort = len(trades) - nlong
    print(f"  {name:40s} 月t={t:+5.2f} 剔最好3月t={t3:+5.2f} beta={beta:+5.2f} "
          f"alpha年化={b[0]*365*100:+6.1f}% alpha_t={t_alpha:+5.2f} "
          f"多/空={nlong}/{nshort} 年均=" + " ".join(
              f"{y}:{v*1000:+.0f}" for y, v in d.groupby(d.index.year).sum().items()))


print("\n" + "=" * 82)
print("[稳健性] 最接近门禁的格子（事后，不计入 10 轮预注册）")
diag(monday_sig(LEV_CHG_B <= -0.5, LEV_CHG_B >= 0.5), 5, BTC, RET["BTC"], RET["BTC"],
     "C4 LEV变化反向 z0.5 持5d")
diag(monday_sig(LEV_CHG_B <= -0.5, LEV_CHG_B >= 0.5), 10, BTC, RET["BTC"], RET["BTC"],
     "C4 LEV变化反向 z0.5 持10d")
diag(monday_sig(LEV_CHG_B <= -1.0, LEV_CHG_B >= 1.0), 5, BTC, RET["BTC"], RET["BTC"],
     "C4 LEV变化反向 z1.0 持5d")
diag(monday_sig(LEV_CHG_B <= -1.0, LEV_CHG_B >= 1.0), 10, BTC, RET["BTC"], RET["BTC"],
     "C4 LEV变化反向 z1.0 持10d")
sig_c9 = pd.Series(0, index=DIDX)
sig_c9[zb >= 1] = 1
sig_c9[zb <= -1] = -1
diag(sig_c9, 5, BTC, RET["BTC"], RET["BTC"], "C9 CME基差顺势 持5d")
diag(sig_c9, 10, BTC, RET["BTC"], RET["BTC"], "C9 CME基差顺势 持10d")
sig_c7e = pd.Series(0, index=DIDX)
sig_c7e[z_e >= 1] = 1
sig_c7e[z_e <= -1] = -1
diag(sig_c7e, 5, ETH, RET["ETH"], RET["ETH"], "C7 ETH溢价顺势 持5d")
diag(sig_c7e, 10, ETH, RET["ETH"], RET["ETH"], "C7 ETH溢价顺势 持10d")

# 多空腿拆分（C4 与 C9）
print("\n  多空腿拆分:")
for lab, s, h, pr, rt in [
        ("C4 z0.5 5d", monday_sig(LEV_CHG_B <= -0.5, LEV_CHG_B >= 0.5), 5, BTC, RET["BTC"]),
        ("C4 z1.0 5d", monday_sig(LEV_CHG_B <= -1.0, LEV_CHG_B >= 1.0), 5, BTC, RET["BTC"]),
        ("C9 顺势 5d", sig_c9, 5, BTC, RET["BTC"]),
        ("C7 ETH 顺势 5d", sig_c7e, 5, ETH, RET["ETH"])]:
    for side, nm in ((1, "多"), (-1, "空")):
        s2 = s.where(s == side, 0)
        d, tr = daily_series(s2, h, rt)
        m, t = mstat(d)
        print(f"  {lab:16s} {nm}腿 笔={len(tr):3d} 总=${d.sum()*1000:6.0f} 月t={t:+5.2f} "
              f"年=" + " ".join(f"{y}:{v*1000:+.0f}" for y, v in d.groupby(d.index.year).sum().items()))

print("\n完成。全部 TEST-only；未跑 VAL、未改策略源码。")
