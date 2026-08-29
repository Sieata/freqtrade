"""V2 × FundingSqueezeV1L 组合回测（双臂 sleeve 模型，独立口径 $1,000/笔）。

部署模型 = 两个独立 bot 各自 $1,000/笔（config_paper_v2 / config_paper_fs），因此组合回测
= 两臂交易集合并，无共享资金竞争。输出 TEST/VAL 两段：
  - 单臂 vs 合并：trades / 利润 / 月均 / 最差月 / 负月占比 / 月度权益最大回撤 / 逐年
  - 月度 P&L 相关系数
  - 双臂并发峰值（资金共享可行性参考）与同品种持仓重叠

用法: .venv/bin/python user_data/scripts/fs_portfolio_backtest.py
"""
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

BT = Path("user_data/backtest_results")
REPORTS = Path("user_data/reports")
SPLIT_DATE = pd.Timestamp("2024-08-28", tz="UTC")
# 评估池：TOP10 实盘优先池（2026-08-29 评估纪律）
UNIVERSE = Path("user_data/universe/pairs_top10.txt")
POOL = {line.split("/")[0].strip() for line in UNIVERSE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#") and "/" in line}


def load_strategy_trades(strategy):
    """收集所有该策略的回测 zip 交易，按 (pair, open_date) 去重。"""
    rows = []
    for zp in sorted(BT.glob("backtest-result-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        with zipfile.ZipFile(zp) as z:
            for n in z.namelist():
                if not n.endswith(".json"):
                    continue
                d = json.loads(z.read(n))
                if isinstance(d, dict) and strategy in d.get("strategy", {}):
                    rows.extend(d["strategy"][strategy].get("trades", []))
    df = pd.DataFrame(rows).drop_duplicates(subset=["pair", "open_date", "close_date", "profit_ratio"])
    df["open_dt"] = pd.to_datetime(df["open_date"], utc=True)
    df["close_dt"] = pd.to_datetime(df["close_date"], utc=True)
    return df


def load_fs_by_leg():
    """从最新的 V1L 验证报告解析两腿 zip（CORE×TEST / CORE×VAL）。"""
    r = sorted(REPORTS.glob("validate_FundingSqueezeV1L_*.md"), key=lambda p: p.stat().st_mtime)[-1]
    txt = r.read_text(encoding="utf-8")
    out = {}
    for pat, key in [(r"## \w+ × TEST（20220101-20240828）", "TEST"),
                     (r"## \w+ × VAL（20240828-）", "VAL")]:
        m = re.search(pat, txt)
        if not m:
            raise SystemExit(f"{r.name}: 找不到 {pat}")
        zname = re.search(r"结果: `(backtest-result-[0-9_-]+\.zip)`", txt[m.end():].split("## ")[0]).group(1)
        with zipfile.ZipFile(BT / zname) as z:
            for n in z.namelist():
                if n.endswith(".json"):
                    d = json.loads(z.read(n))
                    if isinstance(d, dict) and "strategy" in d:
                        out[key] = pd.DataFrame(list(d["strategy"].values())[0]["trades"])
    for k in out:
        out[k]["open_dt"] = pd.to_datetime(out[k]["open_date"], utc=True)
        out[k]["close_dt"] = pd.to_datetime(out[k]["close_date"], utc=True)
        out[k] = out[k][out[k]["pair"].str.split("/").str[0].isin(POOL)].reset_index(drop=True)
    return out


def peak_concurrency(df):
    events = []
    for _, t in df.iterrows():
        events.append((t["open_dt"], 1))
        events.append((t["close_dt"], -1))
    events.sort(key=lambda e: (e[0], e[1]))  # 先 -1 后 +1 同刻不重叠
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def same_pair_overlap(v2, fs):
    n = 0
    for _, t in fs.iterrows():
        m = v2[(v2["pair"] == t["pair"]) & (v2["open_dt"] <= t["close_dt"]) & (v2["close_dt"] >= t["open_dt"])]
        if len(m):
            n += 1
    return n


def stats(df, label):
    df = df.copy()
    df["profit$"] = df["profit_ratio"] * 1000
    df["month"] = df["close_dt"].dt.strftime("%Y-%m")
    m = df.groupby("month")["profit$"].sum()
    eq = m.cumsum()
    mdd = (eq - eq.cummax()).min()
    yr = df.groupby(df["close_dt"].dt.year)["profit$"].sum()
    ys = ", ".join(f"{y}:{v:+,.0f}" for y, v in yr.items())
    return {
        "label": label, "n": len(df), "total": df["profit$"].sum(),
        "m_mean": m.mean(), "m_min": m.min(), "neg%": (m < 0).mean() * 100,
        "mdd": mdd, "years": ys, "monthly": m,
    }


def report_period(v2, fs, name):
    print(f"\n{'=' * 92}\n【{name}】 V2={len(v2)}笔  FS={len(fs)}笔  合并={len(v2) + len(fs)}笔\n{'=' * 92}")
    s_v2 = stats(v2, "V2")
    s_fs = stats(fs, "FS")
    s_all = stats(pd.concat([v2, fs]), "合并")
    print(f"{'':<6}{'trades':>7}{'利润$':>10}{'月均$':>8}{'最差月$':>9}{'负月%':>7}{'月度回撤$':>10}")
    for s in (s_v2, s_fs, s_all):
        print(f"{s['label']:<6}{s['n']:>7}{s['total']:>+10,.0f}{s['m_mean']:>+8,.0f}"
              f"{s['m_min']:>+9,.0f}{s['neg%']:>7.0f}{s['mdd']:>+10,.0f}")
    corr = s_v2["monthly"].corr(s_fs["monthly"])
    print(f"\n月度 P&L 相关系数: {corr:+.3f}")
    print(f"合并逐年: {s_all['years']}")
    pc = peak_concurrency(pd.concat([v2, fs]))
    print(f"双臂并发峰值: {pc} 仓（两 bot 各自上限 8/11，独立资金模型下仅作资金规划参考）")
    ov = same_pair_overlap(v2, fs)
    print(f"同品种持仓重叠: {ov}/{len(fs)} 笔 FS 持仓期间 V2 同品种在仓")


def main():
    v2_all = load_strategy_trades("WeekendReverseV2")
    fs_legs = load_fs_by_leg()
    v2_all = v2_all[v2_all["pair"].str.split("/").str[0].isin(POOL)]
    v2_test = v2_all[v2_all["close_dt"] < SPLIT_DATE]
    v2_val = v2_all[v2_all["close_dt"] >= SPLIT_DATE]
    report_period(v2_test, fs_legs["TEST"], "TEST 20220101-20240828（TOP10 池）")
    report_period(v2_val, fs_legs["VAL"], "VAL 20240828-（TOP10 池）")


if __name__ == "__main__":
    main()
