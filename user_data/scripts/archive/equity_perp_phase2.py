"""股票永续 H9 深化（phase2）：费率收割案例分解 + 盘后事件定价事件研究。

Case A: CXMT/TENCENT 新上市极端费率——"上市即建仓"的资金费收益 vs 方向风险分解。
Case C: 美股永续收市时段大波动后，下一开市时段是延续还是回归（T+1 逃离交易化检验）。

用法: .venv/bin/python user_data/scripts/equity_perp_phase2.py
"""
import numpy as np
import pandas as pd

FUT = "user_data/data/binance/futures"
US_OPEN_HOURS = {12, 16}          # 4h 蜡烛与美股常规时段重叠的小时


def load(sym):
    df = pd.read_feather(f"{FUT}/{sym}_USDT_USDT-4h-futures.feather")
    return df[["date", "open", "high", "low", "close"]].set_index("date").sort_index()


def load_funding(sym):
    f = pd.read_feather(f"{FUT}/{sym}_USDT_USDT-1h-funding_rate.feather")
    s = f[f["open"] != 0].set_index("date")["open"]
    return s[~s.index.duplicated(keep="last")]


print("=" * 100)
print("Case A: 新上市极端费率——'上市即建仓'分解（资金费收益 vs 方向风险，截至最新）")
print("=" * 100)
for sym, direction in [("CXMT", "long"), ("UNITREE", "long"), ("TENCENT", "short")]:
    df = load(sym)
    fund = load_funding(sym)
    first, last = df.index[0], df.index[-1]
    px_ret = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    dir_sign = 1 if direction == "long" else -1
    price_pnl = dir_sign * px_ret
    fund_win = fund[(fund.index > first) & (fund.index <= last)]
    fund_sum = fund_win.sum()
    # 费率收付: f>0 多头付空头收; f<0 多头收空头付 → 多头资金费损益 = -Σf, 空头 = +Σf
    dir_fund = -dir_sign * fund_sum
    total = price_pnl + dir_fund
    # 方向腿的最大不利波动（简化的持仓期 MDD）
    path = dir_sign * (df["close"] / df["close"].iloc[0] - 1)
    mdd = (path - path.cummax()).min()
    days = (last - first).days
    print(f"\n  {sym}（{direction}，{days} 天，费率结算 {len(fund_win)} 次）")
    print(f"    方向价格损益 {price_pnl * 100:+.1f}%  资金费损益 {dir_fund * 100:+.1f}%  "
          f"合计 {total * 100:+.1f}%")
    print(f"    方向腿最大回撤 {mdd * 100:+.1f}%  （资金费是方向风险的 {'垫子' if abs(dir_fund) > abs(price_pnl) else '零头'}）")

print()
print("=" * 100)
print("Case C: 收市时段大波动 → 下一开市时段（延续 or 回归？）——4h 蜡烛口径")
print("=" * 100)
for sym in ["NVDA", "TSLA", "MSTR", "AAPL", "QQQ", "SPY"]:
    df = load(sym)
    df["ret4"] = df["close"] / df["open"] - 1          # 蜡烛内收益（近似时段收益）
    df["hour"] = df.index.hour
    df["sess"] = np.where(df["hour"].isin(US_OPEN_HOURS), "open", "closed")
    # 找收市时段大波动蜡烛（|ret|≥2%），其后的第一根开市蜡烛的开→收收益
    res_drop, res_pump = [], []
    idx = df.index
    for i in range(len(df) - 1):
        if df["sess"].iloc[i] != "closed" or abs(df["ret4"].iloc[i]) < 0.02:
            continue
        j = i + 1
        while j < len(df) and df["sess"].iloc[j] != "open":
            j += 1
        if j >= len(df):
            break
        r_open = df["close"].iloc[j] / df["open"].iloc[j] - 1
        (res_pump if df["ret4"].iloc[i] > 0 else res_drop).append(r_open)
    for label, arr in [("收市暴跌→开市", res_drop), ("收市暴涨→开市", res_pump)]:
        if len(arr) >= 5:
            a = np.array(arr)
            print(f"  {sym:<6} {label}: n={len(a):>3}  开市延续/反转? mean={a.mean() * 100:+.2f}%  "
                  f"win={100 * (a > 0).mean():.0f}%（正=延续跌/涨，负=回归）")
    if not res_drop and not res_pump:
        print(f"  {sym:<6} 样本不足")
