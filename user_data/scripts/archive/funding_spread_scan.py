#!/usr/bin/env python
"""跨所资金费差扫描（币安 USDT 永续 × Hyperliquid）+ 持续性深查。

用法:
  .venv/Scripts/python.exe user_data/scripts/funding_spread_scan.py            # 快照扫描
  .venv/Scripts/python.exe user_data/scripts/funding_spread_scan.py --deep 20  # 前20名做14天持续性核查
  .venv/Scripts/python.exe user_data/scripts/funding_spread_scan.py --days 7   # 深查窗口改为7天

输出: 按费差绝对值降序的全量表 + 告警(|费差|>=10pp/年) + 深查表
      (14天平均费差 / 中位数 / 有利方向占比 -- 过滤"快照漂移"假信号)。

代理: 默认 http://127.0.0.1:7897, --no-proxy 直连。
年化口径: 币安按 8h 结算 x3x365; Hyperliquid 按 1h 结算 x24x365。
方向规则: 做空费率较高的所、做多费率较低的所, 净 carry 约 |费差|。
"""
import argparse
import sys
from datetime import datetime, timezone

import requests

BN_FAPI = "https://fapi.binance.com"
HL_INFO = "https://api.hyperliquid.xyz/info"
PREFIXES = ("1000000", "10000", "1000", "100")


def make_session(proxy):
    s = requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers["User-Agent"] = "funding-spread-scan/1.0"
    return s


def bn_snapshot(s):
    r = s.get(f"{BN_FAPI}/fapi/v1/premiumIndex", timeout=25)
    r.raise_for_status()
    out = {}
    for d in r.json():
        sym = d["symbol"]
        if not sym.endswith("USDT"):
            continue
        base = sym[: -len("USDT")]
        for p in PREFIXES:
            if base.startswith(p) and len(base) > len(p):
                base = base[len(p):]
                break
        try:
            out[base] = {
                "apr": float(d["lastFundingRate"]) * 3 * 365 * 100,
                "mark": float(d["markPrice"]),
                "sym": sym,
            }
        except (ValueError, TypeError):
            continue
    return out


def hl_snapshot(s):
    r = s.post(HL_INFO, json={"type": "metaAndAssetCtxs"}, timeout=25)
    r.raise_for_status()
    meta, ctxs = r.json()
    out = {}
    for u, c in zip(meta["universe"], ctxs):
        if u.get("isDelisted"):
            continue
        try:
            out[u["name"]] = {
                "apr": float(c["funding"]) * 24 * 365 * 100,
                "mark": float(c["markPx"]),
            }
        except (ValueError, TypeError, KeyError):
            continue
    return out


def match_pairs(bn, hl):
    rows = []
    for coin, b in bn.items():
        h = hl.get(coin)
        if not h or b["mark"] <= 0 or h["mark"] <= 0:
            continue
        # 基差: 两所标记价相对差(>5% 提示合约规格未归一)
        px = abs(b["mark"] - h["mark"]) / ((b["mark"] + h["mark"]) / 2) * 100
        rows.append({"coin": coin, "bn": b["apr"], "hl": h["apr"],
                     "diff": b["apr"] - h["apr"], "basis": px, "sym": b["sym"]})
    return rows


def bn_history(s, sym, start_ms):
    r = s.get(f"{BN_FAPI}/fapi/v1/fundingRate",
              params={"symbol": sym, "startTime": start_ms, "limit": 1000}, timeout=25)
    r.raise_for_status()
    return [(int(d["fundingTime"]) / 1000, float(d["fundingRate"])) for d in r.json()]


def hl_history(s, coin, start_ms):
    r = s.post(HL_INFO, json={"type": "fundingHistory", "coin": coin,
                              "startTime": start_ms}, timeout=25)
    r.raise_for_status()
    return [(int(d["time"]) / 1000, float(d["fundingRate"])) for d in r.json()]


