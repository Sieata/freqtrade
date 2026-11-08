"""高离散度旁路监控（2026-11-08）。

用途（对应 RESEARCH 31.11 / 37 的「下一步」）：不改 V2 源码，把「9 品种 24h 收益横截面
标准差」的滚动 180d 分位作为旁路指标，在任何给定日期区间上计算，供：
  1) 现在：查看当前/指定区间每天的高-低离散分位，判断当下是否处在 V2 偏强的「高离散周末」环境；
  2) 将来 paper：每个 V2 信号日记录分位（纯监控，不干预下单），累积真实样本外以复核 37 节的衰减结论。

定义（与 31/37 完全一致，不重调）：
  DISP_t = 9 品种 ret24（6 根 4h）在该 bar 的横截面标准差
  PCT_t  = DISP 过去 180d（1080 根 4h，min_periods 360）的滚动分位，shift(1) 防前视
  高离散 = PCT >= 0.80（HI_Q）；低离散 = 其余

用法:
  .venv/Scripts/python.exe user_data/scripts/disp_monitor.py                 # 最近 30 天
  .venv/Scripts/python.exe user_data/scripts/disp_monitor.py --days 90
  .venv/Scripts/python.exe user_data/scripts/disp_monitor.py --since 2026-10-01 --until 2026-11-08
  .venv/Scripts/python.exe user_data/scripts/disp_monitor.py --weekends-only

只读本地 K 线，不联网、不写任何数据、不改策略源码。
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_lib as R  # noqa: E402

ROOT = Path(R.__file__).resolve().parent.parent
DATA = ROOT / "data" / "binance" / "futures"
PAIRS = ["BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "ZEC", "DOGE", "XMR"]
HI_Q = 0.80
WIN = 1080
MINP = 360


def load_ret24(p):
    k = pd.read_feather(DATA / f"{p}_USDT_USDT-4h-futures.feather").sort_values("date")
    k = k.reset_index(drop=True)
    k["ret24"] = k["close"] / k["close"].shift(6) - 1
    return k.set_index("date")["ret24"]


def rpct(s, win=WIN, minp=MINP):
    return s.rolling(win, min_periods=minp).apply(
        lambda x: (x[-1] > x[:-1]).mean(), raw=True).shift(1)


def dispersion_pct():
    """返回 (disp, pct) 两个 Series，全时间线；Sample 分位用滚动历史。"""
    ret24 = pd.DataFrame({p: load_ret24(p) for p in PAIRS})
    disp = ret24.std(axis=1)
    pct = rpct(disp)
    return disp, pct


def main():
    R.enable_utf8_console()
    ap = argparse.ArgumentParser(description="高离散度旁路监控（V2 环境指标，不改源码）")
    ap.add_argument("--days", type=int, default=30, help="最近 N 天（默认 30，与 --since 二选一）")
    ap.add_argument("--since", default=None, help="起始日期 YYYY-MM-DD（覆盖 --days）")
    ap.add_argument("--until", default=None, help="结束日期 YYYY-MM-DD（默认最新）")
    ap.add_argument("--weekends-only", action="store_true", help="只看周六日（V2 主要在周末接刀）")
    args = ap.parse_args()

    disp, pct = dispersion_pct()
    df = pd.DataFrame({"disp": disp, "pct": pct}).dropna()
    if df.empty:
        raise SystemExit("无可用的离散度序列（检查本地 K 线）")

    last = df.index.max()
    if args.since:
        start = pd.Timestamp(args.since, tz="UTC")
    else:
        start = last - pd.Timedelta(days=args.days)
    end = pd.Timestamp(args.until, tz="UTC") + pd.Timedelta(days=1) if args.until else last + pd.Timedelta(hours=1)
    win = df[(df.index >= start) & (df.index < end)].copy()
    if args.weekends_only:
        win = win[win.index.dayofweek.isin([5, 6])]   # 5=Sat 6=Sun (UTC)
    if win.empty:
        raise SystemExit("所选区间无数据")

    print(f"高离散度旁路监控｜规则 pct>={HI_Q} = 高离散｜最新 bar {last:%Y-%m-%d %H:%M} UTC")
    print(f"区间 {win.index.min():%Y-%m-%d} → {win.index.max():%Y-%m-%d}｜共 {len(win)} 根 4h bar"
          f"{'（仅周末）' if args.weekends_only else ''}")
    hi = win[win["pct"] >= HI_Q]
    print(f"  高离散 bar: {len(hi)}/{len(win)} ({100*len(hi)/len(win):.0f}%)")
    print()
    # 最近 15 根明细
    print(f"  {'date(UTC)':<17}{'disp%':>8}{'pct':>8}  状态")
    for ts, row in win.tail(15).iterrows():
        st = "★高离散" if row["pct"] >= HI_Q else ""
        print(f"  {ts:%Y-%m-%d %H:%M}  {row['disp']*100:8.2f}{row['pct']:8.3f}  {st}")

    # 当前状态判定
    cur = df.iloc[-1]
    print()
    print(f"当前最新 bar 分位 PCT = {cur['pct']:.3f} → "
          f"{'高离散（V2 偏强环境）' if cur['pct'] >= HI_Q else '非高离散'}")
    print("解读：分位越高，历史上 V2 急跌接刀交易每笔期望越高（TEST）；"
          "但 37 节样本外显示该优势已衰减大半，仅作环境参考，不单独决定仓位。")


if __name__ == "__main__":
    main()
