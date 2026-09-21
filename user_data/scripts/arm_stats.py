"""单臂统计质量表：给定币池，对每个策略的 TEST/VAL 交易逐笔做显著性检验。

用途：回答"这个策略拿得出手吗"——回测利润数字与门禁 PASS 不等于证据够硬。
本脚本把每笔交易当成一个独立观测（独立口径 $1,000/笔），给出：
  - t 检验单边 p 值（H0: 每笔期望 ≤ 0）+ 自举 95% 置信区间
  - 去掉最赚的 1 笔后的利润（抗单点依赖）
  - 最赚 1 笔 / 最大品种占利润比（集中度自检）
  - PF / 胜率 / 平均每笔

数据源：各策略**最新一份** validate_<策略>_*.md 报告里记录的 TEST/VAL 两腿 zip
（与 tier_b_eval.py 同源）。因此跑本脚本前，该策略的最新一次验证必须是目标币池，
否则读到的会是别的池子——脚本会打印报告名与池标签供核对，不一致则报错退出。

用法:
  ./.venv/Scripts/python.exe user_data/scripts/arm_stats.py --pool top5
  ./.venv/Scripts/python.exe user_data/scripts/arm_stats.py --pool top10 --arms WeekendReverseV2,CrashBuyV2
"""
import argparse
import json
import random
import re
import zipfile
from pathlib import Path

import pandas as pd

BT = Path("user_data/backtest_results")
REPORTS = Path("user_data/reports")
UNIVERSE = Path("user_data/universe")
STAKE = 1000.0
DEFAULT_ARMS = ["WeekendReverseV2", "CrashBuyV2", "OIFlushV2", "BigMoveV1", "FundingSqueezeV1L"]


def pool_bases(pool):
    path = UNIVERSE / f"pairs_{pool}.txt"
    return {line.split("/")[0].strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#") and "/" in line}


def latest_report(strategy):
    rs = sorted(REPORTS.glob(f"validate_{strategy}_*.md"), key=lambda p: p.stat().st_mtime)
    return rs[-1] if rs else None


def load_arm(strategy, pool):
    """返回 (df, 报告名)。df 列: pair/open_dt/close_dt/profit$/profit_ratio/seg。"""
    r = latest_report(strategy)
    if r is None:
        return None, None
    txt = r.read_text(encoding="utf-8")
    # 池标签核对：报告的 "## <POOL> × TEST" 必须与请求的池一致
    m_pool = re.search(r"## (\w+) × TEST", txt)
    label = m_pool.group(1) if m_pool else "?"
    if label.lower() != pool.lower():
        raise SystemExit(f"{r.name}: 报告池 = {label}，与请求的 --pool {pool} 不一致；"
                         f"请先用 validate_strategy.py --pool {pool} 重跑该策略。")
    zips = []
    for pat in (r"## \w+ × TEST（20220101-20240828）", r"## \w+ × VAL（20240828-）"):
        m = re.search(pat, txt)
        if not m:
            raise SystemExit(f"{r.name}: 找不到 {pat}")
        zips.append(re.search(r"结果: `(backtest-result-[0-9_-]+\.zip)`",
                              txt[m.end():].split("## ")[0]).group(1))
    rows = []
    for seg, zname in zip(("TEST", "VAL"), zips):
        with zipfile.ZipFile(BT / zname) as z:
            for n in z.namelist():
                if not n.endswith(".json"):
                    continue
                d = json.loads(z.read(n))
                if isinstance(d, dict) and "strategy" in d:
                    for t in d["strategy"][list(d["strategy"])[0]].get("trades", []):
                        t = dict(t)
                        t["seg"] = seg
                        rows.append(t)
    df = pd.DataFrame(rows)
    if df.empty:
        return df, r.name
    df["pair_base"] = df["pair"].str.split("/").str[0]
    df = df[df["pair_base"].isin(pool_bases(pool))].reset_index(drop=True)
    df["profit$"] = df["profit_ratio"] * STAKE
    df["close_dt"] = pd.to_datetime(df["close_date"], utc=True)
    return df, r.name


def welch_p(d):
    """单边 t 检验 p 值（H0: 均值 ≤ 0），用 t 分布的近似。"""
    n = len(d)
    if n < 3:
        return float("nan")
    sd = d.std(ddof=1)
    if sd == 0:
        return 0.0 if d.mean() > 0 else 1.0
    t = d.mean() / (sd / n ** 0.5)
    try:
        from scipy import stats
        return float(stats.t.sf(t, df=n - 1))
    except Exception:
        import math
        # 正态近似（n 大时足够）
        return float(0.5 * math.erfc(t / 2 ** 0.5))


def boot_ci(d, iters=4000, seed=7):
    """均值的自举 95% 置信区间。"""
    random.seed(seed)
    vals = list(d)
    n = len(vals)
    if n < 3:
        return (float("nan"), float("nan"))
    means = sorted(sum(random.choices(vals, k=n)) / n for _ in range(iters))
    return (means[int(0.025 * iters)], means[int(0.975 * iters)])


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
            "p": welch_p(p), "ci": boot_ci(p),
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
           f"{'p值':>10}{'95%CI 每笔$':>18}{'去最赚1笔$':>11}{'最大品种':>10}")
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
            print(f"{'':<18}{seg:<6}{s['n']:>5}{s['total']:>10,.0f}{s['mean']:>8,.0f}"
                  f"{s['win']:>7.1f}{s['pf']:>6.2f}{s['p']:>10.2e}{ci:>18}"
                  f"{s['ex_best']:>11,.0f}{s['top_pair'] + ' ' + format(s['top_pair_share'], '.0f') + '%':>10}")
            print(f"{'':<18}{'':<6} 品种盈利 {s['per_pair_win']:.0f}%（{s['pairs']} 品种有交易）"
                  f" · 最赚 1 笔占利润 {s['best_share']:.0f}%（${s['best']:,.0f}）")


if __name__ == "__main__":
    main()
