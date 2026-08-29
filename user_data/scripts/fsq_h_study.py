"""FS 持有期参数修订研究（RESEARCH.md 十五 预注册）：hold_hours 轴 × 池，仅 TEST。

复用 fsq_batch.py 的参数变体机制（临时副本，不动冻结文件），池从 user_data/universe/ 读取。
口径与 validate_strategy.py 一致：独立口径 $1,000/笔、--max-open-trades=池内品种数、--cache none。

用法:
  .venv/Scripts/python user_data/scripts/fsq_h_study.py --pool top10 --combos "0.02:72,0.02:84"
  可选: --fee 0.001  --tag myrun
输出: 每组合一行摘要（含逐年 $ 与收益率%）+ JSON 落 user_data/reports/logs/fsq_h_study_<tag>.json
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "user_data" / "scripts"))
from fsq_batch import make_variant  # noqa: E402  变体生成与 fsq_batch 逐字一致

UNIVERSE = ROOT / "user_data" / "universe"
BT_DIR = ROOT / "user_data" / "backtest_results"
DATA_DIR = ROOT / "user_data" / "data" / "binance" / "futures"
LOG_DIR = ROOT / "user_data" / "reports" / "logs"
TR_TEST = "20220101-20240828"
STAKE = 1000


def load_pool(name):
    """pairs_<name>.txt → 剔注释后的 pair 列表（生成日快照）。"""
    out = []
    for line in (UNIVERSE / f"pairs_{name}.txt").read_text().splitlines():
        pair = line.split("#", 1)[0].strip()
        if pair:
            out.append(pair)
    return out


def have_data(pairs, timeframe="4h"):
    """本地有 4h feather 的品种（与 validate_strategy 的 have 逻辑同规则）。"""
    keep, skipped = [], []
    for p in pairs:
        base = p.split("/")[0]
        f = DATA_DIR / f"{base}_USDT_USDT-{timeframe}-futures.feather"
        (keep if f.exists() else skipped).append(p)
    return keep, skipped


def run_backtest(name, text, pairs, fee=None):
    tmp = Path(tempfile.gettempdir()) / "fsq_h_study"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / f"{name}.py").write_text(text)
    before = set(BT_DIR.glob("backtest-result-*.zip"))
    cmd = [
        sys.executable, "-m", "freqtrade", "backtesting",
        "--config", str(ROOT / "user_data" / "config_perpetual.json"),
        "--strategy-path", str(tmp), "--strategy", name,
        "--timerange", TR_TEST, "--pairs", *pairs,
        "--cache", "none", "--export", "trades",
        "--max-open-trades", str(len(pairs)),
        "--dry-run-wallet", str(int(STAKE * len(pairs) * 1.2)),
        "--stake-amount", str(STAKE),
    ]
    if fee is not None:
        cmd += ["--fee", str(fee)]
    env = os.environ.copy()
    env.setdefault("https_proxy", "http://127.0.0.1:7897")
    env.setdefault("http_proxy", "http://127.0.0.1:7897")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    if proc.returncode != 0:
        raise SystemExit(f"{name} 回测失败:\n" + "\n".join((proc.stdout + proc.stderr).splitlines()[-15:]))
    zp = max((p for p in BT_DIR.glob("backtest-result-*.zip") if p not in before),
             key=lambda p: p.stat().st_mtime)
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():
            if not n.endswith(".json"):
                continue
            d = json.loads(z.read(n))
            if isinstance(d, dict) and "strategy" in d:
                return d["strategy"][name].get("trades", [])
    raise SystemExit("结果解析失败")


def analyze(trades):
    cell = defaultdict(float)   # (base, year) -> $
    wins = defaultdict(int)
    cnt = defaultdict(int)
    for t in trades:
        base, year = t["pair"].split("/")[0], t["close_date"][:4]
        cell[(base, year)] += t["profit_ratio"] * STAKE
        wins[(base, year)] += t["profit_ratio"] > 0
        cnt[(base, year)] += 1
    years = sorted({k[1] for k in cell})
    ytot = {y: sum(cell.get((p, y), 0.0) for p in {k[0] for k in cell}) for y in years}
    pair_tot = defaultdict(float)
    for (p, y), v in cell.items():
        pair_tot[p] += v
    pos = sorted(pair_tot)
    pf_n = sum(t["profit_ratio"] * STAKE for t in trades if t["profit_ratio"] > 0)
    pf_d = -sum(t["profit_ratio"] * STAKE for t in trades if t["profit_ratio"] <= 0)
    top_pair, top_val = max(pair_tot.items(), key=lambda kv: kv[1], default=(None, 0.0))
    tot = sum(pair_tot.values())
    return {
        "trades": len(trades),
        "total": round(tot, 1),
        "pf": round(pf_n / pf_d, 3) if pf_d else None,
        "win_pct": round(100 * sum(wins.values()) / len(trades), 1) if trades else 0.0,
        "pairs_pos": sum(1 for v in pair_tot.values() if v > 0),
        "pairs_n": len(pair_tot),
        "years": {y: round(v, 1) for y, v in ytot.items()},
        "years_pct": {y: round(100 * v / STAKE, 1) for y, v in ytot.items()},  # 0.4 口径：当年 $1,000 本金
        "top_pair": f"{top_pair} {100 * top_val / tot:.0f}%" if tot > 0 and top_pair else None,
        "pair_tot": {p: round(pair_tot[p], 1) for p in pos},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True, choices=["top10", "core", "volume"])
    ap.add_argument("--combos", required=True, help='逗号分隔 q:h，如 "0.02:72,0.02:96"')
    ap.add_argument("--fee", type=float, default=None)
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    combos = []
    for part in args.combos.split(","):
        q, h = part.strip().split(":")
        combos.append((float(q), int(h)))

    pool_pairs, missing = have_data(load_pool(args.pool))
    print(f"池={args.pool} 用 {len(pool_pairs)} 品种" +
          (f"（缺 4h 数据 {len(missing)} 个: {', '.join(p.split('/')[0] for p in missing)}）" if missing else ""),
          flush=True)

    tag = args.tag or dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    rows = []
    for q, h in combos:
        name, text = make_variant(q, h)
        trades = run_backtest(name, text, pool_pairs, fee=args.fee)
        r = analyze(trades)
        r.update({"q": q, "h": h, "fee": args.fee, "pool": args.pool})
        rows.append(r)
        ys = " ".join(f"{y}:{r['years'][y]:+,.0f}({r['years_pct'][y]:+.1f}%)" for y in r["years"])
        print(f"q={q:.2f} h={h:<4}{r['trades']:>6}{r['total']:>+11,.0f}"
              f"{(r['pf'] if r['pf'] else 0):>7.2f}{r['win_pct']:>7.1f}"
              f"{r['pairs_pos']:>4}/{r['pairs_n']:<3} top:{r['top_pair'] or '-'}  {ys}", flush=True)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = LOG_DIR / f"fsq_h_study_{args.pool}_{tag}.json"
    out.write_text(json.dumps({"pool": args.pool, "fee": args.fee, "timerange": TR_TEST,
                               "pairs": pool_pairs, "rows": rows}, ensure_ascii=False, indent=1))
    print(f"JSON → {out}")


if __name__ == "__main__":
    main()
