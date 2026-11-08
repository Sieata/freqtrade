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
    "OIFlushV2": os.path.join(ROOT, "user_data", "tradesv3.dryrun.oi.sqlite"),
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
    "OIFlushV2": {
        "profit_abs": 1293,     # TOP10 VAL 合计（独立 $1,000/笔；validate_..._20261019）
        "winrate": 62.0,        # VAL win_rate 0.62
        "dd": 2.4,              # VAL ddW 2.38%
        "trades": 52,           # VAL n=52（TEST 44）
        "start_wallet": 12000,  # config_paper_oi: 8 并发 × $1,000 + 缓冲
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
    # FREEZE_OI 第四节: 事件臂需补样本到显著，6 个月 ≥20 笔 / 胜率≥45% / dd≤10%
    "OIFlushV2": {"min_trades": 20, "min_winrate": 45.0, "max_dd": 10.0},
}

# 冻结 paper 池里的非 TOP10 小币（V2 池 = 8 品种，含 BANK/CYS 而非 BNB/TRX/DOGE/XMR）。
# 用途：--split-pool 把 forward-test 结果按"TOP10 品种 vs 小币"分列，避免 BANK/CYS 的新币 pump
# 冒充 V2 机制 edge（RESEARCH 26.20：VAL 段这两币 35% 交易却 44% 利润）。
SMALL_CAP_PAIRS = ("BANK/USDT:USDT", "CYS/USDT:USDT")


def split_by_pool(rows, small_caps=SMALL_CAP_PAIRS):
    """把已平仓 rows 拆成 (core_rows, small_cap_rows)。rows 元素 = (pair, profit, close_date, ...)。

    按 pair 前缀匹配小币（容忍交易所后缀差异）。返回两个 list，保持原顺序。
    """
    sc = tuple(p.split("/")[0].upper() for p in small_caps)

    def is_small(pair):
        base = str(pair).split("/")[0].upper()
        return base in sc

    core = [r for r in rows if not is_small(r[0])]
    small = [r for r in rows if is_small(r[0])]
    return core, small


def cohort_stats(rows):
    """一组已平仓 rows 的 (笔数, 胜率%, 总利润)。空集返回 (0, 0.0, 0.0)。"""
    n = len(rows)
    if n == 0:
        return 0, 0.0, 0.0
    profit = sum(r[1] for r in rows)
    winrate = sum(1 for r in rows if r[1] > 0) / n * 100
    return n, winrate, profit


def event_clusters(rows, max_gap_days=5):
    """把已平仓 rows 按 close_date 聚成独立事件簇（相邻开仓/平仓日间隔 > max_gap_days 分界）。

    rows 元素 = (pair, profit, close_date, ...)。用于 OI 事件臂：20 笔不等于 20 个独立事件，
    若利润被单一大额共振日绑架，胜率会误导（RESEARCH 26.24）。
    返回 list[(cluster_start, cluster_end, n_trades, profit)]，按时间升序。
    """
    import datetime as _dt
    items = sorted(rows, key=lambda r: r[2])
    clusters = []
    cur = None
    prev = None
    for r in items:
        cd = r[2]
        if isinstance(cd, str):
            cd = _dt.datetime.fromisoformat(cd.split("+")[0].split("Z")[0])
        day = cd.date() if hasattr(cd, "date") else cd
        if cur is None or prev is None or (day - prev).days > max_gap_days:
            if cur is not None:
                clusters.append(cur)
            cur = [day, day, 0, 0.0]
        cur[1] = day
        cur[2] += 1
        cur[3] += r[1]
        prev = day
    if cur is not None:
        clusters.append(cur)
    return [(a, b, n, p) for (a, b, n, p) in clusters]


def load_closed(db):
    if not os.path.exists(db):
        return None, None
    con = sqlite3.connect(db)
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()]
    if not cols:
        # db 文件存在但从未初始化（0 字节/无 trades 表：paper 尚未产生任何交易）。
        # 视为“无 db”，交给 main() 打印友好提示，而不是让 SELECT 抛 OperationalError。
        con.close()
        return None, None
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
    ap.add_argument("--split-pool", action="store_true",
                    help="按 TOP10品种 vs 小币(BANK/CYS) 分列胜率/利润，防止小币pump冒充机制edge（RESEARCH 26.20）")
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
        print(f"  判据：需 ≥{CRITERIA['min_trades']} 笔已平仓交易才具统计意义（当前 0 笔）。")
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
    if args.split_pool:
        core, small = split_by_pool(rows)
        cn, cwr, cp = cohort_stats(core)
        sn, swr, sp = cohort_stats(small)
        share = (sp / total_profit * 100) if total_profit else 0.0
        print("Pool split (TOP10-pairs vs small-cap BANK/CYS)  [RESEARCH 26.20]:")
        print(f"  {'cohort':<22s} {'trades':>7s} {'win%':>7s} {'profit$':>12s}")
        print(f"  {'TOP10/中大盘':<20s} {cn:>7d} {cwr:>6.1f}% {cp:>12,.2f}")
        print(f"  {'小币(BANK/CYS)':<19s} {sn:>7d} {swr:>6.1f}% {sp:>12,.2f}")
        if sn:
            print(f"  → 小币占利润 {share:.0f}%；达标判读须以「TOP10/中大盘」cohort 为准，")
            print(f"    小币(2025-26才上市、无完整周期)的贡献视为不确定项。")
        print("-" * 66)
    if args.strategy == "OIFlushV2":
        cl = event_clusters(rows)
        print("Event-cluster concentration  [RESEARCH 26.24]:")
        print(f"  {n} 笔 → {len(cl)} 个独立事件簇（相邻平仓日间隔>5天分界）")
        if cl:
            top = max(cl, key=lambda c: c[3])
            share_p = (top[3] / total_profit * 100) if total_profit else 0.0
            share_n = top[2] / n * 100
            print(f"  最大单簇：{top[0]}~{top[1]}  {top[2]} 笔({share_n:.0f}%)  ${top[3]:,.2f}(利润 {share_p:.0f}%)")
            print(f"  → 判读：≥{CRITERIA['min_trades']} 笔≈{len(cl)} 个事件（样本合理），")
            print(f"    但利润/PF 由罕见大额共振主导；须并列看「笔数/胜率」与「是否含大额簇」，")
            print(f"    勿只看胜率（胜率可被单一大共振日绑架）。")
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
