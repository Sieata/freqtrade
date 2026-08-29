"""Tier B 事件臂评估表（门禁分层提案 GATE_TIERING_PROPOSAL.md 的实测工具）。

对指定事件臂策略计算提案中的增量门禁（TOP10 口径，独立 $1,000/笔）：
  门禁4 信号重叠: 臂与 V2 同品种同 4h 入场的占比 ≤ 30%
  门禁5 组合增量: 加臂后组合年化(钱包) − V2 单独年化 ≥ +3pp（TEST/VAL 分别报告）
  门禁6 最差月归一: 合并最差月 ÷ 双臂资金(2×$1,000) vs V2 最差月 ≤ 1.5
  门禁7 负年: VAL 段逐年收益率（÷$1,000）负年 ≤ 1 且最深 ≥ -15%

用法: .venv/bin/python user_data/scripts/tier_b_eval.py            # 全部三个候选
      .venv/bin/python user_data/scripts/tier_b_eval.py --arm OIFlushV2
"""
import argparse
import json
import re
import zipfile
from pathlib import Path

import pandas as pd

BT = Path("user_data/backtest_results")
REPORTS = Path("user_data/reports")
UNIVERSE = Path("user_data/universe/pairs_top10.txt")
SPLIT = pd.Timestamp("2024-08-28", tz="UTC")
WALLET = 12000  # TOP10 × 1.2 × $1,000
STAKE = 1000

POOL = {line.split("/")[0].strip() for line in UNIVERSE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#") and "/" in line}
ARMS = ["FundingSqueezeV1L", "OIFlushV2", "BigMoveV1"]


def load_leg_zips(strategy):
    """从该策略最新的验证报告解析 TOP10 两腿 zip（单一并发口径，避免混入其他运行的交易）。"""
    r = sorted(REPORTS.glob(f"validate_{strategy}_*.md"), key=lambda p: p.stat().st_mtime)[-1]
    txt = r.read_text(encoding="utf-8")
    zips = {}
    for pat, key in [(r"## \w+ × TEST（20220101-20240828）", "TEST"),
                     (r"## \w+ × VAL（20240828-）", "VAL")]:
        m = re.search(pat, txt)
        if not m:
            raise SystemExit(f"{r.name}: 找不到 {pat}")
        zips[key] = re.search(r"结果: `(backtest-result-[0-9_-]+\.zip)`", txt[m.end():].split("## ")[0]).group(1)
    return zips["TEST"], zips["VAL"], r.name


def load_arm(strategy):
    zt, zv, report = load_leg_zips(strategy)
    parts = []
    for zname in (zt, zv):
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
    return df[df["pair_base"].isin(POOL)].reset_index(drop=True), report


def summarize(df):
    years = max((df["close_dt"].max() - df["open_dt"].min()).days / 365.25, 0.1)
    m = df.groupby(df["close_dt"].dt.strftime("%Y-%m"))["profit$"].sum()
    yearly = df.groupby(df["close_dt"].dt.year)["profit$"].sum()
    return {
        "n": len(df), "total": df["profit$"].sum(), "years": years,
        "ann": df["profit$"].sum() / years / WALLET,
        "worst": m.min(), "yearly": yearly,
    }


def eval_arm(arm, v2_test, v2_val):
    arm_all, report = load_arm(arm)
    out = {"arm": arm, "report": report}
    for name in ("TEST", "VAL"):
        seg_test = name == "TEST"
        a = arm_all[arm_all["close_dt"] < SPLIT] if seg_test else arm_all[arm_all["close_dt"] >= SPLIT]
        v = v2_test if seg_test else v2_val
        comb = pd.concat([a, v])
        s_a, s_v, s_c = summarize(a), summarize(v), summarize(comb)
        # 门禁4: 同品种同 4h 入场重叠
        v2_keys = set(zip(v["pair"], v["open_dt"]))
        overlap = sum(1 for _, t in a.iterrows() if (t["pair"], t["open_dt"]) in v2_keys)
        ov_pct = 100 * overlap / len(a) if len(a) else 0
        # 门禁6: 合并最差月÷双臂资金(2000) vs V2 最差月÷单臂资金(1000)
        g6 = (abs(s_c["worst"]) / 2000) / (abs(s_v["worst"]) / 1000) if s_v["worst"] != 0 else float("inf")
        out[name] = {
            "n": s_a["n"], "profit": s_a["total"], "ann_arm": s_a["ann"], "ann_v2": s_v["ann"],
            "ann_comb": s_c["ann"],
            "g4": ov_pct, "g5": (s_c["ann"] - s_v["ann"]) * 100,
            "g6": g6, "g6_worst_comb": s_c["worst"], "g6_worst_v2": s_v["worst"],
            "yearly": s_a["yearly"],
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default=None)
    args = ap.parse_args()
    arms = [args.arm] if args.arm else ARMS
    v2_all, v2_report = load_arm("WeekendReverseV2")
    v2_test = v2_all[v2_all["close_dt"] < SPLIT]
    v2_val = v2_all[v2_all["close_dt"] >= SPLIT]
    print(f"基准 V2: {v2_report}")

    for arm in arms:
        r = eval_arm(arm, v2_test, v2_val)
        print(f"\n{'=' * 100}\n【{arm}】")
        for seg in ("TEST", "VAL"):
            x = r[seg]
            print(f"  {seg}: {x['n']}笔  臂年化 {x['ann_arm'] * 100:+.1f}%  V2年化 {x['ann_v2'] * 100:+.1f}%  "
                  f"组合年化 {x['ann_comb'] * 100:+.1f}%")
        for seg in ("TEST", "VAL"):
            x = r[seg]
            g4 = "✅" if x["g4"] <= 30 else "❌"
            g5 = "✅" if (x["g5"] >= 3 or seg == "TEST") else ("✅" if x["g5"] >= 3 else "❌")
            g6 = "✅" if x["g6"] <= 1.5 else "❌"
            print(f"  [{seg}] 门禁4 重叠 {x['g4']:.0f}%{g4}  门禁5 组合增量 {x['g5']:+.1f}pp{g5}  "
                  f"门禁6 最差月归一 {x['g6']:.2f}x{g6} (合并 {x['g6_worst_comb']:+,.0f}$ vs V2 {x['g6_worst_v2']:+,.0f}$)")
        yv = r["VAL"]["yearly"]
        neg = yv[yv < 0]
        g7 = "✅" if len(neg) <= 1 and (neg.min() / STAKE * 100 if len(neg) else 0) >= -15 else "❌"
        ys = " ".join(f"{y}:{v / STAKE * 100:+.1f}%" for y, v in yv.items())
        print(f"  [VAL] 门禁7 负年 {len(neg)} 个{g7}  逐年: {ys}")


if __name__ == "__main__":
    main()
