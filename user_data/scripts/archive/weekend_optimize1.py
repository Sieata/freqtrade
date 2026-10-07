"""第一阶段优化 — 诊断驱动的两个假设

H1: 尾随太保守(激活1.2%/步长0.3%), profit 中位0.8% vs MFE 中位2.36%, 让利润跑
H2: EMA 离场纯负贡献(47笔全亏), 去掉或用 exit_profit_only
"""
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
    print(f"{label:<28s} {str(t):>6s} {str(w):>6s} {str(p):>12s} {str(d):>8s}")
    return t, w, p, d

print(f"{'Config':<28s} {'Trades':>6s} {'Win%':>6s} {'Profit$':>12s} {'DD%':>8s}")
print("-" * 64)

print("\n[H2] EMA exit on/off (trailing fixed baseline 0.003/0.012):")
row("H2 baseline (exit on)", "True", 0.003, 0.012)
row("H2 no EMA exit", "False", 0.003, 0.012)

print("\n[H1] step scan (offset fixed 0.012):")
for step in [0.003, 0.005, 0.008, 0.01, 0.015]:
    row(f"H1 step={step}", "True", step, 0.012)

print("\n[H1] offset scan (step fixed 0.003):")
for offset in [0.012, 0.02, 0.03, 0.04]:
    row(f"H1 offset={offset}", "True", 0.003, offset)

open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复。")
