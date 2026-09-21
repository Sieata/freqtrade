#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""exit_anatomy — 从 freqtrade 回测导出（--export trades 的 zip）解剖策略的出场与赔率结构。

回答三个问题（全部只用导出里的字段 + 本地 K 线，无未来函数）：
  1. 钱从哪来、亏在哪去：出场原因分布、亏损构成（是止损还是别的）。
  2. 止损该不该动：赢家的持有期最大浮亏分布（收紧止损会砍掉多少赢家）、
     止损单的反事实（不止损扛 N 小时会怎样）。
  3. 赔率几何：由 n/利润/胜率/PF 反推平均盈利 W、平均亏损 L 与**保本胜率** L/(L+W)，
     对照实际胜率给出结构性余量——高胜率策略的脆弱点常在这里，而不在回撤。

用法:
  .venv/bin/python user_data/scripts/exit_anatomy.py <backtest-result-*.zip> [--horizon 72]
"""
from __future__ import annotations

import argparse
import json
import os
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CAND_DIR = ROOT / "user_data" / "data" / "binance" / "futures"

_cache: dict[str, pd.DataFrame] = {}


def candles(pair: str) -> pd.DataFrame | None:
    if pair not in _cache:
        f = CAND_DIR / (pair.replace("/", "_").replace(":", "_") + "-4h-futures.feather")
        _cache[pair] = pd.read_feather(f).reset_index(drop=True) if f.exists() else None
    return _cache[pair]


def load_trades(zip_path: str) -> tuple[str, list[dict]]:
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if not n.endswith(".json") or "config" in n:
                continue
            d = json.loads(z.read(n))
            if isinstance(d, dict) and "strategy" in d:
                name = list(d["strategy"])[0]
                return name, d["strategy"][name]["trades"]
    raise SystemExit(f"{zip_path}: 未找到策略结果")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("result", help="backtest-result-*.zip")
    ap.add_argument("--horizon", type=int, default=72, help="止损反事实的扛单时长（小时，默认 72）")
    args = ap.parse_args()

    name, tr = load_trades(args.result)
    n = len(tr)
    total = sum(t["profit_abs"] for t in tr)
    print("=" * 92)
    print(f"# {name} | {os.path.basename(args.result)} | n={n} | 总利润 ${total:,.0f}")
    print("=" * 92)

    # ── 1. 出场原因分布 ────────────────────────────────
    by = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for t in tr:
        b = by[t["exit_reason"]]
        b[0] += 1
        b[1] += t["profit_abs"]
        b[2] += t["profit_ratio"]
        b[3] += t["trade_duration"]
    print(f"\n[1] 出场原因\n{'reason':22s} {'n':>5s} {'占比':>7s} {'总利润$':>10s} {'利润占比':>8s} {'均值%':>7s} {'时长h':>7s}")
    for k, v in sorted(by.items(), key=lambda x: -x[1][1]):
        print(f"{k:22s} {v[0]:5d} {v[0]/n*100:6.1f}% {v[1]:10,.0f} {v[1]/total*100:7.1f}% "
              f"{v[2]/v[0]*100:6.2f}% {v[3]/v[0]/60:7.1f}")

    # ── 2. 亏损构成 ────────────────────────────────────
    losers = [t for t in tr if t["profit_abs"] < 0]
    stops = [t for t in tr if t["exit_reason"] == "stop_loss"]
    gross_loss = sum(t["profit_abs"] for t in losers)
    print(f"\n[2] 亏损构成：亏损单 {len(losers)} 笔（{len(losers)/n*100:.1f}%），合计 ${gross_loss:,.0f}")
    if losers:
        print(f"    其中 stop_loss {len(stops)} 笔，占亏损总额 "
              f"{sum(t['profit_abs'] for t in stops)/gross_loss*100:.1f}%，"
              f"均值 {sum(t['profit_ratio'] for t in stops)/max(len(stops),1)*100:.2f}%")

    # ── 3. 赢家的浮亏深度（收紧止损的代价） ────────────
    win = [t for t in tr if t["profit_abs"] > 0]
    if win:
        dds = sorted(t["min_rate"] / t["open_rate"] - 1 for t in win)
        print(f"\n[3] 赢家 {len(win)} 笔的持有期最大浮亏（min_rate 毛口径）：收紧止损会砍掉多少赢家")
        for thr in (-0.03, -0.05, -0.07, -0.08):
            k = sum(1 for d in dds if d <= thr)
            print(f"    浮亏 <= {thr*100:5.0f}%: {k:4d} 笔 ({k/len(win)*100:5.1f}%)")
        print(f"    分位 5% / 25% / 50%: {dds[len(dds)//20]*100:.1f}% / {dds[len(dds)//4]*100:.1f}% / "
              f"{dds[len(dds)//2]*100:.1f}%")

    # ── 4. 尾随与 ROI 的让出/截断 ──────────────────────
    ts = [t for t in tr if t["exit_reason"] == "trailing_stop_loss"]
    if ts:
        mfe = sum(t["max_rate"] / t["open_rate"] - 1 for t in ts) / len(ts)
        rl = sum(t["profit_ratio"] for t in ts) / len(ts)
        give = sorted(t["max_rate"] / t["open_rate"] - 1 - t["profit_ratio"] for t in ts)
        print(f"\n[4] 尾随单 {len(ts)} 笔：平均峰值 +{mfe*100:.2f}% → 兑现 +{rl*100:.2f}%，让出 {(mfe-rl)*100:.2f}pp")
        print(f"    让出中位 {give[len(give)//2]*100:.2f}pp；让出>2pp 的占 {sum(1 for g in give if g > 0.02)/len(give)*100:.0f}%")
    roi = [t for t in tr if t["exit_reason"] == "roi"]
    if roi:
        mfe = sum(t["max_rate"] / t["open_rate"] - 1 for t in roi) / len(roi)
        over = sum(1 for t in roi if t["max_rate"] / t["open_rate"] - 1 > t["profit_ratio"])
        print(f"\n[4b] ROI 止盈单 {len(roi)} 笔：兑现 {sum(t['profit_ratio'] for t in roi)/len(roi)*100:.2f}%，"
              f"峰值均值 +{mfe*100:.2f}%，其中 {over} 笔峰值高于兑现（被截断）；"
              f"潜在让出 {(mfe - sum(t['profit_ratio'] for t in roi)/len(roi))*100:.2f}pp/笔")

    # ── 5. 止损反事实：不止损、扛 N 小时 ────────────────
    if stops:
        orig, alt = [], []
        for t in stops:
            df = candles(t["pair"])
            if df is None:
                continue
            ci = df.index[df["date"] == pd.Timestamp(t["close_date"])]
            if not len(ci):
                continue
            i = ci[0]
            j = i + args.horizon // 4
            if j >= len(df):
                continue
            orig.append(t["profit_ratio"])
            alt.append(df["close"].iloc[j] / t["open_rate"] - 1)
        if orig:
            better = sum(1 for o, a in zip(orig, alt) if a > o)
            prof = sum(1 for a in alt if a > 0)
            print(f"\n[5] 止损反事实（{len(orig)} 笔可定位，改为平仓后 {args.horizon}h 收盘离场，毛口径未计 funding）")
            print(f"    原止损均值 {sum(orig)/len(orig)*100:+.2f}% → 扛 {args.horizon}h 均值 {sum(alt)/len(alt)*100:+.2f}%"
                  f"；扛单更优 {better} 笔（{better/len(orig)*100:.0f}%），其中转正 {prof} 笔（{prof/len(orig)*100:.0f}%）")

    # ── 6. 赔率结构与保本胜率 ──────────────────────────
    nw, nl = len(win), len(losers)
    gw, gl = sum(t["profit_abs"] for t in win), -gross_loss
    if nw and nl and gw > 0 and gl > 0:
        pf = gw / gl
        W, L = gw / nw, gl / nl
        be = L / (L + W)
        w = nw / n
        print(f"\n[6] 赔率结构：平均盈利 +${W:,.1f} / 平均亏损 -${L:,.1f}（赔率 {W/L:.2f}，PF {pf:.2f}）")
        print(f"    保本胜率 {be*100:.1f}% vs 实际 {w*100:.1f}% → 结构余量 {(w-be)*100:+.1f}pp")
        print(f"    解读：胜率每掉 1pp，每笔期望变化约 ${(W+L)/100:,.2f}；"
              f"余量 <3pp 时实盘摩擦（费率/滑点）足以吃掉 edge。")
        print(f"    构造换算：1 个止损单 ≈ {L/W:.1f} 个赢家的利润。")


if __name__ == "__main__":
    main()
