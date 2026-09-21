"""按自然年切片回测：回答"只跑某一年（或某段自定区间）成什么样"。

用途：validate_strategy.py 的 TEST/VAL 是冻结窗口，看不到单年表现；本脚本把窗口切成
自然年（默认 2026），用与验证脚本**完全相同**的独立口径（每笔固定 $1,000、
max_open_trades = 池内品种数、--cache none）重跑并出表。

口径约定（STRATEGY_WORKFLOW 第〇节 0.4）：
  - 逐年收益率 = 当年利润 ÷ $1,000（每年重置本金，跨年不复利不共用额度）
  - 每笔期望、胜率、PF、p 值、自举 CI、去最赚 1 笔、最大品种占比 均按独立口径算
  - 除 V2/V1 外无冻结问题；这些年份落在已消费 VAL 窗口内属描述性复核，**不得据此调参**

用法:
  ./.venv/Scripts/python.exe user_data/scripts/year_slice.py --year 2026 --pool top10
  ... --pools top10,top5,top2 --arms WeekendReverseV2,CrashBuyV2
  ... --year 2025 --range 20250101-20260101     # 自定区间覆盖整年
"""
import argparse
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from arm_stats import boot_ci, welch_p  # noqa: E402

import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = ROOT / "user_data" / "universe"
BT_DIR = ROOT / "user_data" / "backtest_results"
STAKE = 1000.0
PROXY = os.environ.get("FT_PROXY", "http://127.0.0.1:7897")

# 策略 → config（BigMove 需要自己的 config，别的一律 perpetual）
CONFIGS = {"BigMoveV1": "user_data/config_bigmove.json"}
DEFAULT_CFG = "user_data/config_perpetual.json"
DEFAULT_ARMS = "WeekendReverseV2,CrashBuyV2,OIFlushV2,BigMoveV1,FundingSqueezeV1L"
POOL_ARMS = {"top10": None, "top5": "WeekendReverseV2,CrashBuyV2", "top2": "WeekendReverseV2,CrashBuyV2"}


def load_pairs(pool):
    lines = (UNIVERSE / f"pairs_{pool}.txt").read_text(encoding="utf-8").splitlines()
    return [l.split("#")[0].strip() for l in lines if l.split("#")[0].strip() and "/" in l]


def run_backtest(strategy, pairs, timerange, tag, fee=None):
    cfg = CONFIGS.get(strategy, DEFAULT_CFG)
    wallet = int(STAKE * len(pairs) * 1.2)
    cmd = [sys.executable, "-m", "freqtrade", "backtesting",
           "--config", cfg, "--strategy", strategy,
           "--timerange", timerange, "--pairs", *pairs,
           "--cache", "none", "--export", "trades",
           "--max-open-trades", str(len(pairs)),
           "--dry-run-wallet", str(wallet), "--stake-amount", str(int(STAKE))]
    if fee is not None:
        cmd += ["--fee", str(fee)]
    env = os.environ.copy()
    if PROXY != "none":
        env.setdefault("https_proxy", PROXY)
        env.setdefault("http_proxy", PROXY)
    before = set(BT_DIR.glob("backtest-result-*.zip"))
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, env=env)
    new = sorted(set(BT_DIR.glob("backtest-result-*.zip")) - before, key=lambda p: p.stat().st_mtime)
    if proc.returncode != 0 or not new:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-20:])
        print(f"  !! {strategy} @ {tag} 失败（exit {proc.returncode}）\n{tail}", flush=True)
        return None
    return new[-1]


def stats_from_zip(zip_path):
    rows = []
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if not n.endswith(".json"):
                continue
            d = json.loads(z.read(n))
            if isinstance(d, dict) and "strategy" in d:
                rows += d["strategy"][list(d["strategy"])[0]].get("trades", [])
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["pair_base"] = df["pair"].str.split("/").str[0]
    df["profit$"] = df["profit_ratio"] * STAKE
    df["close_dt"] = pd.to_datetime(df["close_date"], utc=True)
    p = df["profit$"]
    total = p.sum()
    gw, gl = p[p > 0].sum(), -p[p < 0].sum()
    mon = df.groupby(df["close_dt"].dt.strftime("%Y-%m"))["profit$"].sum()
    share = df.groupby("pair_base")["profit$"].sum()
    return {
        "n": len(df), "total": total, "mean": p.mean(), "win": (p > 0).mean() * 100,
        "pf": gw / gl if gl > 0 else float("inf"),
        "p": welch_p(p), "ci": boot_ci(p),
        "ex_best": total - p.max(), "best_share": p.max() / total * 100 if total else float("nan"),
        "top_pair": share.idxmax(), "top_share": share.max() / total * 100 if total else float("nan"),
        "pairs_traded": df["pair_base"].nunique(),
        "pairs_win": (share > 0).mean() * 100,
        "worst_month": mon.min(), "neg_month": (mon < 0).mean() * 100,
        "avg_win": p[p > 0].mean() if (p > 0).any() else 0,
        "avg_loss": p[p < 0].mean() if (p < 0).any() else 0,
        "monthly": mon,
        "pair_pnl": share.sort_values(ascending=False),
    }


