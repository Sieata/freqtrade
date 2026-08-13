"""诊断两类亏损交易: stop_loss(34笔) 和 exit_signal/EMA(47笔)"""
import json, zipfile
import pandas as pd
import numpy as np

ZIP = "user_data/backtest_results/backtest-result-2026-08-13_08-31-11.zip"
with zipfile.ZipFile(ZIP) as z:
    data = json.loads(z.read(z.namelist()[0]))
s = data["strategy"]["WeekendReverseV1"]
df = pd.DataFrame(s["trades"])

df["mfe"] = (df["max_rate"] - df["open_rate"]) / df["open_rate"]
df["mae"] = (df["min_rate"] - df["open_rate"]) / df["open_rate"]
df["dur_h"] = df["trade_duration"] / 60

# 入场星期/时段(用 open_timestamp)
df["open_ts"] = pd.to_datetime(df["open_timestamp"], unit="ms")
df["open_dow"] = df["open_ts"].dt.dayofweek
df["open_hour_utc"] = df["open_ts"].dt.hour

stop = df[df["exit_reason"] == "stop_loss"]
ema = df[df["exit_reason"] == "exit_signal"]
trail = df[df["exit_reason"] == "trailing_stop_loss"]

print("=" * 70)
print("STOP LOSS trades (34) — 最大亏损源 -$82,772")
print("=" * 70)
print(f"n={len(stop)}  avg profit={stop['profit_ratio'].mean()*100:.2f}%")
print("\n按品种:")
print(stop.groupby("pair").agg(n=("profit_ratio","size"), mae_mean=("mae",lambda x:x.mean()*100),
      mfe_mean=("mfe",lambda x:x.mean()*100)).round(2).to_string())
print("\nMFE 分布(入场后是否曾经盈利过):")
print(f"  MFE>0 (曾盈利) 的笔数: {(stop['mfe']>0).sum()} / {len(stop)}")
print(f"  MFE 中位数: {stop['mfe'].median()*100:.2f}%  | 均值: {stop['mfe'].mean()*100:.2f}%")
print("\n入场星期(open_dow):")
print(stop["open_dow"].value_counts().sort_index().to_string())
print("\n入场时段(open_hour_utc):")
print(stop["open_hour_utc"].value_counts().sort_index().to_string())
print(f"\n持有期: 均值 {stop['dur_h'].mean():.1f}h, 中位 {stop['dur_h'].median():.1f}h")

print()
print("=" * 70)
print("EXIT_SIGNAL/EMA trades (47) — 次亏损源 -$12,221, 胜率仅 12.8%")
print("=" * 70)
print(f"n={len(ema)}  avg profit={ema['profit_ratio'].mean()*100:.2f}%")
print("\n按品种:")
print(ema.groupby("pair").agg(n=("profit_ratio","size"), profit=("profit_ratio",lambda x:x.mean()*100),
      mae_mean=("mae",lambda x:x.mean()*100), mfe_mean=("mfe",lambda x:x.mean()*100)).round(2).to_string())
print(f"\nMFE 中位数: {ema['mfe'].median()*100:.2f}%  | MAE 中位数: {ema['mae'].median()*100:.2f}%")
print(f"MFE>0 笔数: {(ema['mfe']>0).sum()}/{len(ema)}  | MAE<-8% 笔数: {(ema['mae']<-0.08).sum()}")
print(f"\n持有期: 均值 {ema['dur_h'].mean():.1f}h, 中位 {ema['dur_h'].median():.1f}h")
print("\n入场星期(open_dow):")
print(ema["open_dow"].value_counts().sort_index().to_string())

print()
print("=" * 70)
print("对照组 TRAILING(450) — 盈利主力")
print("=" * 70)
print(f"MFE 中位数 {trail['mfe'].median()*100:.2f}% | MAE 中位数 {trail['mae'].median()*100:.2f}%")
print(f"profit 中位数 {trail['profit_ratio'].median()*100:.2f}% | 均值 {trail['profit_ratio'].mean()*100:.2f}%")
print(f"持有期 均值 {trail['dur_h'].mean():.1f}h, 中位 {trail['dur_h'].median():.1f}h")
