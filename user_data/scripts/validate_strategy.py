"""规范化策略验证：测试集(TEST)/验证集(VAL) × 双币池(core/volume) 一键跑完 + 门禁判定。

流程标准（详见 STRATEGY_WORKFLOW.md，切分与币池的唯一权威来源在 user_data/universe/）：
  时间:   TEST 20220101-20240828（调参只准用这段，2021 仅暖机）
          VAL  20240828-          （定版候选只跑一次；跑过又改参 = 作废重来）
  币池:   TOP2   pairs_top2.txt    双雄极限池（只 BTC/ETH；分散度压力测试，非实盘建议）
          TOP5   pairs_top5.txt    蓝筹子集（市值 Top5，剥离中盘贡献用）
          TOP10  pairs_top10.txt   评估第一口径（市值 Top10）
          CORE   pairs_core.txt    实盘允许池（市值 Top50）
          VOLUME pairs_volume.txt  泛化测试池（24h 成交量 Top30，禁实盘）
  口径:   泛化验证用独立口径 —— --stake-amount 1000 --max-open-trades <池内品种数>，
          每笔固定 $1,000，与 pool_review.py 的独立口径一致；复利口径留给定版后的
          单池配置回测。--cache none 恒定（对账纪律）。

门禁（任一 FAIL 则退出码 1）:
  TEST: 总利润>0 且 PF>1.0 且 ≥80% 品种盈利（独立口径）
  VAL : 同上 且 max_relative_drawdown ≤ 30%；笔数<20 只警告
  警告: 利润集中度（top 品种占比>50% 或其利润 80% 集中在单年）→ 防新币单年 pump
  警告: VAL 窥视台账（universe/val_ledger.jsonl）——同名策略的另一 SHA 已跑过 VAL = 二次窥视

产物: reports/validate_<策略>_<时间>.md（人读）+ 同名 .json 旁车（机读，下游脚本经
research_lib.find_validation 读取，不再正则解析 markdown）。VAL 右端开放，实际截止时间写入两者。

用法:
  .venv/bin/python user_data/scripts/validate_strategy.py --strategy WeekendReverseV2
  .venv/bin/python user_data/scripts/validate_strategy.py --strategy BigMoveV1 \
      --config user_data/config_bigmove.json --pool core
  可选: --fee 0.001（摩擦测试） --skip-test / --skip-val --no-report
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_lib import (  # noqa: E402
    REPORT_DIR, ROOT, STAKE, STRATEGIES, UNIVERSE, load_pool, load_splits,
    read_result, result_span_years, run_backtest,
)

VAL_LEDGER = UNIVERSE / "val_ledger.jsonl"

GATES_TEST = {"min_pair_win_rate": 0.80}
GATES_VAL = {"min_pair_win_rate": 0.80, "max_dd": 0.30}


def strategy_timeframe(strategy, config_path):
    """策略类里的 timeframe 优先，回退 config。"""
    src = (STRATEGIES / f"{strategy}.py").read_text(encoding="utf-8")
    m = re.search(r"timeframe\s*=\s*['\"]([^'\"]+)['\"]", src)
    if m:
        return m.group(1)
    with open(config_path, encoding="utf-8") as f:
        return json.load(f).get("timeframe", "4h")


def data_available(pair, tf):
    """本地是否已有该品种该周期的 K 线 feather。"""
    slug = pair.replace("/", "_").replace(":", "_")
    return (ROOT / "user_data" / "data" / "binance" / "futures" / f"{slug}-{tf}-futures.feather").exists()


def analyze(stats, trades, max_open_trades=1):
    """独立口径分析：每笔固定 $1,000。返回 (portfolio dict, 按品种表, 集中度 dict)。"""
    cell = defaultdict(float)
    cnt = defaultdict(int)
    wins = defaultdict(int)
    for t in trades:
        y = t["close_date"][:4]
        p = t["pair"].split("/")[0]
        cell[(p, y)] += t["profit_ratio"] * STAKE
        cnt[(p, y)] += 1
        wins[(p, y)] += t["profit_ratio"] > 0

    pairs = sorted({k[0] for k in cell})
    years = sorted({k[1] for k in cell})
    pair_tot = {p: sum(cell.get((p, y), 0.0) for y in years) for p in pairs}
    prof_pairs = [p for p in pairs if pair_tot[p] > 0]

    ranked = sorted(pair_tot.items(), key=lambda kv: kv[1], reverse=True)
    grand = sum(pair_tot.values())
    conc = {"grand": grand}
    if ranked and grand > 0:
        top_p, top_v = ranked[0]
        pos_years = [(y, cell[(top_p, y)]) for y in years if cell.get((top_p, y), 0) > 0]
        best_y, best_v = max(pos_years, key=lambda kv: kv[1]) if pos_years else ("-", 0.0)
        conc = {
            "grand": grand,
            "top_pair": top_p,
            "top_share": top_v / grand,
            "best_year": best_y,
            "best_year_share": (best_v / top_v) if top_v > 0 else 0.0,
        }

    # 门禁口径 = 钱包口径 max_relative_drawdown（AGENTS/bt_summary 约定）。2026-10-07 前误用
    # max_drawdown_account（同一回测 20.2% vs 32.1%），VAL 回撤门禁系统性偏松。
    dd = float(stats.get("max_relative_drawdown") or 0.0)
    dd_acct = float(stats.get("max_drawdown_account") or 0.0)
    # 钱包 = 池规模×1.2×$1,000，闲置资金会稀释回撤%（低频臂几乎不可能触 30% 线）；
    # 另给"回撤折合几笔本金"（$回撤 ÷ $1,000）作不受池规模影响的参照
    dd_slots = float(stats.get("max_drawdown_abs") or 0.0) / STAKE
    pf = stats.get("profit_factor")
    portfolio = {
        "trades": stats.get("total_trades", len(trades)),
        "profit_abs": stats.get("profit_total_abs", 0.0),
        "win_rate": stats.get("winrate", 0.0),
        "pf": pf if pf else (float("inf") if stats.get("profit_total_abs", 0) > 0 else 0.0),
        "dd": dd,
        "dd_acct": dd_acct,
        "dd_slots": dd_slots,
        "pairs_profitable": len(prof_pairs),
        "pairs_total": len(pairs),
    }

    # 年化（2026-08-29 展示约定）：固定 $1,000/笔不复利；钱包 = max_open_trades×1.2×$1,000
    # 分母 = 回测覆盖时长（2026-10-07 前用首末笔交易跨度，低频臂年化会虚高）
    if trades:
        span_years = result_span_years(stats)
        wallet = max_open_trades * 1.2 * STAKE
        holds = sum(
            (pd.Timestamp(t["close_date"]) - pd.Timestamp(t["open_date"])).total_seconds()
            for t in trades
        )
        avg_conc = holds / (span_years * 365.25 * 86400)
        portfolio.update({
            "years": span_years,
            "avg_conc": avg_conc,
            "ann_wallet": (portfolio["profit_abs"] / span_years) / wallet,
            "ann_deployed": (portfolio["profit_abs"] / span_years) / max(avg_conc * STAKE, 1e-9),
        })
    return portfolio, (pairs, years, cell, cnt, wins), conc


def _ledger_rows():
    if not VAL_LEDGER.exists():
        return []
    return [json.loads(x) for x in VAL_LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]


def val_ledger_check(strategy, sha):
    """VAL 窥视检查：同名策略的其他 SHA 已跑过 VAL → 本次 VAL 结果是"看过答案后改的"。

    只警告不阻断（换池复核是合法用途），但结论里必须如实标注。台账入库，跨设备生效；
    只认策略名——改名另起副本绕不过纪律本身，RESEARCH 里仍须记录来源版本。
    """
    prev = [r for r in _ledger_rows() if r["strategy"] == strategy]
    other = sorted({r["sha16"] for r in prev if r["sha16"] != sha})
    if other:
        first = min(r["date"] for r in prev)
        return (f"该策略已有 {len(other)} 个其他版本跑过 VAL（首次 {first}，SHA {', '.join(other)}）"
                f"→ 本次 VAL 属二次窥视，不能当独立样本外证据")
    return None


def val_ledger_append(strategy, sha, pools, zips):
    row = {"date": dt.date.today().isoformat(), "strategy": strategy, "sha16": sha,
           "pools": pools, "zips": zips}
    with open(VAL_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def gate_check(split, portfolio, conc):
    """返回 [(名称, 结果 PASS/FAIL/WARN, 说明)]。"""
    g = GATES_VAL if split == "VAL" else GATES_TEST
    res = []
    res.append(("利润>0", "PASS" if portfolio["profit_abs"] > 0 else "FAIL",
                f"${portfolio['profit_abs']:,.0f}（独立口径 $1,000/笔）"))
    pf = portfolio["pf"]
    res.append(("PF>1.0", "PASS" if pf > 1.0 else "FAIL",
                "∞（无亏损笔）" if pf == float("inf") else f"{pf:.2f}"))
    n, tot = portfolio["pairs_profitable"], portfolio["pairs_total"]
    ok = tot > 0 and n / tot >= g["min_pair_win_rate"]
    res.append((f"≥{g['min_pair_win_rate']:.0%} 品种盈利", "PASS" if ok else "FAIL", f"{n}/{tot}"))
    if split == "VAL":
        res.append(("回撤≤30%", "PASS" if portfolio["dd"] <= g["max_dd"] else "FAIL",
                    f"{portfolio['dd'] * 100:.1f}%（钱包口径 max_relative_drawdown；"
                    f"账户口径 {portfolio['dd_acct'] * 100:.1f}% 仅参考；"
                    f"折合 {portfolio['dd_slots']:.2f} 笔本金）"))
        if portfolio["trades"] < 20:
            res.append(("笔数≥20", "WARN", f"{portfolio['trades']} 笔（低频策略属正常，解读谨慎）"))
    if conc.get("top_pair") and conc["grand"] > 0:
        c1 = conc["top_share"] > 0.5
        c2 = conc["best_year_share"] > 0.8
        if c1 or c2:
            res.append(("集中度", "WARN",
                        f"{conc['top_pair']} 占利润 {conc['top_share'] * 100:.0f}%，"
                        f"其 {conc['best_year_share'] * 100:.0f}% 集中在 {conc['best_year']} → 单年 pump 嫌疑"))
    return res


def fmt_table(pairs, years, cell, cnt, wins):
    hdr = f"{'pair':<10}" + "".join(f"{y:>11}" for y in years) + f"{'total':>11}{'n':>5}{'win%':>7}"
    lines = [hdr, "-" * len(hdr)]
    row_total = defaultdict(float)
    for p in pairs:
        vals = [cell.get((p, y), 0.0) for y in years]
        n = sum(cnt[(p, y)] for y in years)
        w = sum(wins[(p, y)] for y in years)
        tot = sum(vals)
        mark = "" if tot > 0 else ("  ←亏" if tot < 0 else "")
        lines.append(f"{p:<10}" + "".join(f"{v:>11,.0f}" for v in vals)
                     + f"{tot:>11,.0f}{n:>5}{100 * w / n if n else 0:>7.0f}{mark}")
        for y, v in zip(years, vals):
            row_total[y] += v
    lines.append("-" * len(hdr))
    lines.append(f"{'TOTAL':<10}" + "".join(f"{row_total[y]:>11,.0f}" for y in years)
                 + f"{sum(row_total.values()):>11,.0f}")
    # 逐年收益率（2026-08-29 口径：每年重置 $1,000 本金，当年利润 ÷ 1000 = 当年收益率%）
    lines.append(f"{'TOTAL%':<10}" + "".join(f"{row_total[y] / STAKE * 100:>10.1f}%" for y in years)
                 + f"{sum(row_total.values()) / STAKE * 100:>10.1f}%")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="规范化策略验证（TEST/VAL × core/volume）")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--pool", choices=["top2", "top5", "top10", "core", "volume", "both"], default="both")
    ap.add_argument("--config", default=str(ROOT / "user_data" / "config_perpetual.json"))
    ap.add_argument("--fee", type=float, default=None)
    ap.add_argument("--skip-test", action="store_true", help="只跑 VAL")
    ap.add_argument("--skip-val", action="store_true", help="只跑 TEST")
    ap.add_argument("--no-report", action="store_true", help="不写 markdown 报告")
    args = ap.parse_args()

    if not (STRATEGIES / f"{args.strategy}.py").exists():
        raise SystemExit(f"策略不存在: user_data/strategies/{args.strategy}.py")

    splits = load_splits()
    tf = strategy_timeframe(args.strategy, args.config)
    sha = hashlib.sha256((STRATEGIES / f"{args.strategy}.py").read_bytes()).hexdigest()[:16]

    pools = ["core", "volume"] if args.pool == "both" else [args.pool]
    runs = []  # (pool, split_name, timerange, pairs_used, skipped, zip_path)
    for pool in pools:
        all_pairs = [p for p, _ in load_pool(pool)]
        have = [p for p in all_pairs if data_available(p, tf)]
        skipped = [p for p in all_pairs if p not in have]
        if skipped:
            print(f"[!] {pool} 池缺 {tf} 数据 {len(skipped)} 个: {', '.join(skipped)}")
            print(f"    补数据: ./ensure-data.sh user_data/universe/pairs_{pool}.txt")
        if not have:
            print(f"[!] {pool} 池无任何本地数据，跳过")
            continue
        for split_name, tr in (("TEST", splits["test_timerange"]), ("VAL", splits["val_timerange"])):
            if (split_name == "TEST" and args.skip_test) or (split_name == "VAL" and args.skip_val):
                continue
            try:
                zp = run_backtest(args.strategy, args.config, tr, have, args.fee)
            except RuntimeError as e:
                raise SystemExit(str(e))
            runs.append((pool, split_name, tr, have, skipped, zp))
    if not runs:
        raise SystemExit("没有任何可运行的组合（检查 --pool/--skip-* 与本地数据）")

    # 汇总 + 门禁
    all_pass = True
    report = [
        f"# validate_strategy: {args.strategy}",
        "",
        f"- 时间: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} | 切分 v{splits['version']}（冻结 {splits['frozen_on']}）",
        f"- 策略 SHA256[:16]: `{sha}` | config: `{args.config}` | timeframe: {tf}",
        f"- 口径: 独立口径 ${STAKE:.0f}/笔，max_open_trades=池内品种数，--cache none",
        "",
    ]
    sidecar = {"strategy": args.strategy, "sha16": sha, "config": str(args.config), "timeframe": tf,
               "fee": args.fee, "splits_version": splits["version"],
               "created": dt.datetime.now().isoformat(timespec="seconds"), "runs": []}
    for pool, split_name, tr, have, skipped, zp in runs:
        stats, trades = read_result(zp)
        portfolio, table, conc = analyze(stats, trades, len(have))
        gates = gate_check(split_name, portfolio, conc)
        if split_name == "VAL":
            peek = val_ledger_check(args.strategy, sha)
            if peek:
                gates.append(("VAL 窥视", "WARN", peek))
        if any(r[1] == "FAIL" for r in gates):
            all_pass = False
        sidecar["runs"].append({
            "pool": pool, "split": split_name, "timerange": tr, "zip": zp.name,
            "backtest_start": stats.get("backtest_start"), "backtest_end": stats.get("backtest_end"),
            "pairs": have, "skipped": skipped,
            "metrics": {k: v for k, v in portfolio.items() if isinstance(v, (int, float))},
            "gates": [{"name": n, "verdict": v, "detail": d} for n, v, d in gates],
        })
        ann = (f"年化: 钱包口径 {portfolio['ann_wallet'] * 100:+.1f}%/年 · "
               f"占仓口径 {portfolio['ann_deployed'] * 100:+.1f}%/年"
               f"（平均并发 {portfolio['avg_conc']:.1f} 仓，{portfolio['years']:.2f} 年）"
               if "ann_wallet" in portfolio else "年化: 无交易")
        pairs_list, years_list, cell_map = table[0], table[1], table[2]
        yearly_pct = " ".join(
            f"{y}:{sum(cell_map.get((p, y), 0.0) for p in pairs_list) / STAKE * 100:+.1f}%"
            for y in years_list)
        # 同一利润按钱包（池规模×1.2×$1,000）表达：÷$1,000 口径在 10 品种池里会显示 +300% 级数字
        wallet = len(have) * 1.2 * STAKE
        yearly_wallet = " ".join(
            f"{y}:{sum(cell_map.get((p, y), 0.0) for p in pairs_list) / wallet * 100:+.1f}%"
            for y in years_list)
        print(f"\n=== {pool.upper()} × {split_name} ({tr}，实际 {stats.get('backtest_start')} → "
              f"{stats.get('backtest_end')}) ===")
        print(f"trades={portfolio['trades']}  profit=${portfolio['profit_abs']:,.0f}  "
              f"win%={portfolio['win_rate'] * 100:.1f}  PF={portfolio['pf']:.2f}  "
              f"dd={portfolio['dd'] * 100:.1f}%  盈利品种={portfolio['pairs_profitable']}/{portfolio['pairs_total']}")
        print(ann)
        print(f"逐年收益率（每年重置 $1,000 本金，当年利润÷1000）: {yearly_pct}")
        print(f"逐年收益率（钱包口径，当年利润÷${wallet:,.0f}，首尾年为不足年）: {yearly_wallet}")
        print("独立口径品种×年度（$1,000/笔）:")
        print(fmt_table(*table))
        for name, verdict, detail in gates:
            mark = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ "}[verdict]
            print(f"  {mark} {name}: {detail}")
            if verdict == "FAIL":
                all_pass = False
        report += [f"## {pool.upper()} × {split_name}（{tr}）",
                   f"实际区间: {stats.get('backtest_start')} → {stats.get('backtest_end')}",
                   "",
                   f"结果: `{zp.name}`" + (f"（{len(skipped)} 个品种缺 {tf} 数据未计入，"
                                           f"补数据: ./ensure-data.sh user_data/universe/pairs_{pool}.txt）"
                                           if skipped else ""),
                   "",
                   f"trades={portfolio['trades']} profit=${portfolio['profit_abs']:,.0f} "
                   f"win%={portfolio['win_rate'] * 100:.1f} PF={portfolio['pf']:.2f} "
                   f"dd={portfolio['dd'] * 100:.1f}% 盈利品种={portfolio['pairs_profitable']}/{portfolio['pairs_total']}",
                   f"**{ann}**",
                   f"逐年收益率（每年重置 $1,000 本金）: {yearly_pct}",
                   f"逐年收益率（钱包口径 ÷${wallet:,.0f}，首尾年不足年）: {yearly_wallet}",
                   "",
                   "```", fmt_table(*table), "```", "", "| 门禁 | 结果 | 说明 |", "|---|---|---|"]
        report += [f"| {name} | {verdict} | {detail} |" for name, verdict, detail in gates]
        report.append("")

    verdict = "✅ 验证通过（全部门禁 PASS）" if all_pass else "❌ 未通过（见上方 FAIL 项）"
    last_pool = runs[-1][0].upper()
    print(f"\n{args.strategy} × {last_pool} → {verdict}" if len(runs) == 1 else f"\n{args.strategy} → {verdict}")
    report += ["## 结论", "", verdict, ""]

    if not args.no_report and runs:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"validate_{args.strategy}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        out.write_text("\n".join(report), encoding="utf-8")
        sidecar["verdict"] = "PASS" if all_pass else "FAIL"
        out.with_suffix(".json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
        print(f"报告: {out}（+ .json 旁车）")
    # 摩擦测试（--fee）不记台账：同版本换费率不构成窥视
    val_runs = [r for r in runs if r[1] == "VAL"]
    if val_runs and args.fee is None:
        val_ledger_append(args.strategy, sha, [r[0] for r in val_runs], [r[5].name for r in val_runs])
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