def parse_range(timerange):
    """'20260101-' / '20250101-20260101' / '2026-' → (start_ts, end_ts|None)"""
    def _p(s):
        s = s.strip()
        if len(s) == 8:
            return pd.Timestamp(f"{s[:4]}-{s[4:6]}-{s[6:]}", tz="UTC")
        if len(s) == 6:
            return pd.Timestamp(f"{s[:4]}-{s[4:]}", tz="UTC")
        if len(s) == 4:
            return pd.Timestamp(f"{s}-01-01", tz="UTC")
        return None
    a, _, b = timerange.partition("-")
    return _p(a), (_p(b) if b.strip() else None)


def market_return(sym, timerange, kind="4h"):
    """区间内买持收益（首根收盘→末根收盘）。必须按区间过滤——否则会把全历史起点算进来。"""
    f = ROOT / f"user_data/data/binance/futures/{sym}_USDT_USDT-{kind}-futures.feather"
    if not f.exists():
        return None
    start, end = parse_range(timerange)
    d = pd.read_feather(f)
    d = d[d["date"] >= start]
    if end is not None:
        d = d[d["date"] < end]
    if len(d) < 2:
        return None
    return d["close"].iloc[-1] / d["close"].iloc[0] - 1


def main():
    ap = argparse.ArgumentParser(description="按自然年（或自定区间）切片回测")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--range", default="", help="覆盖整年区间，如 20250101-20260101")
    ap.add_argument("--pools", default="top10")
    ap.add_argument("--arms", default="", help="只对第一个池生效；其余池用 POOL_ARMS 默认")
    ap.add_argument("--fee", type=float, default=None)
    ap.add_argument("--monthly", action="store_true", help="额外打印月度与品种盈亏分布")
    args = ap.parse_args()

    timerange = args.range or (f"{args.year}0101-{args.year + 1}0101" if args.year < 2026
                               else f"{args.year}0101-")
    pools = [p for p in args.pools.split(",") if p]
    note = ""
    if args.year >= 2026 and not args.range:
        note = "（数据到 2026-08-29 04:00，本年只有 YTD 8 个月）"
    print(f"区间 {timerange}{note}  口径 独立 ${STAKE:,.0f}/笔 · max_open_trades=池内品种数"
          + (f" · fee {args.fee}" if args.fee is not None else ""))

    for pool in pools:
        pairs = load_pairs(pool)
        arms = ([a for a in args.arms.split(",") if a] if (args.arms and pool == pools[0])
                else [a for a in (POOL_ARMS.get(pool) or DEFAULT_ARMS).split(",") if a])
        print("\n" + "=" * 118)
        print(f"币池 {pool}（{len(pairs)} 品种）  臂：{', '.join(arms)}")
        print("=" * 118)
        hdr = (f"{'策略':<19}{'n':>5}{'利润$':>9}{'收益率':>8}{'每笔$':>8}{'胜率%':>7}{'PF':>6}"
               f"{'p值':>10}{'95%CI 每笔$':>17}{'去最赚1笔$':>11}{'最大品种':>13}{'最差月$':>10}")
        print(hdr)
        print("-" * len(hdr))
        for arm in arms:
            z = run_backtest(arm, pairs, timerange, pool, args.fee)
            if z is None:
                print(f"{arm:<19} —")
                continue
            s = stats_from_zip(z)
            if s is None:
                print(f"{arm:<19} 无交易")
                continue
            print(f"{arm:<19}{s['n']:>5}{s['total']:>9,.0f}{s['total']/STAKE*100:>7.0f}%"
                  f"{s['mean']:>8,.1f}{s['win']:>7.1f}{s['pf']:>6.2f}{s['p']:>10.2e}"
                  f"{'[' + format(s['ci'][0], '+,.0f') + ',' + format(s['ci'][1], '+,.0f') + ']':>17}"
                  f"{s['ex_best']:>11,.0f}{s['top_pair'] + ' ' + format(s['top_share'], '.0f') + '%':>13}"
                  f"{s['worst_month']:>10,.0f}")
            print(f"{'':<19} 品种盈利 {s['pairs_win']:.0f}%（{s['pairs_traded']} 个有交易）"
                  f" · 赢家均 ${s['avg_win']:,.0f} / 输家均 ${s['avg_loss']:,.0f}"
                  f" · 最赚 1 笔占 {s['best_share']:.0f}% · 负月 {s['neg_month']:.0f}%")
            if args.monthly:
                print(f"{'':<19} 月度：" + "  ".join(
                    f"{k[5:]} {v:+,.0f}" for k, v in s["monthly"].items()))
                print(f"{'':<19} 品种：" + "  ".join(
                    f"{k} {v:+,.0f}" for k, v in s["pair_pnl"].items()))

    # 同期行情参照（买持基准）
    print("\n同期买持（年初→最新收盘）：")
    for sym in ["BTC", "ETH", "BNB", "XRP", "SOL"]:
        r = market_return(sym, timerange)
        if r is not None:
            print(f"   {sym:<5}{r*100:>7.1f}%", end="")
    print()


if __name__ == "__main__":
    main()
