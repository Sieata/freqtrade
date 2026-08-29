"""OIFlushV2 A/B 过拟合检测网格（TEST，TOP10 池，独立口径 $1,000/笔）。

判据（预注册，RESEARCH 12.4）：全组合利润>0 且 **全组合 2022 ≥ 0**（复活主张须在网格上成立）
且无尖峰。用法: .venv/Scripts/python user_data/scripts/of_batch.py [--only "q:h,..."]
"""
import json
import os
import re
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
STRAT = ROOT / "user_data" / "strategies" / "OIFlushV2.py"
BT_DIR = ROOT / "user_data" / "backtest_results"
TMP = Path("/tmp/of_batch")
SRC = STRAT.read_text()

PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT", "SOL/USDT:USDT",
         "TRX/USDT:USDT", "ZEC/USDT:USDT", "DOGE/USDT:USDT", "XMR/USDT:USDT", "HYPE/USDT:USDT"]
TR_TEST = "20220101-20240828"
STAKE = 1000
GRID = [(0.05, 48), (0.05, 72), (0.05, 96), (0.08, 48), (0.08, 72), (0.08, 96)]


def make_variant(q, h):
    text = SRC
    text = re.sub(r"oi_quantile = [0-9.]+", f"oi_quantile = {q}", text)
    text = re.sub(r"hold_hours = [0-9]+", f"hold_hours = {h}", text)
    name = f"OIFlushV2_q{int(q * 100):02d}_h{h}"
    text = text.replace("class OIFlushV2(IStrategy):", f"class {name}(IStrategy):")
    return name, text


def run_one(name, text):
    TMP.mkdir(parents=True, exist_ok=True)
    f = TMP / f"{name}.py"
    f.write_text(text)
    before = set(BT_DIR.glob("backtest-result-*.zip"))
    env = os.environ.copy()
    env.setdefault("https_proxy", "http://127.0.0.1:7897")
    env.setdefault("http_proxy", "http://127.0.0.1:7897")
    proc = subprocess.run(
        [sys.executable, "-m", "freqtrade", "backtesting",
         "--config", str(ROOT / "user_data" / "config_perpetual.json"),
         "--strategy-path", str(TMP), "--strategy", name,
         "--timerange", TR_TEST, "--pairs", *PAIRS,
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
                return d["strategy"][name].get("trades", [])
    raise SystemExit("解析失败")


def summarize(trades):
    cell = defaultdict(float)
    wins = defaultdict(int)
    for t in trades:
        key = (t["pair"].split("/")[0], t["close_date"][:4])
        cell[key] += t["profit_ratio"] * STAKE
        wins[key] += t["profit_ratio"] > 0
    years = sorted({k[1] for k in cell})
    ytot = {y: sum(cell.get((p, y), 0.0) for p in {k[0] for k in cell}) for y in years}
    pair_tot = defaultdict(float)
    for (p, y), v in cell.items():
        pair_tot[p] += v
    pf_n = sum(t["profit_ratio"] * STAKE for t in trades if t["profit_ratio"] > 0)
    pf_d = -sum(t["profit_ratio"] * STAKE for t in trades if t["profit_ratio"] <= 0)
    pf = pf_n / pf_d if pf_d else float("inf")
    ys = " ".join(f"{y}:{ytot[y]:+,.0f}" for y in years)
    return sum(pair_tot.values()), pf, ys, ytot, len(pair_tot), sum(1 for v in pair_tot.values() if v > 0)


def main():
    only = None
    if len(sys.argv) > 2 and sys.argv[1] == "--only":
        only = [tuple(float(a) if "." in a else int(a) for a in c.split(":")) for c in sys.argv[2].split(",")]
    grid = [g for g in GRID if only is None or (g[0], g[1]) in only]
    print(f"{'组合':<16}{'trades':>7}{'利润$':>10}{'PF':>7}  逐年（判据: 2022 ≥ 0）")
    print("-" * 100)
    results = []
    for q, h in grid:
        name, text = make_variant(q, h)
        trades = run_one(name, text)
        tot, pf, ys, ytot, npair, npos = summarize(trades)
        results.append((q, h, tot, pf, ys, ytot, npair, npos))
        print(f"q={q:.2f} h={h:<3}{tot:>+10,.0f}{pf:>7.2f}  {ys}  品种正 {npos}/{npair}", flush=True)
    print("-" * 100)
    all_pos = all(r[2] > 0 for r in results)
    y2022 = all(r[5].get("2022", 0) >= 0 for r in results)
    print(f"A/B 判定: {sum(1 for r in results if r[2] > 0)}/{len(results)} 组合利润>0；"
          f"全组合 2022≥0: {y2022}")


if __name__ == "__main__":
    main()
