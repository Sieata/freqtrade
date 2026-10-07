"""H8 交割合约基差套利研究（ETH 季度交割：USDT-M 与 COIN-M 币本位）。

结构优势（vs 永续 carry）：交割合约到期基差**强制收敛**，入场溢价是锁定的；
COIN-M 用币本身做保证金，持币+空交割天然对冲，价格波动不引发清算。

口径（预注册，RESEARCH 十三 13.4；TEST 20220101-20240828）：
  触发: 年化基差 = b/剩余天数×365 ≥ θ（扫描 8%/15%/25%，b = 合约收盘/现货收盘 − 1），
       且距到期 ≥ 14 天；每合约最多入场一次（首次触发）
  退出: 持有至到期（基差强制归零）→ 收益 ≈ b_entry − 摩擦 0.3%（双边 taker）
  风险指标: 持有期内 max b（最大不利基差走扩 = 保证金压力峰值）
  品种: ETH（USDT-M 季度 + COIN-M 季度），现货参考 = ETH/USDT 现货 4h

数据: data.binance.vision（um/cm 桶 monthly 4h klines，直连），缓存
     user_data/data/binance/quarterly/{SYM}-4h.feather（增量跳过已缓存月份）。
用法: .venv/bin/python user_data/scripts/carry_h8_quarterly.py
"""
import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd

BASE = "https://data.binance.vision/data/futures"
CACHE = Path("user_data/data/binance/quarterly")
FEE = 0.003
THETAS = [0.08, 0.15, 0.25]
MIN_DAYS_LEFT = 14
T_START, T_END = pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2024-08-28", tz="UTC")


def quarter_last_fridays(start_year=2021, end_year=2026):
    """每季度月内的最后一个周五（币安交割规则：季度月最后周五 08:00 UTC 交割）。"""
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
            raw = z.open(z.namelist()[0])
            head = raw.readline().decode()
            raw.seek(0) if False else None
            # vision 2022 起部分文件带表头行
            has_header = head.lower().startswith("open_time")
            df = pd.read_csv(z.open(z.namelist()[0]), header=0 if has_header else None)
            if not has_header:
                df.columns = ["open_time", "open", "high", "low", "close", "volume",
                              "close_time", "qv", "n", "tbb", "tbq", "ig"]
            df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            return df[["date", "close"]]
    except Exception:
        return None


def contract_months(sym, expiry):
    """合约存续期（上市≈上一交割日）覆盖的 yyyy-mm 列表。"""
    start = expiry - timedelta(days=95)
    months, cur = [], date(start.year, start.month, 1)
    end = min(expiry, date.today())
    while cur <= end:
        months.append(f"{cur.year}-{cur.month:02d}")
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    return months


def download_contract(prefix, bucket, sym, expiry):
    out = CACHE / f"{sym}-4h.feather"
    if out.exists():
        return f"  [cache] {sym}"
    months = contract_months(sym, expiry)
    got = []
    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(lambda ym: fetch_zip(f"{BASE}/{bucket}/monthly/klines/{sym}/4h/{sym}-4h-{ym}.zip"), months):
            if r is not None:
                got.append(r)
    if not got:
        return f"  [none] {sym}"
    df = pd.concat(got).sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df = df[df["date"] <= pd.Timestamp(expiry, tz="UTC") + pd.Timedelta(hours=8)]
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_feather(out)
    return f"  [ok] {sym}: {len(df)} 根 {str(df['date'].min())[:10]} -> {str(df['date'].max())[:10]}"


def load_spot():
    s = pd.read_feather("user_data/data/binance/ETH_USDT-4h.feather")[["date", "close"]].rename(
        columns={"close": "spot"}).set_index("date")
    return s


def study(spot, prefix, bucket, label):
    print(f"\n=== {label} ===")
    expiries = [d for d in quarter_last_fridays() if T_START <= pd.Timestamp(d, tz="UTC") < T_END]
    rows = []
    for expiry in expiries:
        sym = f"{prefix}_{expiry.strftime('%y%m%d')}"
        f = CACHE / f"{sym}-4h.feather"
        if not f.exists():
            continue
        c = pd.read_feather(f).set_index("date")["close"].rename("perp")
        df = c.to_frame().join(spot, how="inner")
        expiry_ts = pd.Timestamp(expiry, tz="UTC") + pd.Timedelta(hours=8)
        # 数据完整性：最后数据必须贴近到期日（月度文件缺口的合约跳过，否则 b_exit 失真）
        if len(df) < 50 or df.index.max() < expiry_ts - pd.Timedelta(days=3):
            continue
        df["b"] = df["perp"] / df["spot"] - 1
        expiry_ts = pd.Timestamp(expiry, tz="UTC") + pd.Timedelta(hours=8)
        df["days_left"] = (expiry_ts - df.index).total_seconds() / 86400
        df = df[df["days_left"] >= MIN_DAYS_LEFT]
        if df.empty:
            continue
        df["ann"] = df["b"] / df["days_left"] * 365
        for theta in THETAS:
            hit = df[df["ann"] >= theta]
            if hit.empty:
                continue
            i0 = hit.index[0]
            b_entry = df.loc[i0, "b"]
            # 持有至到期：b_exit = 到期前最后一根
            b_exit = df["b"].iloc[-1]
            max_exc = df.loc[i0:, "b"].max()
            ret = b_entry - b_exit - FEE
            rows.append({"sym": sym, "expiry": expiry, "theta": theta, "year": i0.year,
                         "days_left": df.loc[i0, "days_left"], "b_entry": b_entry,
                         "lock_apr": b_entry / df.loc[i0, "days_left"] * 365,
                         "max_exc": max_exc, "ret": ret})
    if not rows:
        print("  无触发事件")
        return
    d = pd.DataFrame(rows)
    for theta in THETAS:
        s = d[d["theta"] == theta]
        if s.empty:
            continue
        print(f"θ={theta*100:.0f}%: 合约 {len(s)} 个  b_entry均值 {s['b_entry'].mean()*100:+.2f}%  "
              f"入场锁定APR均值 {s['lock_apr'].mean()*100:+.1f}%  实现收益均值 {s['ret'].mean()*100:+.2f}%  "
              f"胜率 {100*(s['ret']>0).mean():.0f}%  max基差走扩均值 {s['max_exc'].mean()*100:+.2f}% "
              f"(峰值 {s['max_exc'].max()*100:+.2f}%)")
        for _, r in s.iterrows():
            print(f"    {r['sym']}: 入场剩余{r['days_left']:.0f}天 b={r['b_entry']*100:+.2f}% "
                  f"锁定APR {r['lock_apr']*100:+.0f}% → 实现 {r['ret']*100:+.2f}%")


def main():
    expiries = quarter_last_fridays(2021, 2026)
    jobs = []
    for e in expiries:
        d = e.strftime("%y%m%d")
        if pd.Timestamp(e, tz="UTC") >= T_END:
            continue
        jobs.append(("ETHUSDT", "um", f"ETHUSDT_{d}", e))
        jobs.append(("ETHUSD", "cm", f"ETHUSD_{d}", e))
    print(f"下载/缓存 {len(jobs)} 个季度合约（4h）...")
    with ThreadPoolExecutor(8) as ex:
        for line in ex.map(lambda j: download_contract(j[0], j[1], j[2], j[3]), jobs):
            print(line, flush=True)
    spot = load_spot()
    study(spot, "ETHUSDT", "um", "H8a: USDT-M 季度交割（空交割+多现货，持有到期）")
    study(spot, "ETHUSD", "cm", "H8b: COIN-M 币本位季度交割（持币+空交割，天然对冲）")


if __name__ == "__main__":
    main()
