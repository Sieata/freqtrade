"""股票/商品/指数永续专项研究（H9，RESEARCH 十五）。

三个子研究（描述性+事件研究，无 TEST/VAL 可用——合约 2025-12 后才上市，样本即全部历史）：
  H9a funding 扫描: 各品种 funding APR（carry/对冲成本定量的基础）
  H9b 周末效应: 美股永续的 V2 式周末回调买入（周末低流动性 + 周一美股开盘反转假设）
  H9c 时段结构: UTC 小时级波动/收益分布（美股常规时段 13:30-20:00 UTC vs 其余 24/7 时段）

用法: .venv/bin/python user_data/scripts/equity_perp_phase1.py
"""
import numpy as np
import pandas as pd

FUT = "user_data/data/binance/futures"
SYMBOLS = ["XAU", "TSLA", "MSTR", "NVDA", "AAPL", "QQQ", "SPY", "SOXL", "GME",
           "SPCX", "OPENAI", "ANTHROPIC", "TENCENT", "CXMT", "UNITREE"]
US_OPEN_UTC = 13.5   # 美股常规开盘 13:30 UTC（夏令时近似）
US_CLOSE_UTC = 20.0  # 收盘 20:00 UTC


def load_kline(sym):
    try:
        df = pd.read_feather(f"{FUT}/{sym}_USDT_USDT-4h-futures.feather")
    except FileNotFoundError:
        return None
    return df[["date", "open", "high", "low", "close", "volume"]].set_index("date").sort_index()


def load_funding(sym):
    try:
        f = pd.read_feather(f"{FUT}/{sym}_USDT_USDT-1h-funding_rate.feather")
    except FileNotFoundError:
        return None
    s = f[f["open"] != 0].set_index("date")["open"]
    return s[~s.index.duplicated(keep="last")]


print("=" * 100)
print("H9a funding 扫描（空头收正费率 = 持股对冲成本/收益；基准 = 全样本均值×3×365）")
print("=" * 100)
for sym in SYMBOLS:
    s = load_funding(sym)
    if s is None or len(s) == 0:
        print(f"  {sym:<11} funding 无数据")
        continue
    apr = s.mean() * 3 * 365 * 100
    neg = (s < 0).mean() * 100
    print(f"  {sym:<11} 结算 {len(s):>4} 条  APR {apr:>+7.1f}%  负费率占比 {neg:>4.0f}%  "
          f"中位 {s.median() * 1e4:>+6.2f}bp")

print()
print("=" * 100)
print("H9b 周末效应（美股永续，样本 ≥90 天的 7 个；周末窗口=Sat/Sun UTC，信号收盘确认次根开盘入场）")
print("=" * 100)
WEEKEND_SYMS = ["XAU", "TSLA", "MSTR", "NVDA", "AAPL", "QQQ", "SPY"]
FEE_RT = 0.001
for sym in WEEKEND_SYMS:
    df = load_kline(sym)
    if df is None or (df.index.max() - df.index.min()).days < 90:
        continue
    df["ret1"] = df["close"].pct_change()
    df["dow"] = df.index.dayofweek
    # V2 式: 周末内前一根跌超 2% 且当前收阳 → 次根开盘买入，持有 3 根（12h，赌周一美股开盘续势）
    sig = (df["dow"] >= 5) & (df["ret1"].shift(1) < -0.02) & (df["close"] > df["open"])
    idx = np.where(sig.reindex(df.index).fillna(False).values)[0]
    rets = []
    for i in idx:
        if i + 4 < len(df):
            rets.append(df["open"].iloc[i + 1 + 3] / df["open"].iloc[i + 1] - 1 - FEE_RT)
    if rets:
        r = np.array(rets)
        print(f"  {sym:<6} n={len(r):>3}  win={100 * (r > 0).mean():.0f}%  mean={r.mean() * 100:+.2f}%  "
              f"median={np.median(r) * 100:+.2f}%")

print()
print("=" * 100)
print("H9c 时段结构（US 开市 13:30-20:00 UTC vs 收市时段；小时收益率绝对值均值 + 方向性）")
print("=" * 100)
for sym in ["NVDA", "TSLA", "MSTR", "QQQ", "SPY"]:
    df = load_kline(sym)
    if df is None:
        continue
    df["ret1"] = df["close"].pct_change()
    df["hour"] = df.index.hour
    # 4h 蜡烛归到开盘小时; 美股常规时段覆盖的 4h 蜡烛 = 12:00 与 16:00 两根
    df["sess"] = np.where(df["hour"].isin([12, 16]), "open", "closed")
    g = df.groupby("sess")["ret1"].agg(open_abs=lambda x: x.abs().mean() * 100,
                                       open_win=lambda x: (x > 0).mean() * 100, n="count")
    if "open" in g.index and "closed" in g.index:
        ratio = g.loc["open", "open_abs"] / g.loc["closed", "open_abs"]
        print(f"  {sym:<6} 开市时段波动 {g.loc['open', 'open_abs']:.2f}%  收市时段 {g.loc['closed', 'open_abs']:.2f}%  "
              f"比值 {ratio:.1f}x  (n={int(g['n'].sum())})")
