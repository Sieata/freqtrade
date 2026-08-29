"""跨所极端费差扫描器（H10 工具化）：币安全永续 × Hyperliquid 全 dex。

费差年化 = HL年化APR − BN年化APR；|费差| ≥ 阈值（默认 10pp）即告警：
  费差 > 0 → 多币安(收) + 空HL(付)；费差 < 0 → 多HL(收) + 空币安(付)。
同时输出跨所价格基差（HL mark / 币安 mark − 1）作为执行风险检查——
基差异常大 = 合约规格未归一或预言机分歧，勿盲目建仓。

数据源（均为单次公开调用，无需密钥）：
  币安 GET /fapi/v1/premiumIndex（全 symbol 的 lastFundingRate + markPrice）
  HL   POST /info {"type":"perpDexs"} + {"type":"metaAndAssetCtxs","dex":<name>}（逐 dex）

假设：币安按 8h 结算年化（×3×365）；HL 按 1h 结算年化（×24×365）。
个别币结算间隔不同（4h 等）会高估/低估其 APR——告警品种需人工用 funding history 复核。

用法: .venv/bin/python user_data/scripts/cross_venue_scanner.py [--threshold 10] [--top 30]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
import pandas as pd

BN_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
HL_URL = "https://api.hyperliquid.xyz/info"
PROXY = {"https": "http://127.0.0.1:7897", "http": "http://127.0.0.1:7897"}


def bn_all():
    r = requests.get(BN_URL, timeout=30, proxies=PROXY)
    r.raise_for_status()
    rows = r.json()
    out = {}
    for x in rows:
        sym = x["symbol"]
        if not sym.endswith("USDT"):
            continue
        base = sym[:-4]
        try:
            out[base] = {"apr": float(x["lastFundingRate"]) * 3 * 365 * 100,
                         "mark": float(x["markPrice"])}
        except (ValueError, KeyError):
            continue
    return out


def hl_dex(dex):
    payload = {"type": "metaAndAssetCtxs"}
    if dex != "main":
        payload["dex"] = dex
    r = requests.post(HL_URL, json=payload, timeout=30, proxies=PROXY)
    r.raise_for_status()
    data = r.json()
    if not (isinstance(data, list) and len(data) == 2):
        return []
    out = []
    for u, c in zip(data[0]["universe"], data[1]):
        try:
            if u.get("isDelisted"):
                continue
            out.append({"dex": dex, "coin": u["name"],
                        "apr": float(c["funding"]) * 24 * 365 * 100,
                        "mark": float(c["markPx"])})
        except (ValueError, KeyError, TypeError):
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=10.0, help="告警阈值（年化 pp）")
    ap.add_argument("--top", type=int, default=30, help="打印前 N 个最大费差")
    args = ap.parse_args()

    bn = bn_all()
    print(f"币安永续（USDT 本位）: {len(bn)} 个")
    # 稳定币锚定监测（HL 用 USDC 结算、币安用 USDT——双腿分属两种美元）
    try:
        px = requests.get("https://api.binance.com/api/v3/ticker/price",
                          params={"symbol": "USDCUSDT"}, timeout=15, proxies=PROXY).json()
        dev = (float(px["price"]) - 1) * 100
        flag = "  ⚠️ 脱锚预警" if abs(dev) > 0.5 else ""
        print(f"USDC/USDT 当前 {px['price']}（偏离 {dev:+.3f}%）{flag}  ——历史尾部: 2023-03 SVB -8.7%/98h 恢复")
    except Exception as e:
        print(f"USDC/USDT 锚定查询失败: {str(e)[:60]}")
    dexes = ["main"]
    try:
        for d in requests.post(HL_URL, json={"type": "perpDexs"}, timeout=30, proxies=PROXY).json():
            if d and d.get("name"):
                dexes.append(d["name"])
    except Exception as e:
        print(f"[!] perpDexs 查询失败: {e}，仅扫描主 dex")
    with ThreadPoolExecutor(6) as pool:
        hl_lists = list(pool.map(hl_dex, dexes))
    hl_all = [x for lst in hl_lists for x in lst]
    print(f"Hyperliquid dexes {len(dexes)} 个, 永续 {len(hl_all)} 个")

    # 匹配：BN base ↔ HL coin（含 builder dex 前缀去重，同币多 dex 各算一条）
    rows = []
    for x in hl_all:
        base = x["coin"]
        if base not in bn:
            continue
        b = bn[base]
        diff = x["apr"] - b["apr"]
        basis = (x["mark"] / b["mark"] - 1) * 100 if b["mark"] else float("nan")
        rows.append({"coin": base, "hl_dex": x["dex"], "bn_apr": b["apr"], "hl_apr": x["apr"],
                     "diff": diff, "basis": basis,
                     "bn_mark": b["mark"], "hl_mark": x["mark"]})
    df = pd.DataFrame(rows).sort_values("diff", key=abs, ascending=False)
    print(f"跨所同标的匹配: {len(df)} 对")

    alerts = df[abs(df["diff"]) >= args.threshold]
    print(f"\n=== 告警（|费差| ≥ {args.threshold}pp/年）：{len(alerts)} 个 ===")
    for _, r in alerts.iterrows():
        side = "多BN+空HL" if r["diff"] > 0 else "多HL+空BN"
        basis_flag = "  ⚠️价差异常(规格未归一?)" if abs(r["basis"]) > 5 else ""
        print(f"  {r['coin']:<12} [{r['hl_dex']:<5}] BN {r['bn_apr']:>+8.1f}%  HL {r['hl_apr']:>+8.1f}%  "
              f"费差 {r['diff']:>+8.1f}pp → {side}   基差 {r['basis']:+.2f}%{basis_flag}")

    print(f"\n=== 全量前 {args.top}（按 |费差| 降序）===")
    with pd.option_context("display.width", 160):
        show = df.head(args.top)[["coin", "hl_dex", "bn_apr", "hl_apr", "diff", "basis"]].copy()
        for c in ("bn_apr", "hl_apr", "diff"):
            show[c] = show[c].map("{:+.1f}".format)
        show["basis"] = show["basis"].map("{:+.2f}%".format)
        print(show.to_string(index=False))
    print("\n提醒: ① 币安按 8h 结算假设年化，个别币间隔不同需人工复核；② 基差 >5% 的品种"
          "先归一合约规格；③ 告警品种动手前用 funding history 核实持续性（快照费率会漂）。")


if __name__ == "__main__":
    main()
