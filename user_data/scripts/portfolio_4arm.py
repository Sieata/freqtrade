"""四臂组合回测（V2 引擎 + FS/OIFlush/BigMove 事件臂，sleeve 模型）。

逐年 = 当年利润 $ 与 ÷钱包%（单臂钱包 = 池钱包；组合 = 臂数 × 池钱包，STRATEGY_WORKFLOW 0.4 v2）；输出：单臂/组合逐年收益率、月度统计、相关矩阵、
逐臂边际贡献（leave-one-out）、并发峰值，以及**配比样本外检验**。
用法: .venv/Scripts/python.exe user_data/scripts/portfolio_4arm.py [--pool top10]
      .venv/Scripts/python.exe user_data/scripts/portfolio_4arm.py --arms OIFlushV2   # V2 + 指定臂

2026-10-07：改用 research_lib（pool 必填，空池报错）；年化分母原硬编码 2.657/2.002 年，
VAL 右端开放后会随数据增长失真 → 改为各段公共窗口实长（VAL 截到四臂最早截止）。
已替代并删除 portfolio_full.py（无 --pool、按"最新报告"取数，同样有静默混池）。
同日：月度统计改全日历（无交易月补 0）——旧版相关矩阵只在双方都有交易的月上算，
低频臂相关系数是噪声（BM–OI TEST 0.68 / VAL 0.03）。

配比检验（只在 TEST 上估权重，VAL 只检验一次，避免拿 VAL 调配比）：
  1:1      现行 sleeve（每臂每笔 $1,000）
  逆波动   权重 ∝ 1/月度σ（不依赖收益估计，最稳健的基线）
  最大Sharpe  权重 ∝ Σ⁻¹μ，负权截 0（依赖收益估计，样本少时易过拟合）
权重是每臂每笔本金的倍数，归一化到 V2 = 1。Sharpe/Calmar 与规模无关，可直接比较。
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_lib import (  # noqa: E402
    load_arm,
    max_dd,
    monthly_series,
    pool_wallet,
    sharpe_m,
    split_ts,
)
from tier_b_eval import leg_window  # noqa: E402


ARMS = ["WeekendReverseV2", "FundingSqueezeV1L", "OIFlushV2", "BigMoveV1"]
SHORT = {"WeekendReverseV2": "V2", "FundingSqueezeV1L": "FS", "OIFlushV2": "OI", "BigMoveV1": "BM"}
WALLET = 12000.0  # 单臂钱包，main 按 --pool 设定
CI_FLOOR = -1.0  # 配比纪律：VAL ΔSharpe 90%CI 下界（STRATEGY_WORKFLOW 4.3）


def peak_conc(df):
    ev = sorted([(t, 1) for t in df["open_dt"]] + [(t, -1) for t in df["close_dt"]],
                key=lambda e: (e[0], e[1]))
    cur = peak = 0
    for _, d in ev:
        cur += d
        peak = max(peak, cur)
    return peak


def hold_overlap(arm, base):
    """臂的持仓期间 V2 同品种也在仓的笔数占比（同品种资金/风险叠加参考；原 fs_portfolio_check）。"""
    if arm.empty or base.empty:
        return 0.0
    by_pair = {p: g for p, g in base.groupby("pair")}
    hit = 0
    for p, o, c in zip(arm["pair"], arm["open_dt"], arm["close_dt"]):
        g = by_pair.get(p)
        hit += g is not None and bool(((g["open_dt"] <= c) & (g["close_dt"] >= o)).any())
    return hit / len(arm) * 100


def seg_parts(arm_dfs, seg, hi):
    return {SHORT.get(a, a): d[(d["seg"] == seg) & (d["close_dt"] <= hi)] for a, d in arm_dfs.items()}


def report(parts, seg, window):
    lo, hi = window
    years = (hi - lo).total_seconds() / 86400 / 365.25
    all_df = pd.concat(parts.values())
    monthly = pd.DataFrame({k: monthly_series(d, lo, hi) for k, d in parts.items()})
    comb = monthly.sum(axis=1)

    print(f"\n{'=' * 96}\n【{seg} {lo:%Y-%m-%d}→{hi:%Y-%m-%d}，{years:.2f} 年，{len(monthly)} 个月】"
          f"钱包 = {len(parts)} 臂 × ${WALLET:,.0f}\n{'=' * 96}")
    for name, d in parts.items():
        yr = d.groupby(d["close_dt"].dt.year)["profit$"].sum()
        ys = " ".join(f"{y}:{v:+,.0f}$({v / WALLET * 100:+.0f}%)" for y, v in yr.items())
        print(f"{name:<4} {len(d):>5}笔  年均 {d['profit$'].sum() / years:>+8,.0f}$  "
              f"Sharpe {sharpe_m(monthly[name]):>5.2f}  逐年 {ys}  并发峰 {peak_conc(d)}"
              + (f"  与V2同品种持仓重叠 {hold_overlap(d, parts['V2']):.0f}%" if name != "V2" else ""))
    yr = all_df.groupby(all_df["close_dt"].dt.year)["profit$"].sum()
    ys = " ".join(f"{y}:{v:+,.0f}$({v / (WALLET * len(parts)) * 100:+.0f}%)" for y, v in yr.items())
    print(f"组合 {len(all_df):>5}笔  年均 {comb.sum() / years:>+8,.0f}$  Sharpe {sharpe_m(comb):>5.2f}  "
          f"逐年 {ys}  最差月 {comb.min():>+,.0f}$  负月 {(comb < 0).mean() * 100:.0f}%  "
          f"月度回撤 {max_dd(comb):>+,.0f}$  并发峰 {peak_conc(all_df)}")
    print("月度相关矩阵（全日历，空月记 0）:")
    print(monthly.corr().round(2).to_string())
    print("leave-one-out（去掉该臂：年均$ 变化 / 组合 Sharpe 变化）:")
    for name in parts:
        rest = comb - monthly[name]
        print(f"  去{name:<4}: 年均 {rest.sum() / years:>+8,.0f}$ (Δ {-monthly[name].sum() / years:>+6,.0f}$)  "
              f"Sharpe {sharpe_m(rest):.2f} (Δ {sharpe_m(rest) - sharpe_m(comb):+.2f})")
    return monthly, years


def fit_weights(monthly):
    """在给定月度矩阵上估三套权重（归一化到 V2 = 1）。"""
    sd = monthly.std(ddof=1)
    out = {"1:1": pd.Series(1.0, index=monthly.columns), "逆波动": 1 / sd}
    mu, cov = monthly.mean().values, monthly.cov().values
    w = np.clip(np.linalg.solve(cov + 1e-9 * np.eye(len(mu)), mu), 0, None)
    out["最大Sharpe"] = pd.Series(w, index=monthly.columns)
    return {k: v / v["V2"] if v["V2"] > 0 else v / v.max() for k, v in out.items()}


def weight_oos(m_test, m_val, y_test, y_val):
    print(f"\n{'=' * 96}\n【配比样本外检验】权重只在 TEST 估计 → VAL 检验（权重 = 每笔本金倍数，V2=1）\n{'=' * 96}")
    cols = list(m_test.columns)
    print(f"{'方案':<10}" + "".join(f"{k:>6}" for k in cols) + "   "
          f"{'TEST Sharpe':>11}{'Calmar':>8}   {'VAL Sharpe':>10}{'Calmar':>8}{'年均$/单位本金':>16}")
    for name, w in fit_weights(m_test).items():
        row = []
        for m, y in ((m_test, y_test), (m_val, y_val)):
            c = (m * w).sum(axis=1)
            dd = max_dd(c)
            row.append((sharpe_m(c), (c.sum() / y) / abs(dd) if dd < 0 else float("inf"),
                        c.sum() / y / w.sum()))
        print(f"{name:<10}" + "".join(f"{w[k]:>6.2f}" for k in cols)
              + f"   {row[0][0]:>11.2f}{row[0][1]:>8.2f}   {row[1][0]:>10.2f}{row[1][1]:>8.2f}{row[1][2]:>+16,.0f}")
    v2 = pd.Series({k: 1.0 if k == "V2" else 0.0 for k in cols})
    c_t, c_v = m_test.mul(v2, axis=1).sum(axis=1), m_val.mul(v2, axis=1).sum(axis=1)
    print(f"{'仅V2':<10}" + "".join(f"{v2[k]:>6.2f}" for k in cols)
          + f"   {sharpe_m(c_t):>11.2f}{(c_t.sum() / y_test) / abs(max_dd(c_t)):>8.2f}"
          f"   {sharpe_m(c_v):>10.2f}{(c_v.sum() / y_val) / abs(max_dd(c_v)):>8.2f}"
          f"{c_v.sum() / y_val:>+16,.0f}")
    # 配对自举：同一组月份重抽，比较各方案与仅V2 的 VAL Sharpe 差（月数少，差异常在噪声内）
    rng = np.random.default_rng(7)
    n = len(m_val)
    idx = rng.integers(0, n, size=(2000, n))
    base_v = c_v.values[idx]
    sr = lambda x: x.mean(axis=1) / x.std(axis=1, ddof=1) * 12 ** 0.5  # noqa: E731
    print(f"VAL Sharpe 差 vs 仅V2（月度配对自举 2000 次，{n} 个月）:")
    calmar = lambda c: (c.sum() / y_val) / abs(max_dd(c)) if max_dd(c) < 0 else float("inf")  # noqa: E731
    cal_v2 = calmar(c_v)
    for name, w in fit_weights(m_test).items():
        c = m_val.mul(w, axis=1).sum(axis=1)
        d = sr(c.values[idx]) - sr(base_v)
        lo, hi = np.nanpercentile(d, [5, 95])
        # 配比纪律（STRATEGY_WORKFLOW 4.3）：VAL Calmar ≥ 仅V2 且 ΔSharpe 90%CI 下界 > CI_FLOOR
        ok = calmar(c) >= cal_v2 and lo > CI_FLOOR
        print(f"  {name:<10} 点估计 {np.nanmedian(d):+.2f}  90%CI [{lo:+.2f}, {hi:+.2f}]"
              f"  P(优于仅V2) {np.nanmean(d > 0) * 100:.0f}%   配比纪律 {'✅' if ok else '❌'}"
              f"（Calmar {calmar(c):.2f} vs {cal_v2:.2f}，CI 下界 {lo:+.2f} vs {CI_FLOOR:+.1f}）")
    print("解读：Calmar = 年均$ ÷ |月度最大回撤$|。VAL 列是唯一的样本外证据；"
          "TEST 列里最大Sharpe 必然最好（在 TEST 上拟合的），不说明问题。")


def main():
    ap = argparse.ArgumentParser(description="四臂组合回测（sleeve 模型）")
    ap.add_argument("--pool", default="top10",
                    choices=["top2", "top5", "top10", "core", "volume"])
    ap.add_argument("--arms", default="", help="逗号分隔事件臂（V2 恒为基准），默认 FS,OI,BM 全部")
    args = ap.parse_args()
    arms = ["WeekendReverseV2"] + ([a for a in args.arms.split(",") if a] or ARMS[1:])
    global WALLET
    WALLET = pool_wallet(args.pool)
    print(f"池 = {args.pool}  单臂钱包 = ${WALLET:,.0f}")
    arm_dfs = {}
    for arm in arms:
        df, rep = load_arm(arm, args.pool)
        print(f"  {SHORT.get(arm, arm):<3} {len(df):>4} 笔  <- {rep}")
        arm_dfs[arm] = df
    split = split_ts()
    val_hi = min(leg_window(a, args.pool, "VAL")[1] for a in arms)
    test_w = (leg_window(arms[0], args.pool, "TEST")[0], split)
    m_test, y_test = report(seg_parts(arm_dfs, "TEST", split), "TEST", test_w)
    m_val, y_val = report(seg_parts(arm_dfs, "VAL", val_hi), "VAL", (split, val_hi))
    weight_oos(m_test, m_val, y_test, y_val)


if __name__ == "__main__":
    main()
