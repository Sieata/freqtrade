"""OI 数据累积器：为 OIFlushV2 的 live/paper 提前积累 OI 历史。

背景：币安 openInterestHist 端点只能查最近 ~30 天，而策略的 180d 分位窗需要长期历史。
本脚本每天（或手动）滚动拉取各品种的 4h OI 序列并入本地 feather，时间越长历史越厚，
是 OIFlush 系策略 live/paper 化的前置数据件。

用法:
  .venv/Scripts/python.exe user_data/scripts/oi_accumulate.py          # 按默认池增量拉取
  .venv/Scripts/python.exe user_data/scripts/oi_accumulate.py --pairs BTC,ETH
建议配 Windows 计划任务 / cron 每日运行（币安 API 需代理，FT_PROXY 可覆盖）。
"""
import argparse
import os
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "user_data" / "data" / "binance" / "futures_metrics"
DEFAULT_PAIRS = ["BTC", "ETH", "BNB", "XRP", "SOL", "ZEC", "DOGE", "ADA", "AVAX", "DOT",
                 "TRX", "HYPE", "XMR"]
URL = "https://fapi.binance.com/futures/data/openInterestHist"


def fetch_oi(sym_usdt, days=25):
    """拉最近 days 天的 4h OI 序列（API 上限约 30 天窗口）。"""
    r = requests.get(URL, params={"symbol": sym_usdt, "period": "4h", "limit": 500}, timeout=30)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None
    df = pd.DataFrame({
        "date": pd.to_datetime([x["timestamp"] for x in rows], unit="ms", utc=True),
        "oi_usd": [float(x["sumOpenInterestValue"]) for x in rows],
    }).sort_values("date").drop_duplicates("date")
    return df


def accumulate(pair):
    sym = f"{pair}USDT"
    path = OUT / f"{pair}_USDT_USDT-4h-oi_live.feather"
    old = pd.read_feather(path) if path.exists() else None
    new = fetch_oi(sym)
    if new is None or new.empty:
        return f"[skip] {pair}: API 无数据"
    if old is not None:
        cutoff = old["date"].max() - pd.Timedelta(days=2)
        new = new[new["date"] > cutoff]
        if new.empty:
            return f"[ok] {pair}: 无新增"
        df = pd.concat([old, new]).sort_values("date").drop_duplicates("date")
    else:
        df = new
    df.to_feather(path)
    return (f"[ok] {pair}: {len(df)} 行 {str(df['date'].min())[:10]} -> {str(df['date'].max())[:10]}"
            f"（本次 +{len(new)}）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for p in [x.strip() for x in args.pairs.split(",") if x.strip()]:
        try:
            print(accumulate(p), flush=True)
        except Exception as e:
            print(f"[FAIL] {p}: {e}", flush=True)


if __name__ == "__main__":
    main()
