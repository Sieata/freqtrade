"""Import Binance futures metrics (OI/多空比/taker 比) from data.binance.vision daily zips.

数据: data/futures/um/daily/metrics/<SYM>/<SYM>-metrics-<date>.zip（5 分钟粒度，2021-06 起）
     列: create_time, sum_open_interest(币本位), sum_open_interest_value(USD),
         count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio,
         count_long_short_ratio, sum_taker_long_short_vol_ratio
降采样到 4h: oi_usd=4h 内最后值；比值类=4h 内均值。输出 feather 一列一指标。
vision 直连无需代理；并发 8 线程（每日 zip ~80KB）。增量: 已有 feather 从其末日续传。

用法: .venv/bin/python user_data/scripts/import_metrics_vision.py [PAIR...]   # 缺省 Phase1 10 品种
"""
import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from urllib.request import urlopen

import pandas as pd

BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
FUT_DIR = Path(__file__).resolve().parent.parent / "data" / "binance" / "futures_metrics"
PHASE1_PAIRS = ["BTC", "ETH", "BNB", "XRP", "SOL", "ZEC", "DOGE", "ADA", "AVAX", "DOT"]
START = date(2021, 6, 1)
COLS = {
    "sum_open_interest_value": "oi_usd",
    "count_toptrader_long_short_ratio": "top_ls_cnt",
    "sum_toptrader_long_short_ratio": "top_ls_pos",
    "count_long_short_ratio": "ls_cnt",
    "sum_taker_long_short_vol_ratio": "taker_ls",
}


def fetch_day(sym_usdt, d):
    url = f"{BASE}/{sym_usdt}/{sym_usdt}-metrics-{d.isoformat()}.zip"
    try:
        with urlopen(url, timeout=30) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
            df = pd.read_csv(z.open(z.namelist()[0]))
        df["date"] = pd.to_datetime(df["create_time"], utc=True)
        df = df.rename(columns=COLS)[["date", *COLS.values()]]
        return df
    except Exception:
        return None


def build(sym):
    sym_usdt = sym + "USDT"
    out = FUT_DIR / f"{sym}_USDT_USDT-4h-metrics.feather"
    d0 = START
    old = None
    if out.exists():
        old = pd.read_feather(out)
        d0 = old["date"].max().date()  # 重取末日并入（去重），边界安全
    days, d = [], d0
    end = date.today() - timedelta(days=0)
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    got = []
    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(lambda x: fetch_day(sym_usdt, x), days):
            if r is not None:
                got.append(r)
    if not got:
        return f"[FAIL] {sym}: 无任何数据"
    df = pd.concat([*([old] if old is not None else []), *got], ignore_index=True)
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    g = df.set_index("date").resample("4h")
    out4 = pd.DataFrame({
        "oi_usd": g["oi_usd"].last(),
        "top_ls_cnt": g["top_ls_cnt"].mean(),
        "top_ls_pos": g["top_ls_pos"].mean(),
        "ls_cnt": g["ls_cnt"].mean(),
        "taker_ls": g["taker_ls"].mean(),
    }).dropna(how="all").reset_index()
    out4.to_feather(out)
    return (f"[ok] {sym}: 日包 {len(got)}/{len(days)}, 4h {len(out4)} 行 "
            f"{str(out4['date'].min())[:10]} -> {str(out4['date'].max())[:10]}")


def main():
    symbols = sys.argv[1:] or PHASE1_PAIRS
    print("processing:", symbols, flush=True)
    for s in symbols:
        print(build(s), flush=True)


if __name__ == "__main__":
    main()
