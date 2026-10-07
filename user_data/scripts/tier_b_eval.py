"""Tier B 事件臂评估表（门禁分层提案 GATE_TIERING_PROPOSAL.md 的实测工具）。

对指定事件臂策略计算提案中的增量门禁（--pool 口径，独立 $1,000/笔）：
  门禁4 信号重叠: 臂与 V2 同品种同 4h 入场的占比 ≤ 30%
  门禁5 组合增量: 加臂后组合年化(钱包) − V2 单独年化 ≥ +3pp（TEST/VAL 分别判）
  门禁5b 风险调整增量（2026-10-07 提案 v2）: 月度 Sharpe 满足 SR_臂 > ρ × SR_V2
         （ρ = 臂与 V2 月度 P&L 相关）。这是"加入该臂能提高组合最大 Sharpe"的充要条件，
         与配比无关。门禁5 用 V2 钱包当分母，等于新臂资金免费——任何赚钱的臂都能过，
         V2 自我复制实测 +19pp 通过；5b 下复制品 SR_臂 = 1.0 × SR_V2，不严格大于 → 不过。
  门禁6 最差月归一: 合并最差月 ÷ 双臂资金(2×$1,000) vs V2 最差月 ≤ 1.5
  门禁7 负年: VAL 段逐年收益率（÷$1,000）负年 ≤ 1 且最深 ≥ -15%

2026-10-07 修正（框架审计）：
  - main/eval_arm 调 load_arm 从未传 pool → 一直按"最新一份报告"取数，静默混池仍在；
  - 门禁5 在 TEST 段恒判 ✅（条件写成 `or seg == "TEST"`）；
  - 年化分母原为各自首末笔跨度（臂/V2/组合三者分母不同，增量 pp 不可比）→ 统一用段长；
  - VAL 右端开放，各臂验证日期不同 → 截断到所有臂共同的最早 VAL 截止时间再比较。

用法: .venv/Scripts/python.exe user_data/scripts/tier_b_eval.py --pool top10
      .venv/Scripts/python.exe user_data/scripts/tier_b_eval.py --pool top10 --arms OIFlushV2
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_lib import (  # noqa: E402
    STAKE, find_validation, load_arm, monthly_series, pool_wallet, read_result, sharpe_m, split_ts,
)

BASE = "WeekendReverseV2"
ARMS = ["FundingSqueezeV1L", "OIFlushV2", "BigMoveV1"]
G4_MAX, G5_MIN, G6_MAX, G7_NEG_MAX, G7_DEPTH = 30.0, 3.0, 1.5, 1, -15.0


def leg_window(strategy, pool, seg):
    """该策略验证腿的实际回测窗口 (start, end)。"""
    stats, _ = read_result(find_validation(strategy, pool)[seg]["zip"])
    return pd.Timestamp(stats["backtest_start"], tz="UTC"), pd.Timestamp(stats["backtest_end"], tz="UTC")


def summarize(df, years, wallet):
    m = df.groupby(df["close_dt"].dt.strftime("%Y-%m"))["profit$"].sum()
    return {
        "n": len(df), "total": df["profit$"].sum(),
        "ann": df["profit$"].sum() / years / wallet,
        "worst": min(m.min(), 0.0) if len(m) else 0.0,
        "yearly": df.groupby(df["close_dt"].dt.year)["profit$"].sum(),
    }


def eval_arm(arm, base_df, pool, windows, wallet):
    arm_df, report = load_arm(arm, pool)
    out = {"arm": arm, "report": report}
    for seg in ("TEST", "VAL"):
        lo, hi = windows[seg]
        years = (hi - lo).total_seconds() / 86400 / 365.25
        a = arm_df[(arm_df["seg"] == seg) & (arm_df["close_dt"] <= hi)]
        v = base_df[(base_df["seg"] == seg) & (base_df["close_dt"] <= hi)]
        s_a, s_v, s_c = (summarize(x, years, wallet) for x in (a, v, pd.concat([a, v])))
        ma, mv = monthly_series(a, lo, hi), monthly_series(v, lo, hi)
        rho = float(ma.corr(mv)) if ma.std() > 0 and mv.std() > 0 else 0.0
        sr_a, sr_v, sr_c = sharpe_m(ma), sharpe_m(mv), sharpe_m(ma + mv)
        v2_keys = set(zip(v["pair"], v["open_dt"]))
        overlap = sum((p, o) in v2_keys for p, o in zip(a["pair"], a["open_dt"]))
        # 门禁6: 合并最差月÷双臂资金 vs V2 最差月÷单臂资金；V2 无亏月时任何合并亏月都算无穷大
        if s_v["worst"] < 0:
            g6 = (abs(s_c["worst"]) / (2 * STAKE)) / (abs(s_v["worst"]) / STAKE)
        else:
            g6 = float("inf") if s_c["worst"] < 0 else 0.0
        out[seg] = {
            "n": s_a["n"], "ann_arm": s_a["ann"], "ann_v2": s_v["ann"], "ann_comb": s_c["ann"],
            "g4": 100 * overlap / len(a) if len(a) else 0.0,
            "g5": (s_c["ann"] - s_v["ann"]) * 100,
            "g6": g6, "g6_worst_comb": s_c["worst"], "g6_worst_v2": s_v["worst"],
            "yearly": s_a["yearly"], "years": years,
            "rho": rho, "sr_a": sr_a, "sr_v": sr_v, "sr_c": sr_c,
            "g5b": sr_a - rho * sr_v,
        }
    return out


def main():
    ap = argparse.ArgumentParser(description="Tier B 事件臂增量门禁")
    ap.add_argument("--arms", default="", help="逗号分隔臂列表，默认 " + ",".join(ARMS))
    ap.add_argument("--arm", default=None, help="单臂（兼容旧用法）")
    ap.add_argument("--pool", default="top10", help="币池名（user_data/universe/pairs_<pool>.txt）")
    args = ap.parse_args()
    arms = [args.arm] if args.arm else ([a for a in args.arms.split(",") if a] or ARMS)
    wallet = pool_wallet(args.pool)

    # 公共窗口：TEST 用切分定义；VAL 截到所有参与者最早的实际截止时间
    split = split_ts()
    test_lo = leg_window(BASE, args.pool, "TEST")[0]
    val_ends = {s: leg_window(s, args.pool, "VAL")[1] for s in [BASE, *arms]}
    val_hi = min(val_ends.values())
    windows = {"TEST": (test_lo, split), "VAL": (split, val_hi)}
    spread = (max(val_ends.values()) - val_hi).days
    print(f"币池={args.pool}  钱包=${wallet:,.0f}  臂={arms}")
    print(f"窗口: TEST {test_lo:%Y-%m-%d}→{split:%Y-%m-%d} · VAL {split:%Y-%m-%d}→{val_hi:%Y-%m-%d}"
          + (f"（各臂 VAL 截止相差 {spread} 天，已截齐；建议同日重跑验证）" if spread > 7 else ""))

    base_df, base_report = load_arm(BASE, args.pool)
    print(f"基准 {BASE}: {base_report}")

    for arm in arms:
        r = eval_arm(arm, base_df, args.pool, windows, wallet)
        print(f"\n{'=' * 100}\n【{arm}】 ← {r['report']}")
        for seg in ("TEST", "VAL"):
            x = r[seg]
            print(f"  {seg}: {x['n']}笔  臂年化 {x['ann_arm'] * 100:+.1f}%  V2年化 {x['ann_v2'] * 100:+.1f}%  "
                  f"组合年化 {x['ann_comb'] * 100:+.1f}%（{x['years']:.2f} 年）")
        for seg in ("TEST", "VAL"):
            x = r[seg]
            g4 = "✅" if x["g4"] <= G4_MAX else "❌"
            g5 = "✅" if x["g5"] >= G5_MIN else "❌"
            g6 = "✅" if x["g6"] <= G6_MAX else "❌"
            print(f"  [{seg}] 门禁4 重叠 {x['g4']:.0f}%{g4}  门禁5 组合增量 {x['g5']:+.1f}pp{g5}  "
                  f"门禁6 最差月归一 {x['g6']:.2f}x{g6} (合并 {x['g6_worst_comb']:+,.0f}$ vs V2 {x['g6_worst_v2']:+,.0f}$)")
        for seg in ("TEST", "VAL"):
            x = r[seg]
            g5b = "✅" if x["g5b"] > 1e-6 else "❌"
            print(f"  [{seg}] 门禁5b SR臂 {x['sr_a']:.2f} vs ρ×SR_V2 = {x['rho']:+.2f}×{x['sr_v']:.2f}"
                  f" → 余量 {x['g5b']:+.2f}{g5b}  （1:1 组合 Sharpe {x['sr_c']:.2f} vs V2 {x['sr_v']:.2f}）")
        yv = r["VAL"]["yearly"]
        neg = yv[yv < 0]
        depth = neg.min() / STAKE * 100 if len(neg) else 0.0
        g7 = "✅" if len(neg) <= G7_NEG_MAX and depth >= G7_DEPTH else "❌"
        ys = " ".join(f"{y}:{v / STAKE * 100:+.1f}%" for y, v in yv.items())
        print(f"  [VAL] 门禁7 负年 {len(neg)} 个{g7}  逐年: {ys}")


if __name__ == "__main__":
    main()
