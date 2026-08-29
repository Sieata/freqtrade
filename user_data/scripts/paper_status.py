"""Forward-test evaluator（V2/V1/FS/BigMove）
Reads the dry-run SQLite DB and prints a forward-test report vs. backtest baseline.
Usage:
  .venv/bin/python user_data/scripts/paper_status.py                     # V2（默认 db）
  .venv/bin/python user_data/scripts/paper_status.py --strategy FundingSqueezeV1L
  .venv/bin/python user_data/scripts/paper_status.py --strategy BigMoveV1
db 路径按策略自动解析（也可 --db 覆盖）。
"""
import os, sqlite3, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_DEFAULT = os.path.join(ROOT, "user_data", "tradesv3.dryrun.sqlite")
DB_BY_STRATEGY = {
    "FundingSqueezeV1L": os.path.join(ROOT, "user_data", "tradesv3.dryrun.fs.sqlite"),
    "BigMoveV1": os.path.join(ROOT, "user_data", "tradesv3.dryrun.bigmove.sqlite"),
}

# Backtest baselines (frozen in paper/FREEZE.md & FREEZE_V2.md; invalid if strategy changes).
# V2 uses the warmup-complete local rerun figure (FREEZE_V2.md §8: 537 trades / +$375,330).
BASELINES = {
    "WeekendReverseV1": {
        "profit_abs": 206386,   # full-period compound $1000 -> $207,386
        "winrate": 91.4,
        "dd": 20.5,
        "trades": 478,
        "start_wallet": 1000,
    },
    "WeekendReverseV2": {
        "profit_abs": 375330,   # warmup-complete compound $1000 -> $376,330
        "winrate": 90.6,
        "dd": 32.1,
        "trades": 537,
        "start_wallet": 1000,
    },
    # Tier B 事件臂（基线 = 各自 FREEZE 文档的 TOP10 验证口径；独立 $1,000/笔）
    "FundingSqueezeV1L": {
        "profit_abs": 2291,     # TOP10 VAL 合计（每年重置 $1,000 口径）
        "winrate": 50.9,
        "dd": 5.8,
        "trades": 275,
        "start_wallet": 10000,  # config_paper_fs: 8 并发 × $1,000
    },
    "BigMoveV1": {
        "profit_abs": 3739,     # TOP10 VAL 合计
        "winrate": 50.0,
        "dd": 2.2,
        "trades": 38,
        "start_wallet": 5000,   # config_paper_bigmove: 3 并发 × $1,000
    },
}

# Pre-defined forward-test criteria（默认 V1/V2；Tier B 臂各自 FREEZE 判据覆盖）
CRITERIA = {
    "min_trades": 20,      # below this, no statistical meaning
    "min_winrate": 70.0,   # historical 91.4%, wide margin for small sample
    "max_dd": 30.0,        # 1.5x historical 20.5%
}
CRITERIA_BY_STRATEGY = {
    # FREEZE_FS 第四节: 6 个月 ≥50 笔 / PF≥1.2(此处以胜率近似 45%) / dd≤15%
    "FundingSqueezeV1L": {"min_trades": 50, "min_winrate": 45.0, "max_dd": 15.0},
    # FREEZE_BIGMOVE 第四节: 低频 ≥6 笔 / PF≥1.2(胜率近似 40%) / dd≤5%
    "BigMoveV1": {"min_trades": 6, "min_winrate": 40.0, "max_dd": 5.0},
}

def load_closed(db):
    if not os.path.exists(db):
        return None, None
    con = sqlite3.connect(db)
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()]
    prof = "profit_abs" if "profit_abs" in cols else "close_profit_abs"
    rows = cur.execute(
        f"SELECT pair, {prof}, close_date, stake_amount, is_open "
        "FROM trades WHERE is_open = 0 ORDER BY close_date ASC"
    ).fetchall()
    con.close()
    return rows, prof

def max_drawdown(equity):
    peak = -1e18; mdd = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak * 100)
    return mdd

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="WeekendReverseV2", choices=sorted(BASELINES))
    ap.add_argument("--db", default=None, help="覆盖 db 路径（默认按策略解析）")
    args = ap.parse_args()
    BASELINE = BASELINES[args.strategy]
    CRITERIA.update(CRITERIA_BY_STRATEGY.get(args.strategy, {}))
    global DB
    DB = args.db or DB_BY_STRATEGY.get(args.strategy, DB_DEFAULT)

    rows, _ = load_closed(DB)
    if rows is None:
        print("X no dry-run DB found (forward-test not started, or nothing closed yet)")
        print(f"  expected path: {DB}")
        sys.exit(1)
    if not rows:
        print("No closed trades yet. forward-test is running; wait for the first close.")
        sys.exit(0)

    total_profit = sum(r[1] for r in rows)
    wins = sum(1 for r in rows if r[1] > 0)
    n = len(rows)
    winrate = wins / n * 100

    wallet = BASELINE["start_wallet"]
    equity = [wallet]
    for r in rows:
        wallet += r[1]
        equity.append(wallet)

    by_pair = {}
    for r in rows:
        by_pair[r[0]] = by_pair.get(r[0], 0.0) + r[1]

    t0, t1 = rows[0][2], rows[-1][2]
    mdd = max_drawdown(equity)

    print("=" * 66)
    print(f"{args.strategy}  forward-test report (paper / dry-run)")
    print("=" * 66)
    print(f"DB: {DB}")
    print(f"Period: {t0}  ~  {t1}")
    print(f"Closed trades: {n}")
    print(f"Win rate: {winrate:.1f}%")
    print(f"Total profit: ${total_profit:,.2f}")
    print(f"Current wallet: ${wallet:,.2f}  (start ${BASELINE['start_wallet']:,})")
    print(f"Max drawdown: {mdd:.1f}%")
    print("-" * 66)
    print("Profit by pair:")
    for p, v in sorted(by_pair.items(), key=lambda x: -x[1]):
        print(f"  {p:<20s} ${v:>12,.2f}")
    print("-" * 66)
    print("Criteria check (vs. historical backtest baseline):")
    print(f"  {'metric':<16s} {'forward':>14s} {'baseline':>12s} {'criterion':>10s} {'result':>6s}")
    checks = [
        ("trades",       f"{n}",              f"{BASELINE['trades']}",  f">={CRITERIA['min_trades']}",  "OK" if n >= CRITERIA["min_trades"] else "LOW"),
        ("win rate",     f"{winrate:.1f}%",   f"{BASELINE['winrate']}%", f">={CRITERIA['min_winrate']}%", "OK" if winrate >= CRITERIA["min_winrate"] else "FAIL"),
        ("max drawdown", f"{mdd:.1f}%",       f"{BASELINE['dd']}%",     f"<={CRITERIA['max_dd']}%",      "OK" if mdd <= CRITERIA["max_dd"] else "FAIL"),
        ("total profit", f"${total_profit:,.0f}", f"${BASELINE['profit_abs']:,}", ">0",                  "OK" if total_profit > 0 else "FAIL"),
    ]
    for name, ft, base, crit, verdict in checks:
        print(f"  {name:<16s} {ft:>14s} {base:>12s} {crit:>10s} {verdict:>6s}")
    print("-" * 66)
    print("Notes:")
    print("  1. forward-test uses real trade order; baseline is historical compounding,")
    print("     so absolute profits are NOT directly comparable.")
    print("  2. Focus on direction: win rate / drawdown / profitability vs. history.")
    print("  3. Criteria are only meaningful at >=20 trades; early noise is normal.")
    print("=" * 66)

if __name__ == "__main__":
    main()
