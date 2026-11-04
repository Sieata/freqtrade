"""抓取更高质量的机构/基差数据（2026-11-04）。

数据源：
  1. CFTC Commitments of Traders — Traders in Financial Futures (TFF)
     Socrata: https://publicreporting.cftc.gov/resource/gpe5-46if.json
     取 CME 全尺寸 BITCOIN / ETHER CASH SETTLED，周频，2017/2021 起。
     字段含 asset manager / leveraged money 多空持仓占 OI 百分比。
  2. Binance USDⓈ-M 永续 premiumIndexKlines（日线）
     https://data.binance.vision/data/futures/um/monthly/premiumIndexKlines/
     真实溢价指数（相对现货指数），比 mark/close 干净；2021-06 起。
  3. CME 期货日线（Yahoo BTC=F / ETH=F），用于 CME 基差与隔夜 lead-lag。

输出：user_data/data/external/
  cftc_cot.csv、premium_index_btc.csv、premium_index_eth.csv、cme_futures.csv

口径：
  - COT 报告日为周二，官方周五 15:30 ET 发布；分析侧把可用日设为报告日+4 天
    （周六 00:00 UTC），再取严格早于信号日的最近值，确保不用未来数据。
  - premiumIndex 日线收盘为 UTC 00:00；分析侧取前一日值，次日 00:00 开盘交易。
  - CME 日线为 Yahoo 收盘；分析侧同样只用前一日值。
"""
import datetime as dt
import io
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "user_data" / "data" / "external"
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (research; freqtrade-quality-data)"}
PROXY = "http://127.0.0.1:7897"


def get(url, tries=3):
    last = None
    for attempt in range(tries):
        for proxy in (None, PROXY):
            try:
                handler = urllib.request.ProxyHandler(
                    {} if proxy is None else {"http": proxy, "https": proxy})
                opener = urllib.request.build_opener(handler)
                r = opener.open(urllib.request.Request(url, headers=UA), timeout=60)
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


print("[1] CFTC COT TFF (BTC/ETH full-size CME)")
BASE = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
MARKETS = {
    "BTC": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
    "ETH": "ETHER CASH SETTLED - CHICAGO MERCANTILE EXCHANGE",
}
rows = []
for asset, mkt in MARKETS.items():
    q = urllib.parse.urlencode({
        "$where": f"market_and_exchange_names='{mkt}'",
        "$order": "report_date_as_yyyy_mm_dd",
        "$limit": 5000,
    }, quote_via=urllib.parse.quote)
    j = get_json(BASE + "?" + q)
    print(f"  {asset}: {len(j)} 周")
    for r in j:
        rows.append({
            "date": pd.to_datetime(r["report_date_as_yyyy_mm_dd"]).tz_localize(None).normalize(),
            "asset": asset,
            "oi": float(r["open_interest_all"]),
            "am_long_pct": float(r["pct_of_oi_asset_mgr_long"]),
            "am_short_pct": float(r["pct_of_oi_asset_mgr_short"]),
            "lev_long_pct": float(r["pct_of_oi_lev_money_long"]),
            "lev_short_pct": float(r["pct_of_oi_lev_money_short"]),
            "am_net_contracts": float(r["asset_mgr_positions_long"]) - float(r["asset_mgr_positions_short"]),
            "lev_net_contracts": float(r["lev_money_positions_long"]) - float(r["lev_money_positions_short"]),
            "oi_chg": float(r.get("change_in_open_interest_all", 0) or 0),
        })
cot = pd.DataFrame(rows)
# 报告日周二 → 可用日周六（周五 15:30 ET 发布；+4 天更保守）
cot["avail_date"] = cot["date"] + pd.Timedelta(days=4)
cot = cot.sort_values(["asset", "date"]).reset_index(drop=True)
cot.to_csv(OUT / "cftc_cot.csv", index=False, encoding="utf-8")
print(f"  cftc_cot.csv {len(cot)} 行  {cot.date.min()} → {cot.date.max()}")

print("[2] Binance premiumIndexKlines (1d, BTC/ETH)")
for sym in ("BTC", "ETH"):
    frames = []
    for y, m in [(2021, 6 + i) for i in range(7)] + \
                [(2022, i) for i in range(1, 13)] + \
                [(2023, i) for i in range(1, 13)] + \
                [(2024, i) for i in range(1, 9)]:
        url = ("https://data.binance.vision/data/futures/um/monthly/"
               f"premiumIndexKlines/{sym}USDT/1d/{sym}USDT-1d-{y}-{m:02d}.zip")
        try:
            b = get(url)
            z = zipfile.ZipFile(io.BytesIO(b))
            with z.open(z.namelist()[0]) as f:
                txt = f.read().decode("utf-8")
            first = txt.splitlines()[0]
            if first.startswith("open_time"):
                d = pd.read_csv(io.StringIO(txt))
                d = d.iloc[:, :6]
                d.columns = ["ts", "open", "high", "low", "close", "volume"]
            else:
                d = pd.read_csv(io.StringIO(txt), header=None)
                d = d.iloc[:, :6]
                d.columns = ["ts", "open", "high", "low", "close", "volume"]
            d["date"] = pd.to_datetime(d["ts"], unit="ms", utc=True).dt.tz_localize(None)
            frames.append(d[["date", "open", "high", "low", "close"]])
        except Exception as e:  # noqa: BLE001
            print(f"    {sym} {y}-{m:02d} skip: {str(e)[:70]}")
    df = pd.concat(frames, ignore_index=True)
    df = df.groupby("date", as_index=False).last()
    save(df, f"premium_index_{sym.lower()}.csv")

print("[3] CME futures (Yahoo BTC=F / ETH=F)")
SYMS = {"btc_cme": "BTC=F", "eth_cme": "ETH=F"}
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
    d = d.groupby("date", as_index=False).last()
    frames.append(d)
cme = frames[0]
for d in frames[1:]:
    cme = cme.merge(d, on="date", how="outer")
save(cme, "cme_futures.csv")

print("完成。输出目录:", OUT)
