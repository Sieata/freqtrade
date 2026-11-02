"""持仓结构指标事件研究（TEST-only 预注册筛查）。

背景：futures_metrics 现有 2021-06 起的 4h 持仓数据（oi_usd / top_ls_pos / ls_cnt / taker_ls），
但 2026-08-10 的项目结论仍写着「多空比历史仅 31 天、无法回测」。本脚本只做事件级筛查，
通过预注册门禁后才值得写 freqtrade 原型；不碰 VAL。

预注册（先写后跑，参见 RESEARCH 24.0 的同款纪律）
- 池：TOP10（research_lib.load_pool），TEST = splits.json 的 test_timerange。
- 数据：K 线在 futures/，持仓指标在 futures_metrics/，按 date 左连接。
  指标在**全历史**上算滚动分位（2021 只作暖机），再切 TEST，避免 TEST 开头样本不足。
- 执行：信号 K 收盘确认 → 次根开盘进场 → 持有 H 根后开盘出场；往返摩擦 0.1%。
- 事件：同一品种信号触发后锁定到出场，不重复计数。
- 基线：同品种 TEST 内所有 4h bar 的同持有期收益均值（无条件），事件必须显著高于基线。

五个假设（均为多头，贴合加密正漂移与既往「做空暴涨证伪」）
  M1 TOPCAP   大户持仓投降：top_ls_pos 24h 均值 ≤ 自身 180d p2
  M2 RETAILCAP 散户数量投降：ls_cnt 24h 均值 ≤ 自身 180d p2
  M3 TAKEREXH  主动卖压衰竭：taker_ls 24h 均值 ≤ 自身 180d p2
  M4 OIFLOW    去杠杆共振：OI 24h 变化 ≤ 180d p5 且 24h 收益 ≤ -5%
               且 taker_ls 24h 均值 ≤ 自身 180d p10
  M5 TOPDIP    大户逆势加多：top_ls_pos 24h 均值 ≥ 自身 180d p98 且 24h 收益 ≤ -5%

主持有期 H=6（24h）；H=12（48h）只作敏感性，不单独晋级。

晋级门禁（全部满足才写原型）
  n ≥ 60 · 事件均值 ≥ +0.5%（已扣 0.1%）· 单边事件 t ≥ 2.0 ·
  品种净正 ≥ 80%（每品种 n ≥ 8）· 剔除最赚 20 笔后总收益仍 > 0 ·
  有数据的年份逐年均值为正（top_ls 类 2022 覆盖 <20%，该年标注 n/a，不强行判负）

用法：.venv/Scripts/python.exe user_data/scripts/metrics_screen.py
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from research_lib import (  # noqa: E402
    USER_DATA, enable_utf8_console, load_pool, load_splits,
)

FUT = USER_DATA / "data" / "binance" / "futures"
METRICS = USER_DATA / "data" / "binance" / "futures_metrics"
FEE_RT = 0.001
WIN = 1080          # 180d @ 4h
MINP = 360          # 至少 60d 才出分位
HOLDS = {"24h": 6, "48h": 12}
MIN_N = 60


def load_pair(pair: str, start: str, end: str):
    sym = pair.replace("/", "_").replace(":", "_")
    kp = FUT / f"{sym}-4h-futures.feather"
    mp = METRICS / f"{sym}-4h-metrics.feather"
    if not kp.exists() or not mp.exists():
        return None
    k = pd.read_feather(kp).set_index("date").sort_index()
    m = pd.read_feather(mp).set_index("date").sort_index()
    df = k[["open", "high", "low", "close", "volume"]].join(m, how="left")
    # 指标在全历史上算，2021 只作暖机
    df["ret4"] = df["close"] / df["close"].shift(1) - 1
    df["ret24"] = df["close"] / df["close"].shift(6) - 1
    df["oi_chg24"] = df["oi_usd"] / df["oi_usd"].shift(6) - 1

    def q(s: pd.Series, p: float) -> pd.Series:
        return s.rolling(WIN, min_periods=MINP).quantile(p)

    df["top24"] = df["top_ls_pos"].rolling(6).mean()
    df["ls24"] = df["ls_cnt"].rolling(6).mean()
    df["tk24"] = df["taker_ls"].rolling(6).mean()
    df["oi_p5"] = q(df["oi_chg24"], 0.05)
    df["top_p2"] = q(df["top24"], 0.02)
    df["top_p98"] = q(df["top24"], 0.98)
    df["ls_p2"] = q(df["ls24"], 0.02)
    df["tk_p2"] = q(df["tk24"], 0.02)
    df["tk_p10"] = q(df["tk24"], 0.10)
    return df[(df.index >= start) & (df.index < end)]


def collect(df: pd.DataFrame, sig: pd.Series, hold: int):
    """非重叠事件：返回 [(year, ret)]；次根开盘进、hold 根后开盘出。"""
    sig = sig.reindex(df.index).fillna(False).to_numpy()
    op = df["open"].to_numpy()
    idx = df.index
    out, locked = [], -1
    for i in np.flatnonzero(sig):
        if i <= locked or i + 1 + hold >= len(idx):
            continue
        out.append((idx[i].year, op[i + 1 + hold] / op[i + 1] - 1 - FEE_RT))
        locked = i + hold
    return out


def baseline(df: pd.DataFrame, hold: int) -> float:
    op = df["open"].to_numpy()
    if len(op) <= 1 + hold:
        return float("nan")
    return float(np.mean(op[1 + hold:] / op[1:len(op) - hold] - 1 - FEE_RT))


def report(name, per_pair: dict[str, list], hold: int, base: float):
    rows = [(p, y, r) for p, ev in per_pair.items() for y, r in ev]
    if not rows:
        print(f"  {name:12s} hold={hold:2d}  无事件")
        return
    d = pd.DataFrame(rows, columns=["pair", "year", "ret"])
    n = len(d)
    mean = d["ret"].mean()
    sd = d["ret"].std(ddof=1)
    t = mean / (sd / np.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    win = (d["ret"] > 0).mean()
    med = d["ret"].median()
    pp = d.groupby("pair")["ret"].agg(["mean", "count"])
    pos = int(((pp["mean"] > 0) & (pp["count"] >= 8)).sum())
    tot = int((pp["count"] >= 8).sum())
    conc = d["ret"].sort_values(ascending=False).iloc[20:].sum() if n > 20 else float("nan")
    by_year = d.groupby("year")["ret"].agg(["mean", "count"])
    ys = " ".join(f"{y}:{r['mean'] * 100:+.2f}%(n={r['count']})" for y, r in by_year.iterrows())
    flag = "★" if (n >= MIN_N and mean >= 0.005 and t >= 2.0
                   and tot > 0 and pos / tot >= 0.8 and conc > 0) else " "
    print(f"  {name:12s} hold={hold:2d} n={n:3d} win={win * 100:4.1f}% mean={mean * 100:+.2f}% "
          f"med={med * 100:+.2f}% t={t:+.2f} 品种{pos}/{tot} 剔20后={conc * 100:+.0f}pp {flag}")
    print(f"               逐年 {ys}")
    print(f"               基线 {base * 100:+.2f}%/笔 | 超额 {(mean - base) * 100:+.2f}pp")


def main():
    enable_utf8_console()
    splits = load_splits()
    start = f"{splits['test_start'][:4]}-{splits['test_start'][4:6]}-{splits['test_start'][6:]}"
    end = f"{splits['split_date'][:4]}-{splits['split_date'][4:6]}-{splits['split_date'][6:]}"
    pairs = [p for p, _ in load_pool("top10")]
    data = {}
    for p in pairs:
        df = load_pair(p, start, end)
        if df is None or df["oi_usd"].notna().sum() < MINP:
            print(f"[skip] {p}: 数据不足")
            continue
        data[p] = df
    print(f"TEST {start} -> {end} | {len(data)} pairs: {', '.join(data)}")
    print("top_ls_pos 覆盖率（2022 / 2023+）：", end="")
    cov22 = np.mean([d.loc[d.index.year == 2022, "top_ls_pos"].notna().mean() for d in data.values()])
    cov23 = np.mean([d.loc[d.index.year >= 2023, "top_ls_pos"].notna().mean() for d in data.values()])
    print(f"{cov22 * 100:.0f}% / {cov23 * 100:.0f}%")

    fams = {
        "M1 TOPCAP": lambda d: d["top24"] <= d["top_p2"],
        "M2 RETAILCAP": lambda d: d["ls24"] <= d["ls_p2"],
        "M3 TAKEREXH": lambda d: d["tk24"] <= d["tk_p2"],
        "M4 OIFLOW": lambda d: (d["oi_chg24"] <= d["oi_p5"]) & (d["ret24"] <= -0.05)
                               & (d["tk24"] <= d["tk_p10"]),
        "M5 TOPDIP": lambda d: (d["top24"] >= d["top_p98"]) & (d["ret24"] <= -0.05),
    }
    for name, fn in fams.items():
        print(f"\n{name}")
        for label, hold in HOLDS.items():
            per = {p: collect(d, fn(d), hold) for p, d in data.items()}
            base = np.nanmean([baseline(d, hold) for d in data.values()])
            report(name, per, hold, base)


if __name__ == "__main__":
    main()
