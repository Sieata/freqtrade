"""规范化策略验证：测试集(TEST)/验证集(VAL) × 双币池(core/volume) 一键跑完 + 门禁判定。

流程标准（详见 STRATEGY_WORKFLOW.md，切分与币池的唯一权威来源在 user_data/universe/）：
  时间:   TEST 20220101-20240828（调参只准用这段，2021 仅暖机）
          VAL  20240828-          （定版候选只跑一次；跑过又改参 = 作废重来）
  币池:   CORE   pairs_core.txt    实盘允许池（市值 Top50）
          VOLUME pairs_volume.txt  泛化测试池（24h 成交量 Top30，禁实盘）
  口径:   泛化验证用独立口径 —— --stake-amount 1000 --max-open-trades <池内品种数>，
          每笔固定 $1,000，与 pool_review.py 的独立口径一致；复利口径留给定版后的
          单池配置回测。--cache none 恒定（对账纪律）。

门禁（任一 FAIL 则退出码 1）:
  TEST: 总利润>0 且 PF>1.0 且 ≥80% 品种盈利（独立口径）
  VAL : 同上 且 max_relative_drawdown ≤ 30%；笔数<20 只警告
  警告: 利润集中度（top 品种占比>50% 或其利润 80% 集中在单年）→ 防新币单年 pump

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
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
UNIVERSE = ROOT / "user_data" / "universe"
STRATEGIES = ROOT / "user_data" / "strategies"
BT_DIR = ROOT / "user_data" / "backtest_results"
REPORT_DIR = ROOT / "user_data" / "reports"
STAKE = 1000.0

GATES_TEST = {"min_pair_win_rate": 0.80}
GATES_VAL = {"min_pair_win_rate": 0.80, "max_dd": 0.30}


def load_splits():
    with open(UNIVERSE / "splits.json") as f:
        return json.load(f)


def load_pool(name):
    """读币池文件 → [(pair, 注释dict)]，'#' 起注释，行内 'k=v' 解析进 dict。"""
    path = UNIVERSE / f"pairs_{name}.txt"
    out = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)
        pair = line[0].strip()
        if not pair:
            continue
        meta = {}
        if len(line) > 1:
            for tok in line[1].split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    meta[k] = v
        out.append((pair, meta))
    return out


def strategy_timeframe(strategy, config_path):
    """策略类里的 timeframe 优先，回退 config。"""
    src = (STRATEGIES / f"{strategy}.py").read_text()
    m = re.search(r"timeframe\s*=\s*['\"]([^'\"]+)['\"]", src)
    if m:
        return m.group(1)
    with open(config_path) as f:
        return json.load(f).get("timeframe", "4h")


def data_available(pair, tf):
    """本地是否已有该品种该周期的 K 线 feather。"""
    slug = pair.replace("/", "_").replace(":", "_")
    return (ROOT / "user_data" / "data" / "binance" / "futures" / f"{slug}-{tf}-futures.feather").exists()


def newest_zip(exclude):
    zips = [p for p in BT_DIR.glob("backtest-result-*.zip") if p not in exclude]
    return max(zips, key=lambda p: p.stat().st_mtime) if zips else None


def run_backtest(strategy, config, timerange, pairs, max_open_trades, fee=None):
    import os

    cmd = [
        sys.executable, "-m", "freqtrade", "backtesting",
        "--config", str(config),
        "--strategy", strategy,
        "--timerange", timerange,
        "--pairs", *pairs,
        "--cache", "none",
        "--export", "trades",
        "--max-open-trades", str(max_open_trades),
        # 启动余额 ≥ 每笔本金 × 最大并发仓 × 1.2，否则 freqtrade 报
        # "Starting balance smaller than stake_amount" 配置错误
        "--dry-run-wallet", str(int(STAKE * max_open_trades * 1.2)),
        "--stake-amount", str(int(STAKE)),
    ]
    if fee is not None:
        cmd += ["--fee", str(fee)]
    # 回测虽用本地数据，但 freqtrade 启动仍要 reload_markets（走 API），
    # 本机直连 binance 不通，必须带代理（FT_PROXY 可覆盖，none 直连）
    env = os.environ.copy()
    proxy = os.environ.get("FT_PROXY", "http://127.0.0.1:7897")
    if proxy != "none":
        env.setdefault("https_proxy", proxy)
        env.setdefault("http_proxy", proxy)
    before = set(BT_DIR.glob("backtest-result-*.zip"))
    print(f"\n$ {' '.join(cmd[:12])} ... ({len(pairs)} pairs)", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, env=env)
    zip_path = newest_zip(before)
    if proc.returncode != 0 or not zip_path:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
        raise SystemExit(f"freqtrade backtesting 失败（exit {proc.returncode}）:\n{tail}")
    return zip_path


def parse_result(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if not n.endswith(".json"):
                continue
            d = json.loads(z.read(n))
            if isinstance(d, dict) and "strategy" in d:
                name = list(d["strategy"])[0]
                s = d["strategy"][name]
                return s, s.get("trades", [])
    raise SystemExit(f"{zip_path}: 无策略结果")


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

    dd = stats.get("max_drawdown_account", stats.get("max_drawdown_abs", 0) or 0)
    pf = stats.get("profit_factor")
    portfolio = {
        "trades": stats.get("total_trades", len(trades)),
        "profit_abs": stats.get("profit_total_abs", 0.0),
        "win_rate": stats.get("winrate", 0.0),
        "pf": pf if pf else (float("inf") if stats.get("profit_total_abs", 0) > 0 else 0.0),
        "dd": dd if isinstance(dd, float) else 0.0,
        "pairs_profitable": len(prof_pairs),
        "pairs_total": len(pairs),
    }

    # 年化（2026-08-29 展示约定）：固定 $1,000/笔不复利；钱包 = max_open_trades×1.2×$1,000
    if trades:
        o = pd.Timestamp(min(t["open_date"] for t in trades))
        c = pd.Timestamp(max(t["close_date"] for t in trades))
        span_years = max((c - o).total_seconds() / 86400 / 365.25, 1e-9)
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
                    f"{portfolio['dd'] * 100:.1f}%（钱包口径 max_relative_drawdown）"))
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
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="规范化策略验证（TEST/VAL × core/volume）")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--pool", choices=["top10", "core", "volume", "both"], default="both")
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
            zp = run_backtest(args.strategy, args.config, tr, have, len(have), args.fee)
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
    for pool, split_name, tr, have, skipped, zp in runs:
        stats, trades = parse_result(zp)
        portfolio, table, conc = analyze(stats, trades, len(have))
        gates = gate_check(split_name, portfolio, conc)
        if any(r[1] == "FAIL" for r in gates):
            all_pass = False
        ann = (f"年化: 钱包口径 {portfolio['ann_wallet'] * 100:+.1f}%/年 · "
               f"占仓口径 {portfolio['ann_deployed'] * 100:+.1f}%/年"
               f"（平均并发 {portfolio['avg_conc']:.1f} 仓，{portfolio['years']:.2f} 年）"
               if "ann_wallet" in portfolio else "年化: 无交易")
        print(f"\n=== {pool.upper()} × {split_name} ({tr}) ===")
        print(f"trades={portfolio['trades']}  profit=${portfolio['profit_abs']:,.0f}  "
              f"win%={portfolio['win_rate'] * 100:.1f}  PF={portfolio['pf']:.2f}  "
              f"dd={portfolio['dd'] * 100:.1f}%  盈利品种={portfolio['pairs_profitable']}/{portfolio['pairs_total']}")
        print(ann)
        print(f"独立口径品种×年度（$1,000/笔）:")
        print(fmt_table(*table))
        for name, verdict, detail in gates:
            mark = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ "}[verdict]
            print(f"  {mark} {name}: {detail}")
            if verdict == "FAIL":
                all_pass = False
        report += [f"## {pool.upper()} × {split_name}（{tr}）",
                   f"结果: `{zp.name}`" + (f"（{len(skipped)} 个品种缺 {tf} 数据未计入，"
                                           f"补数据: ./ensure-data.sh user_data/universe/pairs_{pool}.txt）"
                                           if skipped else ""),
                   "",
                   f"trades={portfolio['trades']} profit=${portfolio['profit_abs']:,.0f} "
                   f"win%={portfolio['win_rate'] * 100:.1f} PF={portfolio['pf']:.2f} "
                   f"dd={portfolio['dd'] * 100:.1f}% 盈利品种={portfolio['pairs_profitable']}/{portfolio['pairs_total']}",
                   f"**{ann}**",
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
        out.write_text("\n".join(report))
        print(f"报告: {out}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
