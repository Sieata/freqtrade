"""8品种(去BNB/HOME) 各 offset 全期 + 普适性 — 定版决策"""
import subprocess, re, json, zipfile

PAIRS_8 = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT ZEC/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"
STRAT = "user_data/strategies/WeekendReverseV1.py"
orig = open(STRAT, "r", encoding="utf-8").read()

def run(offset):
    c = orig.replace("use_exit_signal = True", "use_exit_signal = False")
    c = c.replace("trailing_stop_positive_offset = 0.012", f"trailing_stop_positive_offset = {offset}")
    open(STRAT, "w", encoding="utf-8").write(c)
    r = subprocess.run(
        f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange 20220101- {PAIRS_8} --export trades',
        shell=True, capture_output=True, text=True, timeout=300, cwd=CWD)
    last = json.loads(open("user_data/backtest_results/.last_result.json").read())
    with zipfile.ZipFile(f"user_data/backtest_results/{last['latest_backtest']}") as z:
        data = json.loads(z.read(z.namelist()[0]))
    s = data["strategy"]["WeekendReverseV1"]
    pairs = {r["key"]: r["profit_total_abs"] for r in s["results_per_pair"] if r["key"] != "TOTAL"}
    npos = sum(1 for v in pairs.values() if v > 0)
    return s["profit_total_abs"], s["max_drawdown_account"]*100, npos, s["total_trades"], s["winrate"]*100, pairs

print(f"{'Offset':>7s} {'Trades':>6s} {'Win%':>6s} {'Profit$':>12s} {'DD%':>7s} {'Pos':>5s}  {'亏损品种'}")
print("-" * 80)
for off in [0.012, 0.015, 0.02, 0.022, 0.025]:
    p, dd, npos, t, w, pairs = run(off)
    losers = ", ".join(f"{k.split('/')[0]}:{v:,.0f}" for k, v in pairs.items() if v < 0) or "无"
    print(f"{off:>6.1%} {t:>6d} {w:>6.1f} {p:>12,.0f} {dd:>7.1f} {npos:>4d}/8  {losers}")

open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复。")
