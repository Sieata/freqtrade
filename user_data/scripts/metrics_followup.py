"""持仓指标筛查的稳健性复核（TEST-only，探索性；不单独晋级）。

只复核 metrics_screen.py 第一轮过线的两个候选：
  M3 TAKEREXH（taker 卖压衰竭，48h）
  M4 OIFLOW  （OI 急缩 + 24h 下跌 + taker 卖压，24h）
内容：
  1) 邻域敏感性：分位阈值 × 持有期；规则是「主形态的邻居也同向」，不是挑最好的邻居。
  2) 趋势过滤：加 OIFlushV2 的 mom30>0，看是否只是复制现有臂。
  3) 与 OIFlushV2 信号重叠：K 线级 Jaccard + 事件级 ±2 根重叠率。
  4) 事件级逐笔明细（按年、品种）用于集中度复核。

口径与 metrics_screen.py 完全一致：TEST、次根开盘进、固定持有、0.1% 往返、非重叠事件。

用法：.venv/Scripts/python.exe user_data/scripts/metrics_followup.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_lib import enable_utf8_console, load_pool, load_splits  # noqa: E402
from metrics_screen import FEE_RT, baseline, collect, load_pair, report  # noqa: E402


def event_index(d: pd.DataFrame, sig: pd.Series, hold: int):
    """与 collect 相同的事件锁，但返回信号位置（用于重叠检查）。"""
    s = sig.reindex(d.index).fillna(False).to_numpy()
    out, locked = [], -1
    for i in np.flatnonzero(s):
        if i <= locked or i + 1 + hold >= len(d):
            continue
        out.append(i)
        locked = i + hold
    return out


def jaccard(a: np.ndarray, b: np.ndarray) -> float:
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else float("nan")


def main():
    enable_utf8_console()
    s = load_splits()
    start = f"{s['test_start'][:4]}-{s['test_start'][4:6]}-{s['test_start'][6:]}"
    end = f"{s['split_date'][:4]}-{s['split_date'][4:6]}-{s['split_date'][6:]}"
    pairs = [p for p, _ in load_pool("top10")]
    data = {}
    for p in pairs:
        d = load_pair(p, start, end)
        if d is not None and d["oi_usd"].notna().sum() >= 360:
            d["mom30"] = d["close"] / d["close"].shift(180) - 1
            data[p] = d
    print(f"TEST {start} -> {end} | {len(data)} pairs")

    # ── 1) M3 邻域 ────────────────────────────────────────────────
    print("\n=== M3 TAKEREXH 邻域（taker 24h 均值分位 × 持有期）===")
    for q in (0.01, 0.02, 0.03, 0.05):
        qname = f"tk_p{int(q * 100)}"
        data_q = {}
        for p, d in data.items():
            d = d.copy()
            d[qname] = d["tk24"].rolling(1080, min_periods=360).quantile(q)
            data_q[p] = d
        for hold in (6, 8, 12, 18):
            per = {p: collect(d, d["tk24"] <= d[qname], hold) for p, d in data_q.items()}
            base = np.nanmean([baseline(d, hold) for d in data_q.values()])
            report(f"M3 q{q:.2f}", per, hold, base)

    # ── 2) M4 邻域 + 趋势过滤 ─────────────────────────────────────
    print("\n=== M4 OIFLOW 邻域（taker 分位 × 下跌阈值，hold=6）===")
    for tq in (0.05, 0.10, 0.20):
        for crash in (-0.04, -0.05, -0.06):
            per = {}
            for p, d in data.items():
                tqp = d["tk24"].rolling(1080, min_periods=360).quantile(tq)
                sig = (d["oi_chg24"] <= d["oi_p5"]) & (d["ret24"] <= crash) & (d["tk24"] <= tqp)
                per[p] = collect(d, sig, 6)
            base = np.nanmean([baseline(d, 6) for d in data.values()])
            report(f"M4 t{tq:.2f}/{crash:.0%}", per, 6, base)

    print("\n=== M4 趋势过滤（mom30>0，hold=6）===")
    for tq in (0.05, 0.10, 0.20):
        per = {}
        for p, d in data.items():
            tqp = d["tk24"].rolling(1080, min_periods=360).quantile(tq)
            sig = (d["oi_chg24"] <= d["oi_p5"]) & (d["ret24"] <= -0.05) & (d["tk24"] <= tqp) & (d["mom30"] > 0)
            per[p] = collect(d, sig, 6)
        base = np.nanmean([baseline(d, 6) for d in data.values()])
        report(f"M4T t{tq:.2f}", per, 6, base)

    # ── 3) 与 OIFlushV2 的重叠 ───────────────────────────────────
    print("\n=== 与 OIFlushV2 信号重叠（TEST）===")
    print("OIFlushV2 定义：oi_chg24<=p5 & ret4<=-5% & mom30>0（hold 无影响）")
    for label, m4 in (("M4 (taker<=p10)", True), ("M4T (taker<=p10+mom)", True)):
        js, ev_ov, n4, noi = [], [], 0, 0
        for p, d in data.items():
            oi = (d["oi_chg24"] <= d["oi_p5"]) & (d["ret4"] <= -0.05) & (d["mom30"] > 0)
            tqp = d["tk24"].rolling(1080, min_periods=360).quantile(0.10)
            m4sig = (d["oi_chg24"] <= d["oi_p5"]) & (d["ret24"] <= -0.05) & (d["tk24"] <= tqp)
            if label.startswith("M4T"):
                m4sig = m4sig & (d["mom30"] > 0)
            a, b = m4sig.fillna(False).to_numpy(), oi.fillna(False).to_numpy()
            js.append(jaccard(a, b))
            ia = event_index(d, m4sig, 6)
            ib = set(event_index(d, oi, 48))
            if ia:
                hit = sum(any((i + k) in ib for k in range(-2, 3)) for i in ia)
                ev_ov.append(hit / len(ia))
            n4 += len(ia)
            noi += len(ib)
        print(f"  {label}: 事件 {n4} | K线 Jaccard={np.nanmean(js):.2f} | "
              f"事件±2根重叠={np.nanmean(ev_ov) * 100 if ev_ov else float('nan'):.0f}% | OI 事件 {noi}")

    # ── 4) M3/M4 逐品种明细 ─────────────────────────────────────
    print("\n=== 候选逐品种/逐年（主形态）===")
    cands = {
        "M3 48h": lambda d: (d["tk24"] <= d["tk_p2"], 12),
        "M4 24h": lambda d: ((d["oi_chg24"] <= d["oi_p5"]) & (d["ret24"] <= -0.05)
                             & (d["tk24"] <= d["tk_p10"]), 6),
    }
    for name, fn in cands.items():
        print(f"\n{name}")
        rows = []
        for p, d in data.items():
            s_, h_ = fn(d)
            for y, r in collect(d, s_, h_):
                rows.append((p, y, r))
        df = pd.DataFrame(rows, columns=["pair", "year", "ret"])
        print(df.groupby("pair")["ret"].agg(["count", "mean", "sum"]).mul([1, 100, 100]).round(2).to_string())
        print("逐年(mean%):", (df.groupby("year")["ret"].mean() * 100).round(2).to_dict())


if __name__ == "__main__":
    main()
