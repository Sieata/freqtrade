"""Pool review: per-pair × per-year independent-metric table from a backtest result zip.

独立口径 = 每笔按固定 $1,000 本金计盈亏（profit_ratio × 1000），不受满仓复利
路径影响，用于回答"edge 属于哪个品种/哪一年"（复利归因受交易顺序影响，不可用
于选池 —— 见 FREEZE_V2.md 第 6.5 节）。

用法:
  .venv/bin/python user_data/scripts/pool_review.py user_data/backtest_results/<zip> [--worst 10]

输出:
  1. 品种 × 年度独立盈亏矩阵（$，固定 $1000/笔）
  2. 每品种合计、胜率、笔数
  3. 利润集中度提示（Top 品种占比、单年占比）
  4. --worst N: 最差 N 笔（检查止损缺口穿透）
"""
import argparse
import json
import sys
import zipfile
from collections import defaultdict

STAKE = 1000.0


def load(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.endswith(".json") and "meta" not in n.lower() or n.endswith(".json")]
        # freqtrade 结果 zip 内为一个主 json（meta json 在 .last_result.json 之外单独存在时跳过）
        for n in names:
            d = json.loads(z.read(n))
            if isinstance(d, dict) and "strategy" in d:
                strat = d["strategy"]
                name = list(strat.keys())[0]
                return name, strat[name].get("trades", []), d
    raise SystemExit(f"no strategy results found in {zip_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("--worst", type=int, default=0)
    args = ap.parse_args()

    name, trades, raw = load(args.zip)
    if not trades:
        raise SystemExit("result zip has no trades (was it run with --export trades?)")

    cell = defaultdict(float)   # (pair, year) -> $ pnl
    cnt = defaultdict(int)
    wins = defaultdict(int)
    for t in trades:
        y = t["close_date"][:4]
        key = (t["pair"].split("/")[0], y)
        cell[key] += t["profit_ratio"] * STAKE
        cnt[key] += 1
        wins[key] += 1 if t["profit_ratio"] > 0 else 0

    pairs = sorted({k[0] for k in cell})
    years = sorted({k[1] for k in cell})

    print(f"strategy: {name}   trades: {len(trades)}")
    print(f"independent metric: fixed ${STAKE:.0f}/trade\n")

    hdr = f"{'pair':<8}" + "".join(f"{y:>12}" for y in years) + f"{'total':>12}{'n':>6}{'win%':>8}"
    print(hdr)
    print("-" * len(hdr))
    row_total = defaultdict(float)
    for p in pairs:
        vals = [cell.get((p, y), 0.0) for y in years]
        tot = sum(vals)
        n = sum(cnt[(p, y)] for y in years)
        w = sum(wins[(p, y)] for y in years)
        print(f"{p:<8}" + "".join(f"{v:>12,.0f}" for v in vals) + f"{tot:>12,.0f}{n:>6}{(100*w/n if n else 0):>8.1f}")
        for y, v in zip(years, vals):
            row_total[y] += v
    print("-" * len(hdr))
    print(f"{'TOTAL':<8}" + "".join(f"{row_total[y]:>12,.0f}" for y in years)
          + f"{sum(row_total.values()):>12,.0f}{len(trades):>6}{100*sum(1 for t in trades if t['profit_ratio']>0)/len(trades):>8.1f}")

    # 集中度提示
    pair_tot = {p: sum(cell.get((p, y), 0.0) for y in years) for p in pairs}
    grand = sum(pair_tot.values())
    ranked = sorted(pair_tot.items(), key=lambda kv: kv[1], reverse=True)
    print("\nconcentration:")
    top3 = ranked[:3]
    print(f"  top3 pairs: {', '.join(f'{p} {v/grand*100:.0f}%' for p, v in top3)}  = {sum(v for _, v in top3)/grand*100:.0f}% of total")
    for p, v in top3:
        ys = [(y, cell[(p, y)]) for y in years if cell.get((p, y), 0) > 0]
        best = max(ys, key=lambda kv: kv[1]) if ys else ("-", 0)
        print(f"    {p}: best year {best[0]} {best[1]:+,.0f}$  "
              f"({best[1]/v*100:.0f}% of its profit)" if v > 0 else f"    {p}: negative overall")

    if args.worst:
        print(f"\nworst {args.worst} trades (stoploss gap check: profit_ratio < -stoploss means gap-through):")
        for t in sorted(trades, key=lambda t: t["profit_ratio"])[: args.worst]:
            print(f"  {t['open_date'][:16]} {t['pair']:<18} {t['profit_ratio']*100:>7.2f}%  "
                  f"exit={t.get('exit_reason','?')}")


if __name__ == "__main__":
    main()
