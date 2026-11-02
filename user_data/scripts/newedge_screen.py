"""Phase1 新方向事件研究（TEST-only，迁移自 archive/newedge_phase1.py）。

归档版因 funding 列名过期（open -> funding_rate）无法运行，且没有事件锁/集中度检验。
本脚本按 RESEARCH 24.0 的口径重写，假设保持原预注册不变：

  H1  资金费极端负做多（空头拥挤=轧空燃料；SynthPut 只证伪了做空侧）
  H1b 资金费极端正做多（杠杆拥挤的续涨变体；S11 只证伪了做空侧）
  H2  波动率压缩后突破（1d BB 带宽 squeeze × 20/10 日新高；与 BigMove 的 3 日动量不同源）
  H3  震荡市均值回归（4h 布林下轨回收 × weekday；可加 BTC<MA200 / 前根跌>2% 过滤）

口径：TOP10、TEST、信号收盘确认→次根开盘进、固定持有后开盘出、0.1% 往返、
      同品种事件锁（与 metrics_screen 一致）、基线=同品种全 bar 同持有期均值。
      指标在全历史上算（2021 作暖机）再切 TEST。

用法：.venv/Scripts/python.exe user_data/scripts/newedge_screen.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_lib import USER_DATA, enable_utf8_console, load_pool, load_splits  # noqa: E402

FUT = USER_DATA / "data" / "binance" / "futures"
FEE_RT = 0.001


def load_k(pair: str, tf: str) -> pd.DataFrame:
    sym = pair.replace("/", "_").replace(":", "_")
    p = FUT / f"{sym}-{tf}-futures.feather"
    if not p.exists():
        return None
    df = pd.read_feather(p)[["date", "open", "high", "low", "close", "volume"]]
    return df.set_index("date").sort_index()


def load_fund4h(pair: str, index: pd.DatetimeIndex) -> pd.Series:
    sym = pair.replace("/", "_").replace(":", "_")
    p = FUT / f"{sym}-1h-funding_rate.feather"
    if not p.exists():
        return pd.Series(np.nan, index=index)
    f = pd.read_feather(p)[["date", "funding_rate"]]
    f = f[f["funding_rate"] != 0].drop_duplicates("date", keep="last").set_index("date")["funding_rate"]
    return f.reindex(index, method="ffill")


def collect(df: pd.DataFrame, sig: pd.Series, hold: int):
    s = sig.reindex(df.index).fillna(False).to_numpy(dtype=bool)
    op = df["open"].to_numpy()
    out, locked = [], -1
    for i in np.flatnonzero(s):
        if i <= locked or i + 1 + hold >= len(df):
            continue
        out.append((int(df.index[i].year), float(op[i + 1 + hold] / op[i + 1] - 1 - FEE_RT)))
        locked = i + hold
    return out


def baseline(df: pd.DataFrame, hold: int) -> float:
    op = df["open"].to_numpy()
    if len(op) <= 1 + hold:
        return np.nan
    return float(np.mean(op[1 + hold:] / op[1:len(op) - hold] - 1 - FEE_RT))


def report(name: str, per_pair: dict, hold: int, base: float):
    rows = [(p, y, r) for p, ev in per_pair.items() for y, r in ev]
    if not rows:
        print(f"  {name:34s} hold={hold:2d}  无事件")
        return
    d = pd.DataFrame(rows, columns=["pair", "year", "ret"])
    n = len(d)
    m = d["ret"].mean()
    sd = d["ret"].std(ddof=1)
    t = m / (sd / np.sqrt(n)) if n > 1 and sd > 0 else np.nan
    pp = d.groupby("pair")["ret"].agg(["mean", "count"])
    pos = int(((pp["mean"] > 0) & (pp["count"] >= 8)).sum())
    tot = int((pp["count"] >= 8).sum())
    conc = d["ret"].sort_values(ascending=False).iloc[20:].sum() if n > 20 else np.nan
    ys = " ".join(f"{y}:{r * 100:+.2f}%" for y, r in d.groupby("year")["ret"].mean().items())
    flag = "★" if (n >= 60 and m >= 0.005 and t >= 2.0 and tot and pos / tot >= 0.8 and conc > 0) else " "
    print(f"  {name:34s} hold={hold:2d} n={n:3d} win={(d['ret'] > 0).mean() * 100:4.1f}% "
          f"mean={m * 100:+.2f}% t={t:+.2f} 品种{pos}/{tot} 剔20后={conc * 100:+.0f}pp {flag}")
    print(f"       逐年 {ys} | 基线 {base * 100:+.2f}% | 超额 {(m - base) * 100:+.2f}pp")


def main():
    enable_utf8_console()
    s = load_splits()
    lo, hi = pd.Timestamp(s["test_start"], tz="UTC"), pd.Timestamp(s["split_date"], tz="UTC")
    pairs = [p for p, _ in load_pool("top10")]

    # ── H1 / H1b（4h）──────────────────────────────────────────────
    k4 = {}
    for p in pairs:
        d = load_k(p, "4h")
        if d is None:
            continue
        d = d.assign(fund=load_fund4h(p, d.index), ret1=d["close"].pct_change())
        d["fund_p2"] = d["fund"].rolling(540, min_periods=200).quantile(0.02)
        d["fund_p98"] = d["fund"].rolling(540, min_periods=200).quantile(0.98)
        d["prev_ret1"] = d["ret1"].shift(1)
        k4[p] = d[(d.index >= lo) & (d.index < hi)]
    print(f"H1/H1b 4h | {len(k4)} pairs")
    h1 = {
        "H1 fund<=-0.03%": lambda d: d["fund"] <= -0.0003,
        "H1 fund<=-0.05%": lambda d: d["fund"] <= -0.0005,
        "H1 fund<=-0.10%": lambda d: d["fund"] <= -0.001,
        "H1 fund<=p2(90d)": lambda d: d["fund"] <= d["fund_p2"],
        "H1 fund<=-0.03%+跌>2%": lambda d: (d["fund"] <= -0.0003) & (d["prev_ret1"] < -0.02),
        "H1b fund>=+0.10%": lambda d: d["fund"] >= 0.001,
        "H1b fund>=p98(90d)": lambda d: d["fund"] >= d["fund_p98"],
    }
    for name, fn in h1.items():
        for hold in (6, 12, 18):
            per = {p: collect(d, fn(d), hold) for p, d in k4.items()}
            report(name, per, hold, np.nanmean([baseline(d, hold) for d in k4.values()]))

    # ── H2（1d）───────────────────────────────────────────────────
    k1 = {}
    for p in pairs:
        d = load_k(p, "1d")
        if d is None:
            continue
        mid = d["close"].rolling(20).mean()
        sd = d["close"].rolling(20).std()
        width = (4 * sd) / mid
        d = d.assign(
            brk20=d["close"] > d["high"].rolling(20).max().shift(1),
            brk10=d["close"] > d["high"].rolling(10).max().shift(1),
            sq20=(width <= width.rolling(120, min_periods=60).quantile(0.20)).astype(float),
            sq40=(width <= width.rolling(120, min_periods=60).quantile(0.40)).astype(float),
        )
        k1[p] = d[(d.index >= lo) & (d.index < hi)]
    print(f"\nH2 1d | {len(k1)} pairs")
    h2 = {
        "H2 sq20+break20": lambda d: d["brk20"] & (d["sq20"].rolling(10).max() == 1),
        "H2 sq20+break10": lambda d: d["brk10"] & (d["sq20"].rolling(10).max() == 1),
        "H2 sq40+break20": lambda d: d["brk20"] & (d["sq40"].rolling(10).max() == 1),
        "H2 naked break20": lambda d: d["brk20"],
    }
    for name, fn in h2.items():
        for hold in (5, 10, 20):
            per = {p: collect(d, fn(d), hold) for p, d in k1.items()}
            report(name, per, hold, np.nanmean([baseline(d, hold) for d in k1.values()]))

    # ── H3（4h，weekday，可选 regime）──────────────────────────────
    btc1 = load_k([p for p in pairs if p.startswith("BTC")][0], "1d")
    btc_below = (btc1["close"] < btc1["close"].rolling(200).mean())
    k3 = {}
    for p in pairs:
        d = load_k(p, "4h")
        if d is None:
            continue
        mid = d["close"].rolling(20).mean()
        sd = d["close"].rolling(20).std()
        lower = mid - 2 * sd
        d = d.assign(
            reenter=(d["close"].shift(1) < lower.shift(1)) & (d["close"] > lower),
            ret1=d["close"].pct_change(),
            btc_below=btc_below.reindex(d.index, method="ffill").fillna(False).astype(bool),
        )
        k3[p] = d[(d.index >= lo) & (d.index < hi)]
    print(f"\nH3 4h（仅工作日）| {len(k3)} pairs")
    h3 = {
        "H3 reenter": lambda d: d["reenter"],
        "H3 reenter×BTC<MA200": lambda d: d["reenter"] & d["btc_below"],
        "H3 reenter×跌>2%": lambda d: d["reenter"] & (d["ret1"].shift(1) < -0.02),
        "H3 reenter×跌>2%×BTC<MA200": lambda d: d["reenter"] & (d["ret1"].shift(1) < -0.02) & d["btc_below"],
    }
    for name, fn in h3.items():
        for hold in (3, 6):
            per = {}
            for p, d in k3.items():
                sig = fn(d).fillna(False).astype(bool) & (d.index.dayofweek < 5)
                per[p] = collect(d, sig, hold)
            report(name, per, hold, np.nanmean([baseline(d, hold) for d in k3.values()]))


if __name__ == "__main__":
    main()
