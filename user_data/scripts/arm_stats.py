"""单臂统计质量表：给定币池，对每个策略的 TEST/VAL 交易逐笔做显著性检验。

用途：回答"这个策略拿得出手吗"——回测利润数字与门禁 PASS 不等于证据够硬。
本脚本把每笔交易当成一个独立观测（独立口径 $1,000/笔），给出：
  - t 检验单边 p 值（H0: 每笔期望 ≤ 0）+ 自举 95% 置信区间
  - 去掉最赚的 1 笔后的利润（抗单点依赖）
  - 最赚 1 笔 / 最大品种占利润比（集中度自检）
  - PF / 胜率 / 平均每笔

数据源：research_lib.find_validation——该策略在 --pool 上最新一份含 TEST+VAL 的验证报告
（2026-10-07 前只看"最新一份"，最新那份是别的池就直接报错退出，即使目标池报告存在）。

注意：逐笔 t 检验假设交易独立；同周末/同事件的多品种交易高度相关，p 值偏乐观。
"按月聚类自举"列把同月交易捆在一起重抽，是更保守的 CI，两者差距大说明有效样本远少于 n。

用法:
  ./.venv/Scripts/python.exe user_data/scripts/arm_stats.py --pool top5
  ./.venv/Scripts/python.exe user_data/scripts/arm_stats.py --pool top10 --arms WeekendReverseV2,CrashBuyV2
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_lib import (  # noqa: E402
    STAKE, boot_ci, cluster_boot_ci, load_arm, one_sided_t_p, pool_bases,
)

DEFAULT_ARMS = ["WeekendReverseV2", "CrashBuyV2", "OIFlushV2", "BigMoveV1", "FundingSqueezeV1L"]


def summarize(df, dim="seg"):
    out = {}
    for seg, g in df.groupby(dim):
        p = g["profit$"]
        gw = p[p > 0].sum()
        gl = -p[p < 0].sum()
        best = p.max()
        total = p.sum()
        # 集中度：利润为正时按占利润比；为负时按占绝对亏损比（此时 >100% 表示最赚一笔仍亏）
        denom = total if total > 0 else abs(p).sum()
        pair_share = (g.groupby("pair_base")["profit$"].sum() / denom) if denom else pd.Series(dtype=float)
        out[seg] = {
            "n": len(g), "total": total, "mean": p.mean(), "median": p.median(),
            "win": (p > 0).mean() * 100,
            "pf": (gw / gl if gl > 0 else float("inf")),
            "p": one_sided_t_p(p), "ci": boot_ci(p), "ci_m": cluster_boot_ci(g),
            "best": best, "best_share": (best / denom * 100) if denom else float("nan"),
            "ex_best": total - best,
            "top_pair": (pair_share.idxmax() if len(pair_share) else "-"),
            "top_pair_share": (pair_share.max() * 100 if len(pair_share) else float("nan")),
            "pairs": g["pair_base"].nunique(),
            "per_pair_win": (g.groupby("pair_base")["profit$"].sum() > 0).mean() * 100,
        }
    return out


def main():
    ap = argparse.ArgumentParser(description="单臂统计质量表（显著性 + 集中度自检）")
    ap.add_argument("--pool", default="top5", help="币池名（user_data/universe/pairs_<pool>.txt）")
    ap.add_argument("--arms", default="", help="逗号分隔策略名，默认内置 5 个")
    args = ap.parse_args()
    arms = [a for a in args.arms.split(",") if a] or DEFAULT_ARMS

    print(f"币池 = {args.pool}（{len(pool_bases(args.pool))} 品种）  口径：每笔独立 ${STAKE:,.0f}\n")
    hdr = (f"{'策略':<18}{'段':<6}{'n':>5}{'利润$':>10}{'每笔$':>8}{'胜率%':>7}{'PF':>6}"
           f"{'p值':>10}{'95%CI 每笔$':>18}{'月聚类CI':>18}{'去最赚1笔$':>11}{'最大品种':>10}")
    for arm in arms:
        try:
            df, rep = load_arm(arm, args.pool)
        except SystemExit as e:
            print(f"{arm:<18} 跳过：{e}")
            continue
        if df is None or df.empty:
            print(f"{arm:<18} 无交易（报告 {rep}）")
            continue
        stats = summarize(df)
        print("=" * len(hdr))
        print(f"{arm}    ← {rep}")
        print(hdr)
        print("-" * len(hdr))
        for seg in ("TEST", "VAL"):
            s = stats.get(seg)
            if not s:
                continue
            ci = f"[{s['ci'][0]:+,.0f} , {s['ci'][1]:+,.0f}]"
            cim = f"[{s['ci_m'][0]:+,.0f} , {s['ci_m'][1]:+,.0f}]"
            print(f"{'':<18}{seg:<6}{s['n']:>5}{s['total']:>10,.0f}{s['mean']:>8,.0f}"
                  f"{s['win']:>7.1f}{s['pf']:>6.2f}{s['p']:>10.2e}{ci:>18}{cim:>18}"
                  f"{s['ex_best']:>11,.0f}{s['top_pair'] + ' ' + format(s['top_pair_share'], '.0f') + '%':>10}")
            print(f"{'':<18}{'':<6} 品种盈利 {s['per_pair_win']:.0f}%（{s['pairs']} 品种有交易）"
                  f" · 最赚 1 笔占利润 {s['best_share']:.0f}%（${s['best']:,.0f}）")


if __name__ == "__main__":
    main()
