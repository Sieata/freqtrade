"""高离散度结构的样本外检验（2026-11-08）。

预注册（先写后跑，不改）：
  RESEARCH 三十一在 TEST (2022-01-01 – 2024-08-28) 上发现：V2「急跌接刀」交易在
  「9 品种 24h 收益横截面标准差」的滚动 180d 分位 >p80 时每笔约 $19，
  全样本约 $8.79；硬过滤砍半利润，低离散半仓 Sharpe 2.44/回撤 -$210 是样本内最优但未验证。
  该结论从未在 2024-08-28 之后的数据上检验。

本脚本的唯一问题：**同一个预注册规则（pct >= 0.80 = 高离散）在 2024-08-28 → 现在的
样本外数据上，是否仍然「高离散每笔 > 低离散每笔」？**

规则（完全沿用 31 节，不重调）：
  - DISP_t = 9 品种 ret24 在该 4h bar 的横截面标准差（ret24 = 近 24h 收益，6 根 4h）。
  - PCT_t = DISP 在过去 180 天（1080 根 4h，min_periods 360）的滚动分位，shift(1) 防前视。
  - 高离散 = PCT >= 0.80；低离散 = PCT < 0.80。
  - 交易集 = WeekendReverseV2 在样本外窗口实际成交（run_backtest 导出），信号日 = open_dt-4h。
  - 每笔按 profit$（固定 $1,000 口径，freqtrade 已扣手续费 fee=0.05%/边 基线）。

判定（预注册，跑之前定死）：
  - 主要指标：高离散组每笔 $ 均值 vs 低离散组。若高 > 低 且 方向与 TEST 一致 → 结构复现。
  - 结构复现的稳健旁证：高离散组合计 > 0、品种正比例高、逐年不全靠单年。
  - 不做仓位规则优化（31.10 的低离散半仓是样本内的，本脚本不碰）。

纪律：只跑样本外窗口（validate 的 VAL timerange），不改 V2 源码（SHA 冻结），
不写窥视台账（这是离散度诊断，非 V2 策略定版 VAL 运行；且 V2 的 VAL 已被查看多次，
本就无独立性——本检验的对象是「离散度效应」而非「V2 的 VAL 业绩」）。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_lib as R
import validate_strategy as V

ROOT = Path(R.__file__).resolve().parent.parent          # user_data/scripts -> user_data
DATA = ROOT / "data" / "binance" / "futures"
METRICS = ROOT / "data" / "binance" / "futures_metrics"
PAIRS = ["BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "ZEC", "DOGE", "XMR"]
VAL_START = pd.Timestamp("2024-08-28", tz="UTC")
HI_Q = 0.80


def load_ret24(p):
    k = pd.read_feather(DATA / f"{p}_USDT_USDT-4h-futures.feather").sort_values("date")
    k = k.reset_index(drop=True)
    k["ret24"] = k["close"] / k["close"].shift(6) - 1
    return k.set_index("date")["ret24"]


def rpct(s, win=1080, minp=360):
    return s.rolling(win, min_periods=minp).apply(
        lambda x: (x[-1] > x[:-1]).mean(), raw=True).shift(1)


def main():
    R.enable_utf8_console()
    # 全时间线 DISP（样本外日期的分位需要历史滚动，故用全量算）
    ret24 = pd.DataFrame({p: load_ret24(p) for p in PAIRS})
    disp = ret24.std(axis=1)
    pct = rpct(disp)

    # V2 在样本外窗口的实际成交
    cfg = str(ROOT / "config_perpetual.json")
    have = [f"{p}/USDT:USDT" for p in PAIRS]
    tr = R.load_splits()["val_timerange"]
    print(f"[样本外窗口] {tr}，V2 回测导出中…")
    zp = R.run_backtest("WeekendReverseV2", cfg, tr, have, fee=0.0005,
                        max_open_trades=1, echo=False)
    _, trades = R.read_result(zp)
    v = R.trades_frame(trades)
    v = v[v["open_dt"] >= VAL_START].copy()
    v["sig"] = v["open_dt"] - pd.Timedelta(hours=4)
    v["pct"] = v["sig"].map(pct)
    v = v.dropna(subset=["pct"]).copy()
    print(f"样本外 V2 交易（有离散分位）: {len(v)} 笔，"
          f"{v['sig'].min().date()} → {v['sig'].max().date()}")

    def desc(g, name):
        if len(g) == 0:
            print(f"  {name:18s} n=0"); return
        pairpos = (g.groupby("pair_base")["profit$"].mean() > 0).mean() * 100
        yrs = g.groupby(g["sig"].dt.year)["profit$"].agg(["size", "sum"])
        print(f"  {name:18s} n={len(g):3d} 每笔均值=${g['profit$'].mean():6.2f} "
              f"合计=${g['profit$'].sum():7.0f} 胜率={(g['profit$']>0).mean()*100:4.1f}% "
              f"品种正={pairpos:3.0f}% 逐年=" +
              " ".join(f"{y}:n{n}:${s:+.0f}" for y, (n, s) in yrs.iterrows()))

    print("\n[预注册规则 pct>=0.80 = 高离散]")
    hi = v[v["pct"] >= HI_Q]
    lo = v[v["pct"] < HI_Q]
    desc(hi, "高离散(>=p80)")
    desc(lo, "低离散(<p80)")
    desc(v, "全样本")

    # 分位五分档看单调性（描述性，不挑格）
    print("\n[五分档描述]")
    try:
        q = pd.qcut(v["pct"], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"], duplicates="drop")
        for lab, g in v.groupby(q, observed=True):
            print(f"  {str(lab):4s} n={len(g):3d} 每笔均值=${g['profit$'].mean():6.2f} "
                  f"合计=${g['profit$'].sum():6.0f} 胜率={(g['profit$']>0).mean()*100:4.1f}%")
    except Exception as e:
        print("  分档失败:", e)

    # 判定
    if len(hi) >= 5 and len(lo) >= 5:
        diff = hi["profit$"].mean() - lo["profit$"].mean()
        print(f"\n[判定] 高-低 每笔差 = ${diff:+.2f}")
        print("  TEST 基准(31节): 高 $19.05 vs 全样本 $8.79 → 高 > 低")
        verdict = "复现" if diff > 0 else "未复现"
        print(f"  样本外结论: {verdict}（高离散每笔 {'>' if diff>0 else '<='} 低离散）")
    else:
        print("\n[判定] 样本不足（高或低离散 < 5 笔），无法判定")


if __name__ == "__main__":
    main()
