"""V2 × FundingSqueeze 组合互补性分析（TEST 口径，独立 $1,000/笔）。

输入：最近两个回测 zip（WeekendReverseV2 与 FundingSqueezeV1L，同池 11 品种 × TEST）。
输出：月度 P&L 相关系数、同品种持仓时间重叠率、合并组合统计。
用法: .venv/Scripts/python.exe user_data/scripts/fs_portfolio_check.py
"""
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BT = Path("user_data/backtest_results")


def load_trades(strategy):
    zips = sorted(BT.glob("backtest-result-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for zp in zips:
        with zipfile.ZipFile(zp) as z:
            for n in z.namelist():
                if not n.endswith(".json"):
                    continue
                d = json.loads(z.read(n))
                if isinstance(d, dict) and strategy in d.get("strategy", {}):
                    tr = d["strategy"][strategy].get("trades", [])
                    if tr:
                        return pd.DataFrame(tr), zp.name
    raise SystemExit(f"找不到 {strategy} 的回测结果")


def month_key(s):
    return s[:7]


v2, z2 = load_trades("WeekendReverseV2")
fs, zf = load_trades("FundingSqueezeV1L")
print(f"V2: {z2} ({len(v2)} 笔) | FS-V1L: {zf} ({len(fs)} 笔)")

for df in (v2, fs):
    df["open_dt"] = pd.to_datetime(df["open_date"], utc=True)
    df["close_dt"] = pd.to_datetime(df["close_date"], utc=True)
    df["profit$"] = df["profit_ratio"] * 1000
    df["month"] = df["close_dt"].dt.strftime("%Y-%m")

# 1) 月度 P&L 相关
mv = v2.groupby("month")["profit$"].sum()
mf = fs.groupby("month")["profit$"].sum()
cal = pd.concat([mv, mf], axis=1, keys=["V2", "FS"]).fillna(0.0)
print(f"\n月度 P&L 相关系数 (TEST {cal.index[0]}~{cal.index[-1]}, n={len(cal)} 个月): "
      f"{cal['V2'].corr(cal['FS']):+.3f}")
print(f"V2  月度: 均值 {cal['V2'].mean():+,.0f}$ 最差 {cal['V2'].min():+,.0f}$ | 负月占比 "
      f"{(cal['V2'] < 0).mean() * 100:.0f}%")
print(f"FS  月度: 均值 {cal['FS'].mean():+,.0f}$ 最差 {cal['FS'].min():+,.0f}$ | 负月占比 "
      f"{(cal['FS'] < 0).mean() * 100:.0f}%")
cal["sum"] = cal["V2"] + cal["FS"]
print(f"合并: 均值 {cal['sum'].mean():+,.0f}$ 最差 {cal['sum'].min():+,.0f}$ | 负月占比 "
      f"{(cal['sum'] < 0).mean() * 100:.0f}%  (合并最差月 >= 两策略各自最差月的更差者: "
      f"{cal['sum'].min() >= min(cal['V2'].min(), cal['FS'].min())})")

# 2) 同品种持仓时间重叠（资金占用冲突）
overlap = 0
for _, t in fs.iterrows():
    m = v2[(v2["pair"] == t["pair"]) & (v2["open_dt"] <= t["close_dt"]) & (v2["close_dt"] >= t["open_dt"])]
    if len(m):
        overlap += 1
print(f"\n同品种持仓时间重叠: {overlap}/{len(fs)} 笔 FS 持仓期间 V2 同品种在仓 "
      f"({100 * overlap / len(fs):.0f}%，组合资金峰值占用参考)")

# 3) 合并权益的回撤对比（独立口径月度近似）
eq = cal[["V2", "FS", "sum"]].cumsum()
for c in ("V2", "FS", "sum"):
    dd = (eq[c] - eq[c].cummax()).min()
    print(f"{c:>4} 累计 ${eq[c].iloc[-1]:>+8,.0f}  月度口径最大回撤 ${dd:,.0f}")
