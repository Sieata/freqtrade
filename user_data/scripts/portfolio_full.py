"""四臂全组合回测（V2 + FS + OIFlushV2 + BigMove，TOP10 池，sleeve 模型独立 $1,000/笔）。

输出：单臂/合并的逐年收益率（每年重置本金：单臂 $1,000、四臂 $4,000）、月度统计、
并发峰值、月度相关矩阵、leave-one-out 边际贡献，以及资金分配建议的量化依据。

用法: .venv/bin/python user_data/scripts/portfolio_full.py
"""
import json
import re
import zipfile
from pathlib import Path

import pandas as pd

BT = Path("user_data/backtest_results")
REPORTS = Path("user_data/reports")
UNIVERSE = Path("user_data/universe/pairs_top10.txt")
SPLIT = pd.Timestamp("2024-08-28", tz="UTC")
STAKE = 1000
ARMS = ["WeekendReverseV2", "FundingSqueezeV1L", "OIFlushV2", "BigMoveV1"]
SHORT = {"WeekendReverseV2": "V2", "FundingSqueezeV1L": "FS", "OIFlushV2": "OI", "BigMoveV1": "BM"}

POOL = {line.split("/")[0].strip() for line in UNIVERSE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#") and "/" in line}


def load_arm(strategy):
    r = sorted(REPORTS.glob(f"validate_{strategy}_*.md"), key=lambda p: p.stat().st_mtime)[-1]
    txt = r.read_text(encoding="utf-8")
    zips = {}
    for pat, key in [(r"## \w+ × TEST（20220101-20240828）", "TEST"),
                     (r"## \w+ × VAL（20240828-）", "VAL")]:
        m = re.search(pat, txt)
        zips[key] = re.search(r"结果: `(backtest-result-[0-9_-]+\.zip)`", txt[m.end():].split("## ")[0]).group(1)
    parts = []
    for zname in zips.values():
        with zipfile.ZipFile(BT / zname) as z:
            for n in z.namelist():
                if not n.endswith(".json"):
                    continue
                d = json.loads(z.read(n))
                if isinstance(d, dict) and "strategy" in d:
                    parts.extend(d["strategy"][list(d["strategy"])[0]].get("trades", []))
    df = pd.DataFrame(parts)
    df["open_dt"] = pd.to_datetime(df["open_date"], utc=True)
    df["close_dt"] = pd.to_datetime(df["close_date"], utc=True)
    df["profit$"] = df["profit_ratio"] * STAKE
    df["pair_base"] = df["pair"].str.split("/").str[0]
    return df[df["pair_base"].isin(POOL)].reset_index(drop=True)


def peak_concurrency(df):
    events = []
    for _, t in df.iterrows():
        events.append((t["open_dt"], 1))
        events.append((t["close_dt"], -1))
    events.sort(key=lambda e: (e[0], e[1]))
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def block(name, df, base):
    years_span = max((df["close_dt"].max() - df["open_dt"].min()).days / 365.25, 0.1)
    m = df.groupby(df["close_dt"].dt.strftime("%Y-%m"))["profit$"].sum()
    eq = m.cumsum()
    yr = df.groupby(df["close_dt"].dt.year)["profit$"].sum()
    ys = " ".join(f"{y}:{v / base * 100:+.0f}%" for y, v in yr.items())
    print(f"  {name:<8} {len(df):>5}笔  年均 {df['profit$'].sum() / years_span:>+8,.0f}$  "
          f"最差月 {m.min():>+7,.0f}$({m.min() / base * 100:+.0f}%)  负月 {(m < 0).mean() * 100:>3.0f}%  "
          f"月度回撤 {(eq - eq.cummax()).min():>+8,.0f}$  逐年({base / 1000:.0f}k): {ys}")
    return m


def main():
    arms = {SHORT[a]: load_arm(a) for a in ARMS}
    print("=" * 104)
    print("【TEST 20220101-20240828，TOP10，sleeve 独立 $1,000/笔】")
    print("=" * 104)
    parts_test = {k: v[v["close_dt"] < SPLIT] for k, v in arms.items()}
    base_test = STAKE * len(arms)
    monthly = {}
    for k in [SHORT[a] for a in ARMS]:
        monthly[k] = block(k, parts_test[k], STAKE)
    comb = pd.concat(parts_test.values())
    monthly["组合"] = block("组合(4臂)", comb, base_test)
    print(f"  并发峰值: {peak_concurrency(comb)} 仓（4 臂 × 各自上限，独立资金模型）")

    print()
    print("=" * 104)
    print("【VAL 20240828-】")
    print("=" * 104)
    parts_val = {k: v[v["close_dt"] >= SPLIT] for k, v in arms.items()}
    monthly_v = {}
    for k in [SHORT[a] for a in ARMS]:
        monthly_v[k] = block(k, parts_val[k], STAKE)
    comb_v = pd.concat(parts_val.values())
    monthly_v["组合"] = block("组合(4臂)", comb_v, base_test)
    print(f"  并发峰值: {peak_concurrency(comb_v)} 仓")

    # 相关矩阵（VAL 月度）
    print("\n月度 P&L 相关矩阵（VAL）:")
    cal = pd.DataFrame(monthly_v)
    print(cal.corr().round(2).to_string())

    # leave-one-out 边际贡献（VAL）
    print("\nleave-one-out 边际贡献（VAL，去掉该臂后组合年化下降多少）:")
    span = max((comb_v["close_dt"].max() - comb_v["close_dt"].min()).days / 365.25, 0.1)
    total = comb_v["profit$"].sum()
    base_all_ann = total / span / base_test * 100
    print(f"  四臂组合年化(按 {base_test / 1000:.0f}k 年度分配基数): {base_all_ann:+.1f}%")
    for k in [SHORT[a] for a in ARMS]:
        arm_profit = parts_val[k]["profit$"].sum()
        without = total - arm_profit
        pp = arm_profit / span / STAKE * 100
        print(f"  去 {k:<4}: 组合年化 {without / span / base_test * 100:+.1f}%（边际 {pp:+.1f}pp）")


if __name__ == "__main__":
    main()
