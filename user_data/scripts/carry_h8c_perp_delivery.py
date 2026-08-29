"""H8c 永续×交割基差套利研究（ETH USDT-M：多永续+空交割，及反向贴水收敛）。

与 H8a/b（空交割+现货 / 持币+空交割，RESEARCH 十三 13.4）的区别：用永续腿替代
现货/持币腿，全部在 USDT-M 合约账户内完成，无需现货、无需持币。
  正向（contango）：多 perp + 空交割，持有至交割
    收益 = b_entry + Σ(funding, 多头口径) − 摩擦
    （交割结算价≈指数≈同刻 perp 平仓价，两项相消 → 基差部分在入场即锁定，
      funding 为路径项；历史正基差窗口 funding 通常为正，结构上顺向增强）
  反向（backwardation）：空 perp + 多交割，持有至交割
    收益 = −b_entry − Σ(funding) − 摩擦（负费率时空头收钱）
  到期基差强制收敛 = 胜率的结构性来源；两腿同账户对冲可共享保证金。

预注册口径（TEST 20220101-20240828，纪律同 13.4；2024-09 之后仅描述统计、不调参）：
  触发: 年化基差 ann = b/days_left×365（b = 交割收盘/永续收盘 − 1）
        正向 ann ≥ θ（8/15/25%），反向 ann ≤ −θ（8/15%），且 days_left ≥ 14；
        每合约每方向至多一次（首次触发）
  退出: 持有至交割（days_left = 0 根）
  收益 = s·b_entry + s·Σ(rate_i × P_i/P0) − FEE（s=+1 正向 / −1 反向；
        FEE 主口径 0.25% = 3 条合约腿 taker 0.15% + 0.1% 缓冲，交割腿到期交割免手续费；
        敏感度 0.15% / 0.30%）
  风险 = 持有期 min(s·b)（仓位账面基差的最差点 = 保证金压力峰值）
数据: 交割 4h = data.binance.vision（直连），缓存 user_data/data/binance/quarterly/；
      2024-09 起合约与在市合约用全存续窗口（190 天）下载，在市合约尾部用 fapi 增量补；
      2021-2024-06 沿用 H8 缓存（当季段 95 天窗口，与 13.4 同基准）。
用法: export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897
      .venv/Scripts/python.exe user_data/scripts/carry_h8c_perp_delivery.py
"""
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd

BASE = "https://data.binance.vision/data/futures"
CACHE = Path("user_data/data/binance/quarterly")
PERP_F = Path("user_data/data/binance/futures/ETH_USDT_USDT-4h-futures.feather")
FUND_F = Path("user_data/data/binance/futures/ETH_USDT_USDT-1h-funding_rate.feather")

FEE = 0.0025
FEE_SENSE = [0.0015, 0.0030]
THETAS_POS = [0.08, 0.15, 0.25]
THETAS_NEG = [0.08, 0.15]
MIN_DAYS_LEFT = 14
T_START = pd.Timestamp("2022-01-01", tz="UTC")
T_END = pd.Timestamp("2024-08-28", tz="UTC")  # TEST 边界，之后仅描述
LOOKBACK_DAYS = 190


def quarter_last_fridays(start_year=2021, end_year=2026):
    """每季度月内最后一个周五（币安交割：季度月最后周五 08:00 UTC 交割）。"""
    out = []
    for y in range(start_year, end_year + 1):
        for m in (3, 6, 9, 12):
            last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
            while last.weekday() != 4:
                last -= timedelta(days=1)
            out.append(last)
    return sorted(set(out))


def fetch_zip(url):
    try:
        with urlopen(url, timeout=30) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
            head = z.open(z.namelist()[0]).readline().decode()
            has_header = head.lower().startswith("open_time")
            df = pd.read_csv(z.open(z.namelist()[0]), header=0 if has_header else None)
            if not has_header:
                df.columns = ["open_time", "open", "high", "low", "close", "volume",
                              "close_time", "qv", "n", "tbb", "tbq", "ig"]
            df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            return df[["date", "close"]]
    except Exception:
        return None


def contract_months(expiry, lookback_days):
    start = expiry - timedelta(days=lookback_days)
    months, cur = [], date(start.year, start.month, 1)
    end = min(expiry, date.today())
    while cur <= end:
        months.append(f"{cur.year}-{cur.month:02d}")
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    return months


def fetch_api_tail(sym, last_open_ms):
    """fapi 增量补最近 K 线（vision 月度包缺当月）；需 shell 代理。"""
    try:
        import ccxt
        ex = ccxt.binanceusdm({"enableRateLimit": True})
        rows, since = [], last_open_ms + 1
        while True:
            batch = ex.fetch_ohlcv(sym, "4h", since=int(since), limit=1500)
            if not batch:
                break
            rows += batch
            if len(batch) < 1500:
                break
            since = batch[-1][0] + 1
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "v"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df[["date", "close"]]
    except Exception as e:
        print(f"  [api-tail 失败] {sym}: {type(e).__name__}: {e}")
        return None


