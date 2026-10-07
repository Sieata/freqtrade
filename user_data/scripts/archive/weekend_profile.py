"""基线画像 — 分析 WeekendReverseV1 每笔交易明细"""
import json, zipfile
import pandas as pd
import numpy as np

ZIP = "user_data/backtest_results/backtest-result-2026-08-13_08-31-11.zip"
with zipfile.ZipFile(ZIP) as z:
    data = json.loads(z.read(z.namelist()[0]))
s = data["strategy"]["WeekendReverseV1"]
trades = s["trades"]
df = pd.DataFrame(trades)
print(f"总笔数: {len(df)}  | 胜率 {s['winrate']*100:.1f}%  | 总利润 ${s['profit_total_abs']:,.0f}  | 回撤 {s['max_drawdown_account']*100:.1f}%")

# 1. 离场方式分布(现成 summary)
print("\n=== 离场方式分布 ===")
print(json.dumps(s["exit_reason_summary"], indent=2, ensure_ascii=False))

# 2. 每品种贡献(现成 results_per_pair)
print("\n=== 每品种 (profit_total_pct 为该品种累计%) ===")
rpp = pd.DataFrame(s["results_per_pair"]).set_index("key")
rpp = rpp.sort_values("profit_total_abs", ascending=False)
cols = ["trades", "profit_total_abs", "profit_mean_pct", "winrate", "max_drawdown_abs"]
print(rpp[cols].round(2).to_string())

# 3. MFE/MAE 分析(关键: 止损缓冲是否被用到)
df["mfe"] = (df["max_rate"] - df["open_rate"]) / df["open_rate"]   # 最大有利偏移
df["mae"] = (df["min_rate"] - df["open_rate"]) / df["open_rate"]   # 最大不利偏移(负)
print("\n=== MFE/MAE 分析 ===")
print(f"MFE 中位数: {df['mfe'].median()*100:.2f}%  | MAE 中位数: {df['mae'].median()*100:.2f}%")
print(f"MFE 均值:   {df['mfe'].mean()*100:.2f}%  | MAE 均值:   {df['mae'].mean()*100:.2f}%")
# 有多少笔 MAE 触及了止损线附近(<-8%)
near_stop = (df["mae"] < -0.08).sum()
print(f"MAE < -8% (触及止损缓冲) 的笔数: {near_stop} ({near_stop/len(df)*100:.1f}%)")
# 有多少笔 MAE 很浅(>-3%, 即止损完全没被用到)
shallow = (df["mae"] > -0.03).sum()
print(f"MAE > -3% (完全不需要宽止损) 的笔数: {shallow} ({shallow/len(df)*100:.1f}%)")

# 4. 入场星期分布
print("\n=== 入场星期分布 ===")
if "weekday" in df.columns:
    print(df["weekday"].value_counts().sort_index().to_string())

# 5. 持有期
print("\n=== 持有期(小时) ===")
df["dur_h"] = df["trade_duration"] / 60
print(df["dur_h"].describe().round(1).to_string())

# 6. 单笔收益分位数
print("\n=== 单笔收益分位数(%) ===")
print((df["profit_ratio"].quantile([0, .01, .05, .25, .5, .75, .9, .99, 1.0]) * 100).round(2).to_string())

# 7. 极端交易
print("\n=== 极端交易 ===")
bw = df[df["profit_ratio"] > 0.05]
bl = df[df["profit_ratio"] < -0.05]
print(f"盈利>5%: {len(bw)} 笔, 利润贡献 {bw['profit_abs'].sum():,.0f} USDT")
print(f"亏损<-5%: {len(bl)} 笔, 亏损贡献 {bl['profit_abs'].sum():,.0f} USDT")
