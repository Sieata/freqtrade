"""FundingSqueezeV1 过拟合检测 A+B：参数网格批量回测（只用 TEST）。

A 盲参 + B 参数稳定性合并执行：fund_quantile × hold_hours 网格
（原型默认 p2/72h 来自 Phase1 泛化统计而非网格挑选，此处验证"任意合理参数组合都盈利"）。
每个组合解析品种×年度独立口径，逐组合检查"逐年为正"（C 逐年滚动的等价证据：
参数从不按年重选，逐年独立为正即滚动通过）。

用法: .venv/bin/python user_data/scripts/fsq_batch.py [--quick]
输出: 每组合一行摘要 + 门禁式结论；详细表落 user_data/reports/logs/fsq_batch_<ts>.log
"""
import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
STRAT = ROOT / "user_data" / "strategies" / "FundingSqueezeV1.py"
BT_DIR = ROOT / "user_data" / "backtest_results"
TMP = Path("/tmp/fsq_batch")
SRC = (STRAT).read_text()

PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT", "SOL/USDT:USDT",
         "ZEC/USDT:USDT", "DOGE/USDT:USDT", "ADA/USDT:USDT", "AVAX/USDT:USDT", "DOT/USDT:USDT"]
TR_TEST = "20220101-20240828"
STAKE = 1000

GRID = [
    (0.01, 72), (0.02, 48), (0.02, 72), (0.02, 96), (0.05, 48), (0.05, 72),
    (0.05, 96), (0.10, 72), (0.10, 48), (0.02, 24), (0.01, 48), (0.01, 24),
]
# A 盲参组（与默认相距最远的"不相关"组合）
BLIND = [(0.10, 96), (0.05, 24)]


def make_variant(q, h, win=540):
    text = SRC
    text = re.sub(r"fund_quantile = [0-9.]+", f"fund_quantile = {q}", text)
    text = re.sub(r"hold_hours = [0-9]+", f"hold_hours = {h}", text)
    text = re.sub(r"fund_window = [0-9]+", f"fund_window = {win}", text)
    name = f"FundingSqueezeV1_q{int(q * 100):02d}_h{h}"
    text = text.replace("class FundingSqueezeV1(IStrategy):", f"class {name}(IStrategy):")
    return name, text


def run_one(name, text, timerange=TR_TEST):
    TMP.mkdir(parents=True, exist_ok=True)
    f = TMP / f"{name}.py"
    f.write_text(text)
    before = set(BT_DIR.glob("backtest-result-*.zip"))
    import os

    env = os.environ.copy()
    env.setdefault("https_proxy", "http://127.0.0.1:7897")
    env.setdefault("http_proxy", "http://127.0.0.1:7897")
    proc = subprocess.run(
        [sys.executable, "-m", "freqtrade", "backtesting",
         "--config", str(ROOT / "user_data" / "config_perpetual.json"),
         "--strategy-path", str(TMP), "--strategy", name,
         "--timerange", timerange, "--pairs", *PAIRS,
         "--cache", "none", "--export", "trades",
         "--max-open-trades", str(len(PAIRS)),
         "--dry-run-wallet", str(int(STAKE * len(PAIRS) * 1.2)),
         "--stake-amount", str(STAKE)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    if proc.returncode != 0:
        raise SystemExit(f"{name} 回测失败:\n" + "\n".join((proc.stdout + proc.stderr).splitlines()[-10:]))
    zp = max((p for p in BT_DIR.glob("backtest-result-*.zip") if p not in before), key=lambda p: p.stat().st_mtime)
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():
            if not n.endswith(".json"):
                continue
            d = json.loads(z.read(n))
            if isinstance(d, dict) and "strategy" in d:
                s = d["strategy"][name]
                return s.get("trades", [])
    raise SystemExit("解析失败")


def summarize(name, trades):
    cell = defaultdict(float)
    wins = defaultdict(int)
    cnt = defaultdict(int)
    for t in trades:
        key = (t["pair"].split("/")[0], t["close_date"][:4])
        cell[key] += t["profit_ratio"] * STAKE
        wins[key] += t["profit_ratio"] > 0
        cnt[key] += 1
    years = sorted({k[1] for k in cell})
    ytot = {y: sum(cell.get((p, y), 0.0) for p in {k[0] for k in cell}) for y in years}
    pair_tot = defaultdict(float)
    for (p, y), v in cell.items():
        pair_tot[p] += v
    pos_pairs = sum(1 for v in pair_tot.values() if v > 0)
    pf_n, pf_d = 0.0, 0.0
    for t in trades:
        if t["profit_ratio"] > 0:
            pf_n += t["profit_ratio"] * STAKE
        else:
            pf_d -= t["profit_ratio"] * STAKE
    pf = pf_n / pf_d if pf_d else float("inf")
    tot = sum(pair_tot.values())
    ys = " ".join(f"{y}:{ytot[y]:+,.0f}" for y in years)
    all_year_pos = all(v > 0 for v in ytot.values())
    return tot, pf, 100 * sum(wins.values()) / len(trades), pos_pairs, all_year_pos, ys


def parse_only(s):
    """--only "0.02:96,0.05:48" → [(0.02, 96), ...]（续跑：跳过其他设备已完成的组合）"""
    out = []
    for part in s.split(","):
        q, h = part.strip().split(":")
        out.append((float(q), int(h)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="只跑 A 盲参 + 网格子集")
    ap.add_argument("--only", type=parse_only, default=None,
                    help="逗号分隔的 q:h 列表，只跑这些组合（跨设备续跑用）")
    args = ap.parse_args()
    grid = BLIND + GRID[2:4] if args.quick else BLIND + GRID
    if args.only:
        grid = [c for c in grid if c in args.only]
        known = set(BLIND + GRID)
        missing = [c for c in args.only if c not in known]
        if missing:
            raise SystemExit(f"--only 含未知组合: {missing}")

    print(f"{'组合':<22}{'trades':>7}{'利润$':>10}{'PF':>7}{'win%':>7}{'品种正':>7}  逐年")
    print("-" * 100)
    results = []
    for q, h in grid:
        name, text = make_variant(q, h)
        trades = run_one(name, text)
        tot, pf, wr, pp, ayp, ys = summarize(name, trades)
        results.append((q, h, len(trades), tot, pf, wr, pp, ayp))
        print(f"q={q:.2f} h={h:<3}{'(盲参)' if (q, h) in BLIND else '':<6}"
              f"{len(trades):>7}{tot:>+10,.0f}{pf:>7.2f}{wr:>7.1f}{pp:>5}/{len(PAIRS)}  {ys}", flush=True)

    print("-" * 100)
    n_pos = sum(1 for r in results if r[3] > 0)
    all_pos_years = all(r[7] for r in results)
    print(f"A+B 判定: {n_pos}/{len(results)} 组合利润>0；所有组合逐年为正: {all_pos_years}")
    print("（通过标准: 网格全盈利/基本全盈利 + 无尖峰；逐年全正 = C 滚动等价证据）")


if __name__ == "__main__":
    main()
