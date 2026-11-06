"""抓取外部/另类数据（2026-11-04），供 TEST-only 研究使用。

数据源（全部免费、无需 key，已实测直连可用）：
  1. Fear & Greed Index  https://api.alternative.me/fng/?limit=0&format=json
  2. 稳定币总供给        https://stablecoins.llama.fi/stablecoincharts/all
     （总供给 / USDT id=1 / USDC id=2）
  3. Deribit DVOL        public/get_volatility_index_data（BTC/ETH，1D，2021-03 起）
  4. 宏观日线            Yahoo Finance chart API：
     DXY(DX-Y.NYB)、S&P500(^GSPC)、Nasdaq(^IXIC)、Gold(GC=F)、VIX(^VIX)、US10Y(^TNX)
  5. BTC 链上日线        blockchain.info charts API：
     n-transactions / transaction-fees / hash-rate / n-unique-addresses

输出：user_data/data/external/*.csv（UTF-8，date 为 UTC 日期）。
注意：
  - 所有序列在分析侧统一 shift(1) 后再用，避免「当天收盘数据当天交易」的前视。
  - 稳定币供给可能被后续修订；本文件是 2026-11-04 快照。
  - DVOL 2021-03-24 才开始，2021 年只作暖机。
  - 免责：这些是公开聚合数据，字段口径以来源为准；研究结论不构成实盘建议。
"""
import datetime as dt
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

# 本脚本无 argparse，整体即流程；防止误传 --help/-h 时直接触发全量网络抓取。
if any(a in ("-h", "--help") for a in sys.argv[1:]):
    print(__doc__.strip())
    sys.exit(0)

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "user_data" / "data" / "external"
OUT.mkdir(parents=True, exist_ok=True)
PROXY = "http://127.0.0.1:7897"
UA = {"User-Agent": "Mozilla/5.0 (research; freqtrade-altdata)"}


def get(url, tries=3):
    last = None
    for attempt in range(tries):
        for proxy in (None, PROXY):
            try:
                handler = urllib.request.ProxyHandler(
                    {} if proxy is None else {"http": proxy, "https": proxy})
                opener = urllib.request.build_opener(handler)
                r = opener.open(urllib.request.Request(url, headers=UA), timeout=40)
                return r.read()
            except Exception as e:  # noqa: BLE001
                last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed: {url}: {last}")


def get_json(url):
    return json.loads(get(url))


def save(df, name):
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    df.to_csv(OUT / name, index=False, encoding="utf-8")
    print(f"  {name:28s} {len(df):5d} 行  {df.date.min()} → {df.date.max()}")


print("[1] Fear & Greed")
j = get_json("https://api.alternative.me/fng/?limit=0&format=json")
fng = pd.DataFrame(j["data"])
fng["date"] = pd.to_datetime(fng["timestamp"].astype(int), unit="s", utc=True).dt.tz_localize(None)
fng["value"] = fng["value"].astype(int)
fng = fng[["date", "value", "value_classification"]].rename(
    columns={"value_classification": "classification"})
save(fng, "fear_greed.csv")

print("[2] Stablecoins")
rows = []
for sid, name in [(None, "total"), (1, "usdt"), (2, "usdc")]:
    q = "https://stablecoins.llama.fi/stablecoincharts/all" + (
        "" if sid is None else f"?stablecoin={sid}")
    j = get_json(q)
    d = pd.DataFrame(j)
    d["date"] = pd.to_datetime(d["date"].astype(int), unit="s", utc=True).dt.tz_localize(None)
    d[name] = d["totalCirculatingUSD"].apply(lambda x: x.get("peggedUSD", 0.0))
    rows.append(d[["date", name]])
stable = rows[0]
for d in rows[1:]:
    stable = stable.merge(d, on="date", how="outer")
save(stable, "stablecoins.csv")

print("[3] Deribit DVOL (BTC/ETH)")
for cur in ("BTC", "ETH"):
    chunks = []
    for a, b in [("2021-01-01", "2022-12-31"), ("2023-01-01", "2024-12-31"),
                 ("2025-01-01", "2026-12-31")]:
        s = int(dt.datetime.fromisoformat(a).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        e = int(dt.datetime.fromisoformat(b).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        q = ("https://www.deribit.com/api/v2/public/get_volatility_index_data"
             f"?currency={cur}&start_timestamp={s}&end_timestamp={e}&resolution=1D")
        j = get_json(q)
        chunks += j["result"]["data"]
    d = pd.DataFrame(chunks, columns=["ts", "open", "high", "low", "close"])
    d["date"] = pd.to_datetime(d["ts"], unit="ms", utc=True).dt.tz_localize(None)
    save(d[["date", "open", "high", "low", "close"]], f"dvol_{cur.lower()}.csv")

print("[4] Macro (Yahoo)")
SYMS = {"dxy": "DX-Y.NYB", "spx": "^GSPC", "ndx": "^IXIC",
        "gold": "GC=F", "vix": "^VIX", "us10y": "^TNX"}
frames = []
for name, sym in SYMS.items():
    q = ("https://query1.finance.yahoo.com/v8/finance/chart/"
         + urllib.parse.quote(sym)
         + "?period1=1577836800&period2=1800000000&interval=1d")
    j = get_json(q)
    r = j["chart"]["result"][0]
    d = pd.DataFrame({
        "date": pd.to_datetime(r["timestamp"], unit="s", utc=True).tz_localize(None).normalize(),
        name: r["indicators"]["quote"][0]["close"],
    }).dropna()
    frames.append(d)
macro = frames[0]
for d in frames[1:]:
    macro = macro.merge(d, on="date", how="outer")
save(macro, "macro.csv")

print("[5] BTC on-chain (blockchain.info)")
frames = []
for name, chart in [("n_tx", "n-transactions"), ("fees_btc", "transaction-fees"),
                    ("hash_rate", "hash-rate"), ("unique_addr", "n-unique-addresses")]:
    q = f"https://api.blockchain.info/charts/{chart}?timespan=all&format=json&sampled=false"
    j = get_json(q)
    d = pd.DataFrame(j["values"])
    d["date"] = pd.to_datetime(d["x"], unit="s", utc=True).dt.tz_localize(None).dt.normalize()
    d = d.rename(columns={"y": name})[["date", name]]
    # 有些图是小时/10 分钟粒度，按日取最后一个值
    d = d.groupby("date", as_index=False)[name].last()
    frames.append(d)
onchain = frames[0]
for d in frames[1:]:
    onchain = onchain.merge(d, on="date", how="outer")
save(onchain, "btc_onchain.csv")

print("完成。输出目录:", OUT)