def download_contract(sym, expiry):
    out = CACHE / f"{sym}-4h.feather"
    expiry_ts = pd.Timestamp(expiry, tz="UTC") + pd.Timedelta(hours=8)
    if out.exists():
        return f"  [cache] {sym}"
    got = []
    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(lambda ym: fetch_zip(
                f"{BASE}/um/monthly/klines/{sym}/4h/{sym}-4h-{ym}.zip"),
                contract_months(expiry, LOOKBACK_DAYS)):
            if r is not None:
                got.append(r)
    if not got:
        return f"  [none] {sym}"
    df = pd.concat(got).sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df = df[df["date"] <= expiry_ts + pd.Timedelta(hours=4)]
    if expiry_ts > pd.Timestamp.now("UTC") and len(df):
        last_ms = int(df["date"].max().value // 10**6)
        now_ms = pd.Timestamp.now("UTC").value // 10**6
        if last_ms < now_ms - 2 * 86400 * 10**3:
            tail = fetch_api_tail(sym, last_ms)
            if tail is not None:
                df = pd.concat([df, tail]).sort_values("date").drop_duplicates("date")
                print(f"  [api 补尾] {sym}: +{len(tail)} 根 -> {str(df['date'].max())[:16]}")
    df.to_feather(out)
    return (f"  [ok] {sym}: {len(df)} 根 {str(df['date'].min())[:10]} -> "
            f"{str(df['date'].max())[:10]}")


def load_inputs():
    perp = pd.read_feather(PERP_F)[["date", "close"]].set_index("date")["close"].rename("P")
    fund = pd.read_feather(FUND_F)
    rates = pd.Series(fund["open"].values, index=pd.DatetimeIndex(fund["date"]))
    return perp, rates


def build_contract(sym, expiry_ts, perp):
    f = CACHE / f"{sym}-4h.feather"
    if not f.exists():
        return None
    F = pd.read_feather(f).set_index("date")["close"].rename("F")
    df = F.to_frame().join(perp, how="inner")
    if len(df) < 50:
        return None
    df["b"] = df["F"] / df["P"] - 1
    df["days_left"] = (expiry_ts - df.index).total_seconds() / 86400
    return df[df["days_left"] >= 0]


def fund_contrib(s, entry_ts, expiry_ts, P0, perp, rates):
    m = (rates.index > entry_ts) & (rates.index <= expiry_ts)
    r = rates[m]
    if r.empty:
        return 0.0
    P_i = perp.reindex(r.index, method="ffill")
    return float((s * r * P_i / P0).sum())


def main():
    now = pd.Timestamp.now("UTC")
    perp, rates = load_inputs()
    expiries = quarter_last_fridays(2021, 2026)

    jobs = []
    for e in expiries:
        if pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=8) < now - pd.Timedelta(days=730):
            continue
        jobs.append(("ETHUSDT", f"ETHUSDT_{e.strftime('%y%m%d')}", e))
    print(f"下载/缓存 {len(jobs)} 个季度合约（4h，直连 vision + fapi 补尾）...")
    with ThreadPoolExecutor(8) as ex:
        for line in ex.map(lambda j: download_contract(j[1], j[2]), jobs):
            print(line, flush=True)

    # ---------- 正式扫描（TEST） ----------
    expiries_scan = [e for e in expiries
                     if T_START <= pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=8) < T_END]
    events = []
    for e in expiries_scan:
        sym = f"ETHUSDT_{e.strftime('%y%m%d')}"
        expiry_ts = pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=8)
        df = build_contract(sym, expiry_ts, perp)
        if df is None:
            continue
        d14 = df[df["days_left"] >= MIN_DAYS_LEFT]
        if d14.empty:
            continue
        ann = d14["b"] / d14["days_left"] * 365
        for s, thetas in ((1, THETAS_POS), (-1, THETAS_NEG)):
            for th in thetas:
                hit = d14[s * ann >= th]
                if hit.empty:
                    continue
                i0 = hit.index[0]
                b0, P0, dl = df.loc[i0, "b"], df.loc[i0, "P"], df.loc[i0, "days_left"]
                hold = df.loc[i0:]
                fc = fund_contrib(s, i0, expiry_ts, P0, perp, rates)
                risk = float((s * hold["b"]).min())
                events.append({"sym": sym, "year": i0.year, "dir": "正" if s == 1 else "反",
                               "theta": th, "days_left": dl, "b_entry": b0,
                               "lock_apr": s * b0 / dl * 365,
                               "fund_apr": fc / dl * 365, "fund": fc,
                               "risk_min": risk,
                               "ret": s * b0 + fc - FEE})
    d = pd.DataFrame(events)
    print(f"\n=== H8c 正式扫描（TEST 20220101-20240828，fee {FEE*100:.2f}%）===")
    if d.empty:
        print("  无事件")
    for s, thetas in (("正", THETAS_POS), ("反", THETAS_NEG)):
        for th in thetas:
            sub = d[(d["dir"] == s) & (d["theta"] == th)]
            if sub.empty:
                print(f"{s}向 θ={th*100:.0f}%: 无触发")
                continue
            print(f"{s}向 θ={th*100:.0f}%: n={len(sub)}  b_entry均值 {sub['b_entry'].mean()*100:+.2f}%  "
                  f"锁定APR(基差) {sub['lock_apr'].mean()*100:+.1f}%  "
                  f"funding贡献 {sub['fund'].mean()*100:+.3f}%/事件  "
                  f"实现收益均值 {sub['ret'].mean()*100:+.2f}%  胜率 {100*(sub['ret']>0).mean():.0f}%  "
                  f"最差事件 {sub['ret'].min()*100:+.2f}%  "
                  f"不利基差最差点均值 {sub['risk_min'].mean()*100:+.2f}%")
            for _, r in sub.iterrows():
                print(f"    {r['sym']} [{r['dir']}] 剩余{r['days_left']:.0f}天 b={r['b_entry']*100:+.2f}% "
                      f"锁定APR {r['lock_apr']*100:+.0f}% funding {r['fund']*100:+.2f}% "
                      f"→ 实现 {r['ret']*100:+.2f}% (risk {r['risk_min']*100:+.2f}%)")
    if len(d):
        print("\nfee 敏感度（全部事件均值）:")
        gross = d["b_entry"] * d["dir"].map({"正": 1, "反": -1}) + d["fund"]
        for fee in [FEE] + FEE_SENSE:
            print(f"  fee={fee*100:.2f}%: 均值 {(gross - fee).mean()*100:+.2f}%")

    # ---------- 描述区（2024-09 之后，不调参） ----------
    print(f"\n=== 描述区 2024-09 之后已交割合约（仅统计，不构成调参依据）===")
    expiries_desc = [e for e in expiries
                     if pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=8) >= T_END
                     and pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=8) < now]
    for e in expiries_desc:
        sym = f"ETHUSDT_{e.strftime('%y%m%d')}"
        expiry_ts = pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=8)
        df = build_contract(sym, expiry_ts, perp)
        if df is None or len(df) < 50:
            print(f"  {sym}: 数据不足，跳过")
            continue
        d14 = df[df["days_left"] >= MIN_DAYS_LEFT]
        ann = d14["b"] / d14["days_left"] * 365
        pos_best = ann.max() if len(ann) else np.nan
        neg_best = max(float((-ann).max()), 0.0) if len(ann) else np.nan
        print(f"  {sym}: b均值 {df['b'].mean()*100:+.2f}%  [{df['b'].min()*100:+.2f}%, {df['b'].max()*100:+.2f}%]  "
              f"正向最优锁定APR {pos_best*100:+.0f}%  反向最优(无贴水则为0) {neg_best*100:+.0f}%  "
              f"ann>+8% 根占比 {100*(ann>0.08).mean():.0f}%  ann<-8% 根占比 {100*(ann<-0.08).mean():.0f}%")

    # ---------- funding 年化参考 ----------
    print("\n=== ETH 永续 funding 年化参考（Σrate×365/天数，未做价格修正）===")
    for y in (2022, 2023, 2024, 2025, 2026):
        r = rates[(rates.index >= pd.Timestamp(f"{y}-01-01", tz="UTC")) &
                  (rates.index < pd.Timestamp(f"{y+1}-01-01", tz="UTC"))]
        if r.empty:
            continue
        days = (r.index.max() - r.index.min()).total_seconds() / 86400
        print(f"  {y}: {r.sum()/days*365*100:+.1f}%")

    # ---------- 在市合约快照 ----------
    print("\n=== 在市合约快照 ===")
    for e in expiries:
        expiry_ts = pd.Timestamp(e, tz="UTC") + pd.Timedelta(hours=8)
        if expiry_ts <= now:
            continue
        sym = f"ETHUSDT_{e.strftime('%y%m%d')}"
        df = build_contract(sym, expiry_ts, perp)
        if df is None or df.empty:
            print(f"  {sym}: 无数据")
            continue
        last = df.iloc[-1]
        d30 = df[df.index >= df.index.max() - pd.Timedelta(days=30)]
        dl = last["days_left"]
        print(f"  {sym}: 最新 {str(df.index.max())[:16]}  b={last['b']*100:+.2f}%  剩余 {dl:.0f} 天  "
              f"锁定APR {last['b']/dl*365*100:+.1f}%  近30天 b均值 {d30['b'].mean()*100:+.2f}%")
    r90 = rates[rates.index >= now - pd.Timedelta(days=90)]
    if not r90.empty:
        print(f"  近90天 funding 年化: {r90.sum()/90*365*100:+.1f}%")


if __name__ == "__main__":
    main()
