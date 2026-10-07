"""普适性扫描 — 每个 offset 的每品种利润 + 盈利品种数"""
import subprocess, re, json, zipfile, os

PAIRS_ARG = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT BNB/USDT:USDT ZEC/USDT:USDT HOME/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"
STRAT = "user_data/strategies/WeekendReverseV1.py"
orig = open(STRAT, "r", encoding="utf-8").read()

def run(offset):
    c = orig.replace("use_exit_signal = True", "use_exit_signal = False")
    c = c.replace("trailing_stop_positive_offset = 0.012", f"trailing_stop_positive_offset = {offset}")
    open(STRAT, "w", encoding="utf-8").write(c)
    r = subprocess.run(
        f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange 20220101- {PAIRS_ARG} --export trades',
        shell=True, capture_output=True, text=True, timeout=300, cwd=CWD)
    # 读 .last_result.json 拿最新 zip
    last = json.loads(open("user_data/backtest_results/.last_result.json").read())
    zipname = last["latest_backtest"]
    with zipfile.ZipFile(f"user_data/backtest_results/{zipname}") as z:
        data = json.loads(z.read(z.namelist()[0]))
    s = data["strategy"]["WeekendReverseV1"]
    rpp = s["results_per_pair"]
    pairs = {r["key"]: r["profit_total_abs"] for r in rpp if r["key"] != "TOTAL"}
    npos = sum(1 for v in pairs.values() if v > 0)
    total = s["profit_total_abs"]
    dd = s["max_drawdown_account"] * 100
    return total, dd, npos, pairs

print(f"{'Offset':>7s} {'Profit$':>12s} {'DD%':>7s} {'PosPairs':>8s}   {'亏损品种':<30s}")
print("-" * 72)
for off in [0.012, 0.015, 0.02, 0.022, 0.025]:
    total, dd, npos, pairs = run(off)
    losers = [f"{k.split('/')[0]}:{v:,.0f}" for k, v in pairs.items() if v < 0]
    loser_str = ", ".join(losers) if losers else "无"
    print(f"{off:>6.1%} {total:>12,.0f} {dd:>7.1f} {npos:>6d}/10   {loser_str:<30s}")

open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复。")
