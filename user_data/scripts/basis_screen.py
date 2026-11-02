"""Basis（mark/最新价溢价）方向性事件研究（TEST-only，预注册）。

数据：`*-1h-mark.feather`（mark price klines）+ `*-4h-futures.feather`（最新价 4h）。
      basis = 4h 最后一根 mark close / 4h close − 1。正=永续溢价（多头拥挤），负=折价。
      与 H1/H1b 的「资金费水平」不同：basis 是瞬时定价，资金费是 8h 结算后的滞后值。

假设（先写后跑；多空两种方向都报，避免只挑一边）：
  B1 溢价极端：basis >= 180d p98 → 次根开盘做空，持有 24h/48h
  B2 折价极端：basis <= 180d p2  → 次根开盘做多，持有 24h/48h
  B3 溢价急升：24h basis 变化 >= p98 → 做空，持有 24h/48h
  B4 溢价急降：24h basis 变化 <= p2  → 做多，持有 24h/48h

口径：TOP10、TEST、次根开盘进/固定持有后开盘出、0.1% 往返、同品种事件锁、
      基线=全 bar 同持有期均值；指标全历史算、2021 暖机。
      B1/B3 报「做多收益」，若显著为负即做空 edge；B2/B4 报做多收益，需为正。
      注意：做空腿须额外考虑资金费/借币与强平风险，本筛查不含这些成本。

用法：.venv/Scripts/python.exe user_data/scripts/basis_screen.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_lib import USER_DATA, enable_utf8_console, load_pool, load_splits  # noqa: E402
from newedge_screen import baseline, collect, report  # noqa: E402

FUT = USER_DATA / "data" / "binance" / "futures"


def load_basis(pair: str) -> pd.DataFrame:
    sym = pair.replace("/", "_").replace(":", "_")
    kp, mp = FUT / f"{sym}-4h-futures.feather", FUT / f"{sym}-1h-mark.feather"
    if not kp.exists() or not mp.exists():
        return None
    k = pd.read_feather(kp)[["date", "open", "close", "volume"]].set_index("date").sort_index()
    m = pd.read_feather(mp)[["date", "close"]].set_index("date").sort_index()
    # 4h 内最后一根 mark 作为该 4h 的 mark 收盘；与 K 线 close 对齐
    mk = m["close"].resample("4h").last().reindex(k.index)
    k = k.assign(basis=mk / k["close"] - 1)
    k["basis24"] = k["basis"] - k["basis"].shift(6)
    k["basis_p98"] = k["basis"].rolling(1080, min_periods=360).quantile(0.98)
    k["basis_p2"] = k["basis"].rolling(1080, min_periods=360).quantile(0.02)
    k["b24_p98"] = k["basis24"].rolling(1080, min_periods=360).quantile(0.98)
    k["b24_p2"] = k["basis24"].rolling(1080, min_periods=360).quantile(0.02)
    return k


def main():
    enable_utf8_console()
    s = load_splits()
    lo, hi = pd.Timestamp(s["test_start"], tz="UTC"), pd.Timestamp(s["split_date"], tz="UTC")
    data = {}
    for p, _ in load_pool("top10"):
        d = load_basis(p)
        if d is not None and d["basis"].notna().sum() >= 500:
            data[p] = d[(d.index >= lo) & (d.index < hi)]
    print(f"TEST {lo.date()} -> {hi.date()} | {len(data)} pairs")

    fams = {
        "B1 溢价极端(p98) 做空": lambda d: d["basis"] >= d["basis_p98"],
        "B2 折价极端(p2) 做多": lambda d: d["basis"] <= d["basis_p2"],
        "B3 溢价急升(p98) 做空": lambda d: d["basis24"] >= d["b24_p98"],
        "B4 溢价急降(p2) 做多": lambda d: d["basis24"] <= d["b24_p2"],
    }
    for name, fn in fams.items():
        for hold in (6, 12):
            per = {p: collect(d, fn(d), hold) for p, d in data.items()}
            report(name, per, hold, np.nanmean([baseline(d, hold) for d in data.values()]))


if __name__ == "__main__":
    main()
