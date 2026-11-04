"""第十一轮研究：再找 10 个可能赚钱的方向（2026-11-04，TEST-only）。

与 30/32 节不重复。10 个假设全部先写后跑，固定参数/固定邻域，不挑格：
  R1  MAX/LOTTERY   横截面：过去 7/30 天最大单根 4h 收益越高，下周越差（彩票偏好）
  R2  IVOL          横截面：30 天特质波动（对等权市场回归残差）越高，下周越差
  R3  OI/DVOL       横截面：OI 名义 / 7 天成交额（杠杆拥挤度）越高，下周越差
  R4  BTC→ALT LAG   BTC 单根 4h 涨跌 ≥1.5% 后，山寨下一根同向（滞后补涨/补跌）
  R5  PAIRS MR      ETH/BTC、SOL/ETH、BNB/ETH、XRP/ETH 对数比 z-score 均值回归
  R6  FUND SETTLE   资金费结算时点：极端正 funding 后 4/8h 做空，极端负 funding 后做多
  R7  CAPITULATION  BTC 3 天跌 ≥6/8/10% 后，等权 CORE 反弹 3/5/7 天
  R8  VWAP DEV      收盘相对 20 天 VWAP 的偏离：横截面反向；单品种 |z|≥2 反转
  R9  ALT/BTC ROT   山寨等权篮子/BTC 的 2/4/8 周动量：顺势做多或做空相对强弱
  R10 DONCHIAN      10/20/40 天唐奇安突破，固定持有 5/10/20 天（时间序列）

纪律：TEST 20220101–20240828；信号收盘确认→次根 4h 开盘进→固定持有后开盘出；
周频多空两腿各 $1,000，往返成本 0.2%；单腿事件往返 0.1%；同品种事件不重叠；
横截面同日相关用「按日聚类 t」；不跑 VAL、不改在用策略源码、不写窥视台账。
晋级门禁沿用 27.2：日数≥60、按日均值≥+0.5%、t日≥2.0、品种净正≥70%、
剔最赚 20 笔后仍>0、逐年为正。达不到只记录。
"""
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "user_data" / "data" / "binance" / "futures"
MET = ROOT / "user_data" / "data" / "binance" / "futures_metrics"
SPLIT = pd.Timestamp("2024-08-28", tz="UTC")
START = pd.Timestamp("2022-01-01", tz="UTC")
GRID0 = pd.Timestamp("2021-06-01", tz="UTC")
COST_XS = 0.002
COST_1 = 0.001
TOP10 = ["BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "ZEC", "DOGE", "XMR"]


def core_pairs():
    lines = io.open(ROOT / "user_data/universe/pairs_core.txt", encoding="utf-8").read().split("\n")
    return [l.strip().split("/")[0] for l in lines
            if l.strip() and not l.strip().startswith("#")]


def load_all():
    btc = pd.read_feather(DATA / "BTC_USDT_USDT-4h-futures.feather")
    grid = btc.loc[(btc["date"] >= GRID0) & (btc["date"] < SPLIT), "date"].reset_index(drop=True)
    P = {}
    for p in core_pairs():
        try:
            k = pd.read_feather(DATA / f"{p}_USDT_USDT-4h-futures.feather")
            k = k.set_index("date").reindex(grid)
            if k["close"].notna().sum() < 3000:
                continue
            m = pd.read_feather(MET / f"{p}_USDT_USDT-4h-metrics.feather")
            k["oi"] = m.set_index("date")["oi_usd"].reindex(grid)
            f = pd.read_feather(DATA / f"{p}_USDT_USDT-1h-funding_rate.feather")
            f = f[f["open"] != 0].set_index("date")["open"].sort_index()
            k["fund"] = f.reindex(grid, method="ffill")
            k["pair"] = p
            P[p] = k
        except FileNotFoundError:
            continue
    return grid, P


GRID, P = load_all()
OPEN = pd.DataFrame({p: d["open"] for p, d in P.items()}, index=GRID)
CLOSE = pd.DataFrame({p: d["close"] for p, d in P.items()}, index=GRID)
HIGH = pd.DataFrame({p: d["high"] for p, d in P.items()}, index=GRID)
LOW = pd.DataFrame({p: d["low"] for p, d in P.items()}, index=GRID)
VOL = pd.DataFrame({p: d["volume"] for p, d in P.items()}, index=GRID)
OI = pd.DataFrame({p: d["oi"] for p, d in P.items()}, index=GRID)
RET = CLOSE / CLOSE.shift(1) - 1
DVOL = VOL * CLOSE
FWD42 = OPEN.shift(-43) / OPEN.shift(-1) - 1


def fwd_h(hold):
    return OPEN.shift(-(hold + 1)) / OPEN.shift(-1) - 1


WK = [i for i in range(len(GRID))
      if GRID.iloc[i].dayofweek == 0 and GRID.iloc[i].hour == 0
      and GRID.iloc[i] >= START and i + 43 < len(GRID) and GRID.iloc[i + 43] < SPLIT]
WDATE = [GRID.iloc[i] for i in WK]
POS = {d: i for i, d in enumerate(GRID)}
print(f"池: {len(P)} 品种；周频点: {len(WK)}；区间 {GRID.iloc[0]} → {GRID.iloc[-1]}")


def tstat(s):
    return s.mean() / s.std(ddof=1) * np.sqrt(len(s)) if len(s) > 1 and s.std(ddof=1) > 0 else np.nan


def xs(score, long_low=True, sub=None, min_n=12, hold=42, cost=COST_XS, name=""):
    """周频横截面多空；long_low=True 为做多低分位。"""
    fwd = FWD42 if hold == 42 else fwd_h(hold)
    rows = []
    for i in WK:
        s = score.iloc[i].dropna()
        if sub is not None:
            s = s[[p for p in s.index if p in sub]]
        f = fwd.iloc[i]
        s = s[f.reindex(s.index).notna()]
        if len(s) < min_n:
            continue
        q = s.rank(pct=True)
        lo = s.index[q <= 0.2]
        hi = s.index[q >= 0.8]
        if len(lo) == 0 or len(hi) == 0:
            continue
        lr, hr = f[lo].mean(), f[hi].mean()
        rows.append((GRID.iloc[i], (lr - hr if long_low else hr - lr) - cost,
                     lr if long_low else hr, hr if long_low else lr, len(s)))
    return pd.DataFrame(rows, columns=["date", "spread", "leg_long", "leg_short", "n"])


def rep(name, df):
    if df is None or df.empty or len(df) < 10:
        print(f"  {name:34s} 样本不足 n={0 if df is None else len(df)}")
        return
    s = df["spread"]
    ex = s.sort_values(ascending=False).iloc[max(1, int(len(s) * .05)):].mean()
    by = df.groupby(df.date.dt.year)["spread"].mean()
    pos_years = int((by > 0).sum())
    print(f"  {name:34s} 周={len(df):3d} 均值={s.mean()*100:+.3f}% "
          f"年化≈{s.mean()*52*100:+6.1f}% t={tstat(s):+.2f} "
          f"Sharpe={s.mean()/s.std(ddof=1)*np.sqrt(52) if s.std(ddof=1) > 0 else np.nan:+.2f} "
          f"胜率={(s > 0).mean()*100:3.0f}% 剔最好5%={ex*100:+.3f}% 正年={pos_years}/{len(by)}")
    legs = (f"多头腿={df['leg_long'].mean()*100:+.3f}% "
            f"空头腿={df['leg_short'].mean()*100:+.3f}% ") if "leg_long" in df else ""
    print(f"      {legs}逐年: " + " ".join(f"{y}:{v*100:+.3f}%" for y, v in by.items()))


def events(mask_by_pair, hold, side=1, fee=COST_1):
    """mask_by_pair: {pair: bool Series}；side 可为 Series/dict 或标量。"""
    rows = []
    for p, m in mask_by_pair.items():
        op = OPEN[p].to_numpy()
        last = -1
        for i in np.flatnonzero(np.asarray(m)):
            if GRID.iloc[i] < START:
                continue
            j = i + 1 + hold
            if j >= len(GRID) or GRID.iloc[j] >= SPLIT or i + 1 <= last:
                continue
            s = side[p].iloc[i] if isinstance(side, dict) else side
            if pd.isna(s):
                continue
            r = s * (op[j] / op[i + 1] - 1) - fee
            rows.append((GRID.iloc[i], p, r))
            last = j
    return pd.DataFrame(rows, columns=["date", "pair", "ret"])


def erep(name, ev, exploratory=False):
    if ev is None or ev.empty:
        print(f"  {name:40s} 0 笔")
        return
    per_day = ev.groupby("date")["ret"].mean()
    td = tstat(per_day)
    ex = ev.sort_values("ret", ascending=False).iloc[20:]
    by = ev.groupby(ev.date.dt.year)["ret"].mean()
    breadth = (ev.groupby("pair")["ret"].mean() > 0).mean() * 100
    print(f"  {name:40s} 笔={len(ev):4d} 日={len(per_day):3d} 均值={ev.ret.mean()*100:+.3f}% "
          f"t日={td:+.2f} 胜率={(ev.ret > 0).mean()*100:3.0f}% 品种正={breadth:3.0f}% "
          f"剔20={ex.ret.mean()*100:+.3f}%")
    print(f"      逐年: " + " ".join(f"{y}:{v*100:+.3f}%" for y, v in by.items())
          + ("  [探索性]" if exploratory else ""))


print("\n" + "=" * 78)
print("[R1] MAX / 彩票偏好（横截面，周频）")
max7 = RET.rolling(42).max()
max30 = RET.rolling(180).max()
sub10 = set(TOP10)
ex10 = [p for p in P if p not in sub10]
rep("MAX7 低→多 / 高→空", xs(max7, True))
rep("MAX7 高→多 / 低→空", xs(max7, False))
rep("MAX30 低→多 / 高→空", xs(max30, True))
rep("MAX7 ex-TOP10 低→多", xs(max7, True, sub=ex10))

print("\n" + "=" * 78)
print("[R2] 特质波动 IVOL（30 天，对等权市场回归残差）")
MKT = RET.mean(axis=1)
IV = {}
for p in P:
    beta = RET[p].rolling(180).cov(MKT) / MKT.rolling(180).var()
    IV[p] = (RET[p] - beta * MKT).rolling(180).std()
IV = pd.DataFrame(IV)
RAWVOL = RET.rolling(180).std()
rep("IVOL 低→多 / 高→空", xs(IV, True))
rep("IVOL 高→多 / 低→空", xs(IV, False))
rep("原始波动 低→多 / 高→空", xs(RAWVOL, True))
rep("IVOL ex-TOP10 低→多", xs(IV, True, sub=ex10))

print("\n" + "=" * 78)
print("[R3] OI / 成交额（杠杆拥挤度，横截面，周频）")
oi_ratio = OI / DVOL.rolling(42).mean()
rep("OI/DVOL 低→多 / 高→空", xs(oi_ratio, True))
rep("OI/DVOL 高→多 / 低→空", xs(oi_ratio, False))
rep("OI/DVOL ex-TOP10 低→多", xs(oi_ratio, True, sub=ex10))

print("\n" + "=" * 78)
print("[R4] BTC → ALT 滞后（BTC 4h 大动后，山寨下一根同向）")
alts = [p for p in P if p != "BTC"]
btc_ret = RET["BTC"]
for thr in (0.015, 0.01, 0.02):
    base = btc_ret.abs() >= thr
    for lag_filter in (False, True):
        for hold in (1, 2, 3):
            masks, sides = {}, {}
            for p in alts:
                m = base & RET[p].notna()
                if lag_filter:
                    m = m & (RET[p].abs() <= 0.005)
                masks[p] = m
                sides[p] = np.sign(btc_ret)
            ev = events(masks, hold, sides)
            lab = f"BTC|{thr*100:g}%| hold{hold*4}h" + (" 山寨未动" if lag_filter else "")
            erep(lab, ev, exploratory=True)

print("\n" + "=" * 78)
print("[R5] 配对均值回归（对数比 z-score，20 天，z≥2 入场，z=0 或 5 天出场）")
RATIOS = {"ETH/BTC": ("ETH", "BTC"), "SOL/ETH": ("SOL", "ETH"),
          "BNB/ETH": ("BNB", "ETH"), "XRP/ETH": ("XRP", "ETH")}
pool5 = []
for name, (a, b) in RATIOS.items():
    if a not in P or b not in P:
        continue
    ratio = CLOSE[a] / CLOSE[b]
    lr = np.log(ratio)
    z = (lr - lr.rolling(180).mean()) / lr.rolling(180).std()
    for zthr in (2.0, 1.5):
        rows = []
        last = -1
        for i in np.flatnonzero((z.abs() >= zthr).fillna(False).to_numpy()):
            if GRID.iloc[i] < START or i + 1 <= last or i + 1 >= len(GRID) - 1:
                continue
            side = -np.sign(z.iloc[i])
            j = i + 1
            maxj = min(i + 1 + 30, len(GRID) - 2)
            if maxj <= i + 1:
                continue
            exit_j = None
            while j <= maxj:
                if side > 0 and z.iloc[j] <= 0:
                    exit_j = j
                    break
                if side < 0 and z.iloc[j] >= 0:
                    exit_j = j
                    break
                j += 1
            if exit_j is None:
                exit_j = maxj
            if exit_j + 1 >= len(GRID) or GRID.iloc[exit_j + 1] >= SPLIT:
                continue
            rr = (OPEN[a].iloc[exit_j + 1] / OPEN[a].iloc[i + 1]) \
                / (OPEN[b].iloc[exit_j + 1] / OPEN[b].iloc[i + 1]) - 1
            rows.append((GRID.iloc[i], name, side * rr - COST_XS))
            last = exit_j + 1
        ev = pd.DataFrame(rows, columns=["date", "pair", "ret"])
        erep(f"{name} z{zthr}", ev, exploratory=True)
        if zthr == 2.0:
            pool5.append(ev)
if pool5:
    erep("四组配对合并", pd.concat(pool5, ignore_index=True), exploratory=True)

print("\n" + "=" * 78)
print("[R6] 资金费结算时点（极端 funding 后 4/8h）")
for p in P:
    f = pd.read_feather(DATA / f"{p}_USDT_USDT-1h-funding_rate.feather")
    f = f[f["open"] != 0].set_index("date")["open"].sort_index()
    P[p]["fund_raw"] = f
for hold in (1, 2):
    for hi_side, lo_side, lab in [(-1, 1, "正funding空/负funding多")]:
        masks, sides = {}, {}
        for p, d in P.items():
            f = d["fund_raw"]
            q90 = f.rolling(270, min_periods=90).quantile(.9).shift(1)
            q10 = f.rolling(270, min_periods=90).quantile(.1).shift(1)
            at = pd.Series(False, index=GRID)
            side = pd.Series(np.nan, index=GRID)
            for dt in GRID[GRID.dt.hour.isin([0, 8, 16])]:
                if dt not in f.index:
                    continue
                i = POS[dt]
                if pd.notna(q90.get(dt)) and f.loc[dt] >= q90.loc[dt]:
                    at.iloc[i] = True
                    side.iloc[i] = hi_side
                elif pd.notna(q10.get(dt)) and f.loc[dt] <= q10.loc[dt]:
                    at.iloc[i] = True
                    side.iloc[i] = lo_side
            masks[p] = at
            sides[p] = side
        ev = events(masks, hold, sides)
        erep(f"结算极端 funding {hold*4}h {lab}", ev, exploratory=True)

print("\n" + "=" * 78)
print("[R7] 市场级恐慌反弹（BTC 3 天大跌后，等权 CORE 做多）")
btc3 = CLOSE["BTC"] / CLOSE["BTC"].shift(18) - 1
for thr in (-0.06, -0.08, -0.10):
    for hold in (18, 30, 42):
        mask = (btc3 <= thr).to_numpy()
        rows = []
        last = -1
        for i in np.flatnonzero(mask):
            if GRID.iloc[i] < START or i + 1 <= last:
                continue
            j = i + 1 + hold
            if j >= len(GRID) or GRID.iloc[j] >= SPLIT:
                continue
            rr = []
            for p in P:
                a, b = OPEN[p].iloc[i + 1], OPEN[p].iloc[j]
                if pd.notna(a) and pd.notna(b) and a > 0:
                    rr.append(b / a - 1)
            if not rr:
                continue
            rows.append((GRID.iloc[i], float(np.mean(rr) - COST_1)))
            last = j
        ev = pd.DataFrame(rows, columns=["date", "ret"])
        if len(ev):
            bt = []
            fb = fwd_h(hold)
            for i in range(len(GRID) - hold - 1):
                v = fb.iloc[i].mean()
                if pd.notna(v):
                    bt.append(v - COST_1)
            print(f"  BTC 3d<={thr*100:.0f}% 持有{hold*4//24}d: 事件={len(ev):2d} "
                  f"均值={ev.ret.mean()*100:+.2f}% t={tstat(ev.ret):+.2f} "
                  f"胜率={(ev.ret > 0).mean()*100:.0f}% 同期基线={np.mean(bt)*100:+.2f}% "
                  f"逐年: " + " ".join(f"{y}:{v*100:+.1f}%" for y, v in
                                       ev.groupby(ev.date.dt.year).ret.mean().items()))
        else:
            print(f"  BTC 3d<={thr*100:.0f}% 持有{hold*4//24}d: 0 事件")

print("\n" + "=" * 78)
print("[R8] 收盘相对 20 天 VWAP 偏离")
TP = (HIGH + LOW + CLOSE) / 3
VWAP = (TP * VOL).rolling(120).sum() / VOL.rolling(120).sum()
DEV = CLOSE / VWAP - 1
rep("VWAP偏离 低→多 / 高→空", xs(DEV, True))
rep("VWAP偏离 高→多 / 低→空", xs(DEV, False))
DEVZ = (DEV - DEV.rolling(180).mean()) / DEV.rolling(180).std()
for hold in (6, 12):
    masks, sides = {}, {}
    for p in P:
        masks[p] = (DEVZ[p].abs() >= 2).fillna(False)
        sides[p] = -np.sign(DEVZ[p])
    ev = events(masks, hold, sides)
    erep(f"VWAP |z|>=2 反转 hold{hold*4}h", ev, exploratory=True)

print("\n" + "=" * 78)
print("[R9] 山寨等权篮子 / BTC 轮动（2/4/8 周动量）")
alt_pairs = [p for p in P if p != "BTC"]
ALT_IDX = (1 + RET[alt_pairs]).cumprod().mean(axis=1)
RATIO = ALT_IDX / CLOSE["BTC"]
ALT_FWD = FWD42[alt_pairs].mean(axis=1)


def rot(w, reverse=False):
    mom = (RATIO / RATIO.shift(w * 42) - 1).loc[WDATE]
    spread = ALT_FWD.loc[WDATE] - FWD42["BTC"].loc[WDATE]
    side = np.sign(mom)
    if reverse:
        side = -side
    s = side * spread - COST_XS
    s = s.dropna()
    df = pd.DataFrame({"date": s.index, "spread": s.to_numpy()})
    rep(f"{w} 周动量{'（反向）' if reverse else ''}", df)


for w in (2, 4, 8):
    rot(w)
    rot(w, reverse=True)

print("\n" + "=" * 78)
print("[R10] 唐奇安突破（时间序列，固定持有）")
for win in (60, 120, 240):
    hi = CLOSE.rolling(win).max().shift(1)
    lo = CLOSE.rolling(win).min().shift(1)
    for hold in (30, 60, 120):
        masks, sides = {}, {}
        for p in P:
            up = CLOSE[p] > hi[p]
            dn = CLOSE[p] < lo[p]
            masks[p] = (up | dn).fillna(False)
            sides[p] = np.where(up, 1.0, np.where(dn, -1.0, np.nan))
            sides[p] = pd.Series(sides[p], index=GRID)
        ev = events(masks, hold, sides)
        erep(f"通道{win*4//24}d 持有{hold*4//24}d", ev, exploratory=True)
        if not ev.empty:
            # 多空拆分：重新按方向统计
            long_rows, short_rows = [], []
            for p in P:
                m = masks[p].to_numpy()
                op = OPEN[p].to_numpy()
                last = -1
                for i in np.flatnonzero(m):
                    if GRID.iloc[i] < START:
                        continue
                    j = i + 1 + hold
                    if j >= len(GRID) or GRID.iloc[j] >= SPLIT or i + 1 <= last:
                        continue
                    s = sides[p].iloc[i]
                    if pd.isna(s):
                        continue
                    pnl = s * (op[j] / op[i + 1] - 1) - COST_1
                    (long_rows if s > 0 else short_rows).append(pnl)
                    last = j
            if long_rows and short_rows:
                print(f"      多腿 n={len(long_rows):4d} 均值={np.mean(long_rows)*100:+.3f}% | "
                      f"空腿 n={len(short_rows):4d} 均值={np.mean(short_rows)*100:+.3f}%")

print("\n完成。以上全部 TEST-only；未跑 VAL、未改策略源码。")
