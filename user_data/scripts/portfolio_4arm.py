"""四臂组合回测（V2 引擎 + FS/OIFlush/BigMove 事件臂，sleeve 模型）。

每年重置本金 = 臂数 × $1,000；输出：单臂/组合逐年收益率、月度统计、相关矩阵、
逐臂边际贡献（leave-one-out）、并发峰值。
用法: .venv/Scripts/python.exe user_data/scripts/portfolio_4arm.py [--pool top10]

坑（2026-09-21 修）：必须显式设定 tier_b_eval.POOL/--pool，否则 load_arm 的
POOL 为空集 → 所有交易被过滤、四臂全 0 笔且不报错。
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tier_b_eval  # noqa: E402
from tier_b_eval import load_arm, load_pool, SPLIT, STAKE  # noqa: E402

ARMS = ["WeekendReverseV2", "FundingSqueezeV1L", "OIFlushV2", "BigMoveV1"]
SHORT = {"WeekendReverseV2": "V2", "FundingSqueezeV1L": "FS", "OIFlushV2": "OI", "BigMoveV1": "BM"}


def peak_conc(df):
    ev = []
    for _, t in df.iterrows():
        ev.append((t["open_dt"], 1))
        ev.append((t["close_dt"], -1))
    ev.sort(key=lambda e: (e[0], e[1]))
    cur = peak = 0
    for _, d in ev:
        cur += d
        peak = max(peak, cur)
    return peak


def stats(df, base, label):
    m = df.groupby(df["close_dt"].dt.strftime("%Y-%m"))["profit$"].sum()
    yr = df.groupby(df["close_dt"].dt.year)["profit$"].sum()
    eq = m.cumsum()
    return {
        "label": label, "n": len(df), "total": df["profit$"].sum(),
        "m_mean": m.mean(), "m_min": m.min(), "neg": (m < 0).mean() * 100,
        "mdd": (eq - eq.cummax()).min(), "peak": peak_conc(df),
        "yearly": yr, "ypct": " ".join(f"{y}:{v / base * 100:+.0f}%" for y, v in yr.items()),
        "monthly": m,
    }


def report(arm_dfs, seg, seg_test):
    sel = (lambda d: d[d["close_dt"] < SPLIT]) if seg_test else (lambda d: d[d["close_dt"] >= SPLIT])
    years_span = 2.657 if seg_test else 2.002
    parts = {SHORT[a]: sel(d).copy() for a, d in arm_dfs.items()}
    for p in parts.values():
        p["profit$"] = p["profit_ratio"] * STAKE
    all_df = pd.concat(parts.values())
    base = len(parts) * STAKE

    print(f"\n{'=' * 96}\n【{seg}】基数 = {len(parts)} 臂 × ${STAKE:,} = ${base:,}/年\n{'=' * 96}")
    rows = []
    for name, d in parts.items():
        s = stats(d, STAKE, name)
        rows.append(s)
        print(f"{name:<4} {s['n']:>5}笔  年均 {s['total'] / years_span:>+8,.0f}$  "
              f"逐年 {s['ypct']}  并发峰 {s['peak']}")
    s_all = stats(all_df, base, "组合")
    print(f"组合 {s_all['n']:>5}笔  年均 {s_all['total'] / years_span:>+8,.0f}$  "
          f"逐年 {s_all['ypct']}  最差月 {s_all['m_min']:>+,.0f}$  负月 {s_all['neg']:.0f}%  "
          f"月度回撤 {s_all['mdd']:>+,.0f}$  并发峰 {s_all['peak']}")
    # 相关矩阵
    cal = pd.DataFrame({k: v["monthly"] for k, v in
                        [(n, s) for n, s in ((name, stats(d, STAKE, name)) for name, d in parts.items())]})
    print("月度相关矩阵:")
    print(cal.corr().round(2).to_string())
    # leave-one-out 边际
    print("leave-one-out（去掉该臂后组合年利润变化，负数=该臂贡献为正）:")
    for name in parts:
        rest = pd.concat([d for n, d in parts.items() if n != name])
        s_rest = stats(rest, base - STAKE, name)
        print(f"  去{name:<4}: 组合年均 {s_rest['total'] / years_span:>+8,.0f}$ "
              f"(Δ {s_rest['total'] / years_span - s_all['total'] / years_span:>+6,.0f}$)")


def main():
    ap = argparse.ArgumentParser(description="四臂组合回测（sleeve 模型）")
    ap.add_argument("--pool", default="top10",
                    choices=["top2", "top5", "top10", "core", "volume"])
    args = ap.parse_args()
    pool, wallet = load_pool(args.pool)
    tier_b_eval.POOL, tier_b_eval.WALLET = pool, wallet
    print(f"池 = {args.pool}（{len(pool)} 品种）  钱包口径 = ${wallet:,}")
    arm_dfs = {}
    for arm in ARMS:
        df, rep = load_arm(arm, args.pool)
        print(f"  {SHORT[arm]:<3} {len(df):>4} 笔  <- {rep}")
        arm_dfs[arm] = df
    report(arm_dfs, "TEST 20220101-20240828", True)
    report(arm_dfs, "VAL 20240828-", False)


if __name__ == "__main__":
    main()
