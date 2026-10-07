"""Tier B 事件臂门禁（STRATEGY_WORKFLOW 4.3，2026-10-07 批准生效）的判定工具。

对指定事件臂逐条判定（--pool 口径，独立 $1,000/笔，TEST 与 VAL 都要过）：
  门禁1 利润>0 且 PF>1.0
  门禁2 VAL 回撤 max_relative_drawdown ≤ 30%
  门禁3 集中度：最大品种利润占比 ≤ 50%，且该品种利润单年集中 ≤ 80%
  门禁4 信号重叠: 臂与 V2 同品种同 4h 入场的占比 ≤ 30%
  门禁5（描述性，不判）组合增量: 加臂后组合年化(钱包) − V2 单独年化
  门禁5b 风险调整增量: 月度 Sharpe 满足 SR_臂 > ρ × SR_V2
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
G2_MAX_DD, G3_TOP, G3_YEAR, G4_MAX, G6_MAX, G7_NEG_MAX, G7_DEPTH = 0.30, 0.50, 0.80, 30.0, 1.5, 1, -15.0


def leg_window(strategy, pool, seg):
    """该策略验证腿的实际回测窗口 (start, end)。"""
    stats, _ = read_result(find_validation(strategy, pool)[seg]["zip"])
    return pd.Timestamp(stats["backtest_start"], tz="UTC"), pd.Timestamp(stats["backtest_end"], tz="UTC")


def concentration(df):
    """(最大品种, 其利润占比, 其最好一年占该品种利润比)；总利润 ≤ 0 时返回 None。"""
    tot = df["profit$"].sum()
    if tot <= 0 or df.empty:
        return None
    by_pair = df.groupby("pair_base")["profit$"].sum()
    top = by_pair.idxmax()
    t = df[df["pair_base"] == top]
    yr = t.groupby(t["close_dt"].dt.year)["profit$"].sum()
    return top, by_pair[top] / tot, (yr.max() / by_pair[top]) if by_pair[top] > 0 else 0.0


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
    out = {"arm": arm, "report": report,
           "val_dd": float(read_result(find_validation(arm, pool)["VAL"]["zip"])[0]
                           .get("max_relative_drawdown") or 0.0)}
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
            "profit": a["profit$"].sum(),
            "pf": (a.loc[a["profit$"] > 0, "profit$"].sum() / -a.loc[a["profit$"] < 0, "profit$"].sum()
                   if (a["profit$"] < 0).any() else float("inf")),
            "conc": concentration(a),
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
        gates = []  # (段, 名称, 通过?, 说明)
        for seg in ("TEST", "VAL"):
            x = r[seg]
            gates.append((seg, "1 利润/PF", x["profit"] > 0 and x["pf"] > 1.0,
                          f"${x['profit']:+,.0f} PF {x['pf']:.2f}"))
            c = x["conc"]
            gates.append((seg, "3 集中度", c is not None and c[1] <= G3_TOP and c[2] <= G3_YEAR,
                          f"{c[0]} 占 {c[1]:.0%}，其最好一年占 {c[2]:.0%}" if c else "无利润"))
            gates.append((seg, "4 重叠", x["g4"] <= G4_MAX, f"{x['g4']:.0f}%"))
            gates.append((seg, "5b 风险调整增量", x["g5b"] > 1e-6,
                          f"SR臂 {x['sr_a']:.2f} vs ρ×SR_V2 = {x['rho']:+.2f}×{x['sr_v']:.2f}，余量 {x['g5b']:+.2f}"
                          f"（1:1 组合 SR {x['sr_c']:.2f}）"))
            gates.append((seg, "6 最差月归一", x["g6"] <= G6_MAX,
                          f"{x['g6']:.2f}x（合并 {x['g6_worst_comb']:+,.0f}$ vs V2 {x['g6_worst_v2']:+,.0f}$）"))
        gates.append(("VAL", "2 回撤", r["val_dd"] <= G2_MAX_DD, f"{r['val_dd']:.1%}"))
        yv = r["VAL"]["yearly"]
        neg = yv[yv < 0]
        depth = neg.min() / STAKE * 100 if len(neg) else 0.0
        gates.append(("VAL", "7 负年", len(neg) <= G7_NEG_MAX and depth >= G7_DEPTH,
                      f"{len(neg)} 个，最深 {depth:+.1f}%  逐年 "
                      + " ".join(f"{y}:{v / STAKE * 100:+.1f}%" for y, v in yv.items())))
        for seg, name, ok, detail in sorted(gates, key=lambda g: (g[1], g[0])):
            print(f"  {'✅' if ok else '❌'} [{seg:<4}] 门禁{name}: {detail}")
        print(f"  ·  [描述] 门禁5 组合增量（已降级不判）: TEST {r['TEST']['g5']:+.1f}pp / VAL {r['VAL']['g5']:+.1f}pp")
        fails = [f"{n}({s})" for s, n, ok, _ in gates if not ok]
        print(f"  → {arm}: " + ("✅ Tier B 全部门禁通过" if not fails else "❌ 未通过: " + ", ".join(fails)))


if __name__ == "__main__":
    main()
