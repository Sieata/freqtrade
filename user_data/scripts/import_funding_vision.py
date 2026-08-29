"""Import funding_rate history from data.binance.vision monthly zips → freqtrade feather.

用于 fapi /fapi/v1/fundingRate 端点被 WAF 403 拦截时补数据（K 线与 mark 已由 API 下载）。
vision 桶直连可用（无需代理）。注意：
  - vision 只有 fundingRate 的 monthly 包，daily 包全部 404 → 导入止于最后一个完整月，
    当月增量靠 API（重跑 ensure-data.sh）。
  - 个别月份 zip 缺件会留空洞（实例：BNB 2025-11），用 /fapi/v1/fundingRate 按 startTime
    单独补齐；完整性检查惯例：funding 相邻间隔 >3 天即报警（ENGINEERING_NOTES 一.3）。

用法: .venv/bin/python user_data/scripts/import_funding_vision.py [PAIR...]
缺省处理所有缺少 funding_rate feather 的品种。
"""
import io
import os
import sys
import zipfile
from datetime import date
from urllib.request import urlopen

import pandas as pd

BASE = "https://data.binance.vision/data/futures/um"
FUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "binance", "futures")
ALL_PAIRS = ["BTC", "ETH", "SOL", "XRP", "BNB", "ZEC", "HOME", "BANK", "CYS", "HYPE", "DOGE", "ADA", "AVAX", "DOT"]
SKIP = {"ta-lib"}  # noqa


def months(start=(2021, 1), end=None):
    """月度序列；end 缺省 = 上一个完整月（vision 当月包不存在）。"""
    if end is None:
        t = date.today()
        y, m = t.year, t.month - 1
        if m == 0:
            y, m = y - 1, 12
        end = (y, m)
    y, m = start
    while (y, m) <= end:
        yield f"{y}-{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1


def fetch(url):
    try:
        with urlopen(url, timeout=60) as r:
            return r.read()
    except Exception:
        return None


def rows_from_zip(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    df = pd.read_csv(z.open(z.namelist()[0]))
    return df


def build(symbol):
    recs = []
    n_miss = 0
    for ym in months():
        url = f"{BASE}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{ym}.zip"
        blob = fetch(url)
        if blob is None:
            n_miss += 1
            continue
        recs.append(rows_from_zip(blob))
    # vision 无 fundingRate daily 包（404），导入止于最后一个完整月；当月增量走 API

    if not recs:
        return None
    df = pd.concat(recs, ignore_index=True)
    rate_col = "last_funding_rate" if "last_funding_rate" in df.columns else "lastFundingRate"
    out = pd.DataFrame({
        "date": pd.to_datetime(df["calc_time"], unit="ms", utc=True).dt.as_unit("ms"),
        "open": df[rate_col].astype("float64"),
        "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0.0,
    }).sort_values("date").drop_duplicates("date").reset_index(drop=True)
    return out, n_miss


def main():
    symbols = sys.argv[1:] or [
        p for p in ALL_PAIRS
        if not os.path.exists(os.path.join(FUT_DIR, f"{p}_USDT_USDT-1h-funding_rate.feather"))
    ]
    print("processing:", symbols)
    for s in symbols:
        res = build(s + "USDT")
        if res is None:
            print(f"[FAIL] {s}: no data files found")
            continue
        df, n_miss = res
        path = os.path.join(FUT_DIR, f"{s}_USDT_USDT-1h-funding_rate.feather")
        df.to_feather(path)
        print(f"[ok] {s}: {len(df)} rows {df['date'].min()} -> {df['date'].max()}  (missing files: {n_miss})")


if __name__ == "__main__":
    main()
