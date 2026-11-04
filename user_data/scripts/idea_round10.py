"""第十轮研究：10 个赚钱思路的 TEST-only 事件筛查（2026-11-04）。

纪律：
  - 只用 TEST 段 20220101–20240828；信号与出场都必须落在 TEST 内（出场越过切分即丢弃）。
  - 信号收盘确认 → 次根 4h 开盘进 → 固定持有后开盘出；往返 0.1%（双腿 0.2%）。
  - 同品种事件不重叠；跨品种同日的横截面相关用「按日聚类 t」处理（t_date 为主）。
  - 每个假设只跑预注册的主参数 + 1~2 个固定邻域，结果全量打印，不挑格。
  - 晋级门禁（沿用 27.2）：事件日数≥60、按日均值≥+0.5%、t_date≥2.0、
    品种净正≥70%、剔最赚 20 笔后仍>0、数据年份逐年为正。达不到只记录。

10 个假设（预注册，先写后跑）：
  H1 TOM        月末最后一天 00:00 UTC 做多，持 1/3/5 天（日历效应）
  H2 WKPUMPSHORT 周日 20:00 UTC 前 24h 涨≥5% → 做空 24/48h（周末冲高的镜像）
  H3 OIBUILD    24h OI +5% 且价格 |24h|≤1%（杠杆堆积无价格进展）→ 做空 24/48h
  H4 FUNDSTREAK 连续 6 次结算 funding≤0 且 24h OI 上升 → 做多 48h（空头拥挤+加仓）
  H5 ATRCAP     单根跌≥2.5×ATR14 且收在上半区 → 做多 24/48h（波动归一化接刀）
  H6 VOLCLIMAX  量≥4×20 根中位、阴线、收上半区 → 做多 24/48h（放量恐慌反转）
  H7 WICKREV    下影≥60% 振幅、振幅≥2×ATR14、收在顶部 25% → 做多 24/48h
  H8 DISTRIB    5 根内≥3 根放量阴线，首次触发 → 做空 48/72h（派发）
  H9 ETHBTC     20 日 ETH/BTC 动量≥±5% → 多 ETH 空 BTC 7 天（相对价值，探索性）
  H10 DISPFILTER V2 周末接刀信号按横截面离散度分档，看高/低离散度哪档更赚钱
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "user_data" / "data" / "binance" / "futures"
METRICS = ROOT / "user_data" / "data" / "binance" / "futures_metrics"
SPLIT = pd.Timestamp("2024-08-28", tz="UTC")
FEE = 0.001  # 单边 0.05% × 2

PAIRS = ["BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "ZEC", "DOGE", "XMR"]


def load(p):
    k = pd.read_feather(DATA / f"{p}_USDT_USDT-4h-futures.feather")
    k = k.sort_values("date").reset_index(drop=True)
    k["pair"] = p
    k["ret"] = k["close"] / k["close"].shift(1) - 1
    tr = pd.concat([(k["high"] - k["low"]),
                    (k["high"] - k["close"].shift(1)).abs(),
                    (k["low"] - k["close"].shift(1)).abs()], axis=1).max(axis=1)
    k["atr14"] = tr.rolling(14).mean()
    k["vol_med20"] = k["volume"].rolling(20).median()
    m = pd.read_feather(METRICS / f"{p}_USDT_USDT-4h-metrics.feather")
    k = k.merge(m[["date", "oi_usd", "taker_ls"]], on="date", how="left")
    k["oi_chg24"] = k["oi_usd"] / k["oi_usd"].shift(6) - 1
    k["ret24"] = k["close"] / k["close"].shift(6) - 1
    f = pd.read_feather(DATA / f"{p}_USDT_USDT-1h-funding_rate.feather")
    f = f[f["open"] != 0][["date", "open"]].rename(columns={"open": "fund"})
    f = f.sort_values("date")
    # 连续非正 funding 结算次数（8h 一次，6 次 = 48h）
    f["neg"] = (f["fund"] <= 0).astype(int)
    f["streak"] = f["neg"].groupby((f["neg"] != f["neg"].shift()).cumsum()).cumsum()
    k = pd.merge_asof(k.sort_values("date"), f.sort_values("date"),
                      on="date", direction="backward")
    k["bj_h"] = (k["date"].dt.hour + 8) % 24
    k["dow"] = k["date"].dt.dayofweek
    return k


def fwd(df, i, hold):
    """信号 bar i 收盘 → i+1 开盘进 → i+1+hold 开盘出。"""
    j = i + 1 + hold
    if j >= len(df):
        return None
    return df["open"].iloc[j] / df["open"].iloc[i + 1] - 1


def baseline(df, hold, short=False):
    vals = []
    for i in range(0, len(df) - hold - 1):
        if df["date"].iloc[i + 1 + hold] >= SPLIT:
            break
        r = fwd(df, i, hold)
        if r is not None:
            vals.append(-r - FEE if short else r - FEE)
    return float(np.mean(vals)) if vals else np.nan


def events(df, mask, hold, short=False, fee=FEE):
    """同品种不重叠；信号与出场均须在 TEST 内。"""
    rows, last_exit = [], -1
    for i in np.flatnonzero(mask.to_numpy()):
        if df["date"].iloc[i] < pd.Timestamp("2022-01-01", tz="UTC"):
            continue
        j = i + 1 + hold
        if j >= len(df) or df["date"].iloc[j] >= SPLIT:
            continue
        if i + 1 <= last_exit:
            continue
        r = fwd(df, i, hold)
        if r is None:
            continue
        r = -r if short else r
        rows.append((df["date"].iloc[i], df["pair"].iloc[0], r - fee))
        last_exit = j
    return pd.DataFrame(rows, columns=["sig_date", "pair", "ret"])


def stats(ev, label, base=None):
    if ev.empty:
        print(f"  {label}: 0 笔")
        return
    ev = ev.copy()
    ev["sig_date"] = pd.to_datetime(ev["sig_date"], utc=True)
    per_day = ev.groupby("sig_date")["ret"].mean()
    nd = len(per_day)
    sd = per_day.std(ddof=1) if nd > 1 else np.nan
    t_date = per_day.mean() / sd * np.sqrt(nd) if nd > 1 and sd > 0 else np.nan
    tt = ev["ret"].mean() / ev["ret"].std(ddof=1) * np.sqrt(len(ev)) if len(ev) > 1 else np.nan
    drop = ev.sort_values("ret", ascending=False).iloc[20:]
    pair_ok = (ev.groupby("pair")["ret"].mean() > 0).mean() * 100
    yrs = ev.groupby(ev["sig_date"].dt.year)["ret"].mean()
    print(f"  {label}: 笔={len(ev):3d} 日={nd:3d} 均值={ev['ret'].mean()*100:+.2f}% "
          f"t日={t_date:+.2f} t笔={tt:+.2f} 胜率={(ev['ret']>0).mean()*100:.0f}% "
          f"品种正={pair_ok:.0f}% 剔20={drop['ret'].mean()*100:+.2f}%")
    print(f"      逐年: " + " ".join(f"{y}:{v*100:+.2f}%" for y, v in yrs.items())
          + (f" | 基线={base*100:+.2f}%" if base is not None and not np.isnan(base) else ""))


def per_pair(dfs, fn, hold, short=False, fee=FEE):
    out = []
    for p, df in dfs.items():
        mask = fn(df)
        if mask is None:
            continue
        out.append(events(df, mask, hold, short=short, fee=fee))
    return pd.concat(out).reset_index(drop=True) if out else pd.DataFrame(
        columns=["sig_date", "pair", "ret"])


def main():
    dfs = {p: load(p) for p in PAIRS}
    print("数据: " + ", ".join(f"{p}:{len(df)}" for p, df in dfs.items()))

    print("\n[H1] TOM 月末做多")
    for hold, lab in [(6, "24h"), (18, "72h"), (30, "120h")]:
        ev = per_pair(dfs, lambda d: (d["date"].dt.is_month_end & (d["date"].dt.hour == 0)), hold)
        stats(ev, f"TOM {lab}")
    print("  (事件日仅 32 个月，t 仅作描述)")

    print("\n[H2] 周末冲高做空")
    for thr in (0.05, 0.03):
        for hold, lab in [(6, "24h"), (12, "48h")]:
            ev = per_pair(dfs, lambda d, t=thr: (d["dow"] == 6) & (d["date"].dt.hour == 20)
                          & (d["ret24"] >= t), hold, short=True)
            stats(ev, f"涨>={thr*100:.0f}% 空{lab}")

    print("\n[H3] OI 堆积无价格进展 → 做空")
    for hold, lab in [(6, "24h"), (12, "48h")]:
        ev = per_pair(dfs, lambda d: (d["oi_chg24"] >= 0.05) & (d["ret24"].abs() <= 0.01),
                      hold, short=True)
        stats(ev, f"OI+5% 平盘 空{lab}")

    print("\n[H4] 负 funding 连续 + OI 上升 → 做多")
    for hold, lab in [(12, "48h"), (6, "24h")]:
        ev = per_pair(dfs, lambda d: (d["streak"] >= 6) & (d["oi_chg24"] > 0), hold)
        stats(ev, f"streak>=6+OI↑ 多{lab}")
    print("  (BNB funding 覆盖仅 ~1.5 次/日，品种宽度需打折)")

    print("\n[H5] ATR 归一化急跌 → 做多")
    for mult in (2.5, 1.5):
        for hold, lab in [(6, "24h"), (12, "48h")]:
            ev = per_pair(dfs, lambda d, k=mult: (d["ret"] <= -k * d["atr14"] / d["close"].shift(1))
                          & (d["close"] >= (d["high"] + d["low"]) / 2), hold)
            stats(ev, f"跌>={mult}ATR 收上半 多{lab}")

    print("\n[H6] 放量恐慌反转 → 做多")
    for hold, lab in [(6, "24h"), (12, "48h")]:
        ev = per_pair(dfs, lambda d: (d["volume"] >= 4 * d["vol_med20"]) & (d["ret"] < 0)
                      & (d["close"] >= (d["high"] + d["low"]) / 2), hold)
        stats(ev, f"量4x 阴线收上半 多{lab}")

    print("\n[H7] 长下影反转 → 做多")
    for hold, lab in [(6, "24h"), (12, "48h")]:
        def f(d):
            rng = d["high"] - d["low"]
            lower = d[["open", "close"]].min(axis=1) - d["low"]
            return (rng > 0) & (lower >= 0.6 * rng) & (rng >= 2 * d["atr14"]) \
                & (d["close"] >= d["low"] + 0.75 * rng)
        ev = per_pair(dfs, f, hold)
        stats(ev, f"长下影 多{lab}")

    print("\n[H8] 放量派发簇 → 做空")
    for hold, lab in [(12, "48h"), (18, "72h")]:
        def f(d):
            dist = (d["volume"] >= 1.5 * d["vol_med20"]) & (d["ret"] < 0)
            now = dist.rolling(5).sum() >= 3
            return now & ~now.shift(1, fill_value=False)
        ev = per_pair(dfs, f, hold, short=True)
        stats(ev, f"5根3阴量 空{lab}")

    print("\n[H9] ETH/BTC 20 日动量（多 ETH 空 BTC，7 天）")
    b, e = dfs["BTC"], dfs["ETH"]
    r = b[["date", "open", "close"]].merge(
        e[["date", "open", "close"]], on="date", suffixes=("_btc", "_eth"))
    r["ratio"] = r["close_eth"] / r["close_btc"]
    r["mom20"] = r["ratio"] / r["ratio"].shift(120) - 1
    rows = []
    last = -1
    for i in np.flatnonzero(r["mom20"].notna().to_numpy()):
        if r["date"].iloc[i] < pd.Timestamp("2022-01-01", tz="UTC"):
            continue
        if abs(r["mom20"].iloc[i]) < 0.05:
            continue
        j = i + 1 + 42
        if j >= len(r) or r["date"].iloc[j] >= SPLIT or i + 1 <= last:
            continue
        side = 1 if r["mom20"].iloc[i] > 0 else -1  # 动量方向：多强腿/空弱腿
        eth = r["open_eth"].iloc[j] / r["open_eth"].iloc[i + 1] - 1
        btc = r["open_btc"].iloc[j] / r["open_btc"].iloc[i + 1] - 1
        ret = side * (eth - btc) - 0.002
        rows.append((r["date"].iloc[i], f"{'多ETH空BTC' if side > 0 else '空ETH多BTC'}", ret))
        last = j
    ev9 = pd.DataFrame(rows, columns=["sig_date", "pair", "ret"])
    stats(ev9, "ETH/BTC 动量 7d")
    print(f"  分方向: " + ", ".join(
        f"{k}:n={len(g)},均值={g.ret.mean()*100:+.2f}%" for k, g in ev9.groupby("pair")))
    print("  (n<60，仅探索性，不作为独立证据)")

    print("\n[H10] V2 周末接刀信号 × 横截面离散度分档（24h）")
    sigs = []
    for p, d in dfs.items():
        win = (d["dow"] >= 5) | ((d["dow"] == 0) & (d["bj_h"] <= 21))
        m = (d["ret"].shift(1) < -0.02) & (d["ret"] >= -0.02) & (d["ret"] > 0) & win
        sub = d.loc[m, ["date", "pair", "ret24"]].copy()
        idx = np.flatnonzero(m.to_numpy())
        sub["fwd"] = [fwd(d, i, 6) for i in idx]
        sub["exit_ok"] = [d["date"].iloc[i + 7] < SPLIT if i + 7 < len(d) else False for i in idx]
        sub = sub[(sub["date"] < SPLIT) & sub["exit_ok"]]
        sigs.append(sub)
    s = pd.concat(sigs).dropna(subset=["fwd"])
    s["fwd"] = s["fwd"] - FEE
    disp = s.groupby("date")["ret24"].std().rename("disp")
    s = s.merge(disp, on="date", how="left")
    q = disp.quantile([1 / 3, 2 / 3]).to_numpy()
    s["bucket"] = np.where(s["disp"] <= q[0], "低离散",
                           np.where(s["disp"] <= q[1], "中离散", "高离散"))
    for bk, g in s.groupby("bucket"):
        per_day = g.groupby("date")["fwd"].mean()
        t = per_day.mean() / per_day.std(ddof=1) * np.sqrt(len(per_day)) if len(per_day) > 1 else np.nan
        print(f"  {bk}: 信号={len(g):3d} 日={len(per_day):3d} 均值={g['fwd'].mean()*100:+.2f}% "
              f"t日={t:+.2f} 胜率={(g['fwd']>0).mean()*100:.0f}%")
    print("  (分档在 TEST 内分位，属样本内描述)")

    print("\n[H10b] 实际 V2 交易 × 滚动 180d 离散度分位（$ 口径，无前视）")
    try:
        from research_lib import read_result, trades_frame
        rr = pd.DataFrame({p: d.set_index("date")["ret24"] for p, d in dfs.items()})
        rr = rr[(rr.index >= pd.Timestamp("2022-01-01", tz="UTC")) & (rr.index < SPLIT)]
        all_disp = rr.std(axis=1).dropna()
        # 只用信号日之前的历史分位，避免用全样本分位造成的窥视
        rp = all_disp.rolling(1080, min_periods=360).apply(
            lambda x: (x.iloc[-1] > x.iloc[:-1]).mean(), raw=False).shift(1)
        atr_avg = pd.DataFrame({p: d.set_index("date")["atr14"] / d.set_index("date")["close"]
                                for p, d in dfs.items()}).mean(axis=1).reindex(all_disp.index)
        ap = atr_avg.rolling(1080, min_periods=360).apply(
            lambda x: (x.iloc[-1] > x.iloc[:-1]).mean(), raw=False).shift(1)
        _, trades = read_result("backtest-result-2026-10-31_01-44-39.zip")
        vt = trades_frame(trades)
        vt = vt[vt["open_dt"] < SPLIT].copy()
        vt["sig"] = vt["open_dt"] - pd.Timedelta(hours=4)
        vt["pct"] = vt["sig"].map(rp)
        vt["apct"] = vt["sig"].map(ap)
        vt = vt.dropna(subset=["pct", "apct"])
        for lab, g in [("可匹配子集", vt), ("离散>p80", vt[vt.pct >= .8]),
                       ("ATR>p80", vt[vt.apct >= .8]),
                       ("离散>p80 & ATR>p80", vt[(vt.pct >= .8) & (vt.apct >= .8)]),
                       ("离散>p80 & ATR<=p80", vt[(vt.pct >= .8) & (vt.apct < .8)])]:
            eq = g.sort_values("close_dt")["profit$"].cumsum()
            dd = (eq - eq.cummax()).min() if len(eq) else 0
            print(f"  {lab:20s} n={len(g):3d} 均值=${g['profit$'].mean():6.2f} "
                  f"合计=${g['profit$'].sum():7.0f} 胜率={(g['profit$']>0).mean()*100:.0f}% "
                  f"顺序回撤=${dd:.0f}")
    except Exception as ex:  # 报告脚本不应因缺 zip 中断
        print(f"  [skip] {ex}")


if __name__ == "__main__":
    sys.exit(main())
