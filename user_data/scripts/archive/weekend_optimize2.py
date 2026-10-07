"""第二阶段 — 组合 H2(去EMA) + 细扫尾随激活线, 及激活线下的步长交互"""
import subprocess, re

PAIRS_ARG = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT BNB/USDT:USDT ZEC/USDT:USDT HOME/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"
STRAT = "user_data/strategies/WeekendReverseV1.py"
orig = open(STRAT, "r", encoding="utf-8").read()

def run_backtest():
    r = subprocess.run(
        f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange 20220101- {PAIRS_ARG}',
        shell=True, capture_output=True, text=True, timeout=300, cwd=CWD)
    out = r.stdout + "\n" + r.stderr
    trades = win = profit = dd = None
    for line in out.split("\n"):
        if "TOTAL" in line and "|" in line:
            p = [x.strip() for x in line.split("|") if x.strip()]
            if len(p) >= 7 and p[1].isdigit():
                trades = int(p[1]); profit = float(p[3])
                wt = p[6].split()
                if wt: win = float(wt[-1])
        m = re.search(r"Absolute drawdown \(wallet balance\)\s*\|\s*[\d.]+ USDT \(([\d.]+)%\)", out)
        if m: dd = float(m.group(1))
    return trades, win, profit, dd

def set_params(use_exit, step, offset):
    c = orig
    c = c.replace("use_exit_signal = True", f"use_exit_signal = {use_exit}")
    c = c.replace("trailing_stop_positive = 0.003", f"trailing_stop_positive = {step}")
    c = c.replace("trailing_stop_positive_offset = 0.012", f"trailing_stop_positive_offset = {offset}")
    open(STRAT, "w", encoding="utf-8").write(c)

def row(label, use_exit, step, offset):
    set_params(use_exit, step, offset)
    t, w, p, d = run_backtest()
    print(f"{label:<34s} {str(t):>6s} {str(w):>6s} {str(p):>12s} {str(d):>8s}")
    return t, w, p, d

print(f"{'Config':<34s} {'Trades':>6s} {'Win%':>6s} {'Profit$':>12s} {'DD%':>8s}")
print("-" * 70)

print("\n[组合] use_exit=False + offset 细扫 (step 固定 0.003):")
row("base(exit on, off=1.2%)", "True", 0.003, 0.012)
row("off=1.2% (H2 only)", "False", 0.003, 0.012)
for offset in [0.015, 0.018, 0.02, 0.022, 0.025, 0.03]:
    row(f"off={offset}", "False", 0.003, offset)

print("\n[步长交互] off=0.02 下扫 step:")
for step in [0.003, 0.004, 0.005, 0.006]:
    row(f"off=0.02 step={step}", "False", step, 0.02)

open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复。")