def deep_check(s, rows, n, days, thresh_pp):
    """对费差前 n 名做逐小时对齐的持续性核查。"""
    import bisect
    now = datetime.now(timezone.utc).timestamp()
    start_ms = int((now - days * 86400) * 1000)
    out = []
    for row in sorted(rows, key=lambda r: abs(r["diff"]), reverse=True)[:n]:
        coin, sym = row["coin"], None
        try:
            sym = row.get("sym")
            bn_h = bn_history(s, sym, start_ms)
            hl_h = hl_history(s, coin, start_ms)
        except Exception as e:
            out.append({"coin": coin, "err": f"{type(e).__name__}: {str(e)[:60]}"})
            continue
        if not bn_h or not hl_h:
            out.append({"coin": coin, "err": "历史数据不足"})
            continue
        bn_t = [t for t, _ in bn_h]
        spreads = []
        for t, fr in hl_h:
            i = bisect.bisect_right(bn_t, t) - 1
            if i < 0:
                continue
            spreads.append(fr * 8760 * 100 - bn_h[i][1] * 1095 * 100)  # carry: 多HL+空BN 口径
        if not spreads:
            out.append({"coin": coin, "err": "对齐后无重叠区间"})
            continue
        spreads.sort()
        mean = sum(spreads) / len(spreads)
        med = spreads[len(spreads) // 2]
        # 有利占比按 14 天均值方向判定(而非快照方向): |carry|>阈值 的小时占比
        direction = 1 if mean >= 0 else -1
        fav = sum(1 for x in spreads if direction * x > thresh_pp) / len(spreads) * 100
        out.append({"coin": coin, "snap": row["diff"], "mean": mean, "med": med,
                    "fav": fav, "n": len(spreads), "err": None})
        continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep", type=int, default=0, help="对费差前N名做历史持续性核查")
    ap.add_argument("--days", type=int, default=14, help="深查历史窗口(天)")
    ap.add_argument("--thresh", type=float, default=5.0, help="有利方向判定阈值(pp/年)")
    ap.add_argument("--proxy", default="http://127.0.0.1:7897")
    ap.add_argument("--no-proxy", action="store_true")
    a = ap.parse_args()
    s = make_session(None if a.no_proxy else a.proxy)

    bn, hl = bn_snapshot(s), hl_snapshot(s)
    rows = match_pairs(bn, hl)
    print(f"币安 USDT 永续: {len(bn)} 个 | Hyperliquid: {len(hl)} 个 | 匹配: {len(rows)} 对")
    print(f"快照时间(UTC): {datetime.now(timezone.utc):%Y-%m-%d %H:%M}\n")

    alerts = sorted([r for r in rows if abs(r["diff"]) >= 10],
                    key=lambda r: abs(r["diff"]), reverse=True)
    print(f"=== 告警（|费差| >= 10.0pp/年）: {len(alerts)} 个 ===")
    for r in alerts:
        side = "多BN+空HL" if r["diff"] < 0 else "多HL+空BN"
        flag = " ⚠基差" if r["basis"] > 5 else ""
        print(f"  {r['coin']:<12} BN {r['bn']:>9.1f}%  HL {r['hl']:>9.1f}%  "
              f"费差 {r['diff']:>8.1f}pp  基差 {r['basis']:>6.2f}%  {side}{flag}")

    print("\n=== 全量前 30（按|费差|降序）===")
    print(f"{'coin':<12}{'bn_apr':>10}{'hl_apr':>10}{'diff':>9}{'basis':>9}")
    for r in sorted(rows, key=lambda r: abs(r["diff"]), reverse=True)[:30]:
        print(f"{r['coin']:<12}{r['bn']:>10.1f}{r['hl']:>10.1f}{r['diff']:>9.1f}{r['basis']:>8.2f}%")

    if a.deep:
        print(f"\n=== 深查: 前 {a.deep} 名, 近 {a.days} 天逐小时费差 ===")
        print(f"{'coin':<12}{'快照pp':>9}{'均值pp':>9}{'中位pp':>9}{'有利占比':>9}{'样本':>6}  判定")
        for d in deep_check(s, rows, a.deep, a.days, a.thresh):
            if d["err"]:
                print(f"{d['coin']:<12}  ERR {d['err']}")
                continue
            verdict = "稳定" if abs(d["mean"]) >= 10 and d["fav"] >= 60 else (
                "间歇" if abs(d["mean"]) >= 5 else "漂移/无持续")
            print(f"{d['coin']:<12}{d['snap']:>9.1f}{d['mean']:>9.1f}{d['med']:>9.1f}"
                  f"{d['fav']:>8.0f}%{d['n']:>6}  {verdict}")


if __name__ == "__main__":
    main()
