"""样本外验证 — 2022-2024 调参 → 2025-2026 盲测

回答: 去掉 EMA + 提高尾随激活线, 在样本外是否依然优于基线?
训练集最优的 offset 在测试集是否也成立(否则是过拟合)?
"""
import subprocess, re

PAIRS_ARG = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT BNB/USDT:USDT ZEC/USDT:USDT HOME/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"
STRAT = "user_data/strategies/WeekendReverseV1.py"
orig = open(STRAT, "r", encoding="utf-8").read()

def run_backtest(timerange):
    r = subprocess.run(
        f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange {timerange} {PAIRS_ARG}',
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

def set_params(use_exit, offset):
    c = orig.replace("use_exit_signal = True", f"use_exit_signal = {use_exit}")
    c = c.replace("trailing_stop_positive_offset = 0.012", f"trailing_stop_positive_offset = {offset}")
    open(STRAT, "w", encoding="utf-8").write(c)

TRAIN = "20220101-20241231"
TEST = "20250101-"

print(f"{'Config':<26s} {'Train$':>12s} {'Test$':>12s} {'TestDD':>7s}")
print("-" * 62)

configs = [
    ("base (exit on, 1.2%)", "True", 0.012),
    ("exit off, 1.2%", "False", 0.012),
    ("exit off, 1.5%", "False", 0.015),
    ("exit off, 2.0%", "False", 0.02),
    ("exit off, 2.2%", "False", 0.022),
    ("exit off, 2.5%", "False", 0.025),
]

for label, use_exit, offset in configs:
    set_params(use_exit, offset)
    t_train = run_backtest(TRAIN)
    t_test = run_backtest(TEST)
    tr = f"{t_train[2]:,.0f}" if t_train[2] is not None else "None"
    te = f"{t_test[2]:,.0f}" if t_test[2] is not None else "None"
    dd = f"{t_test[3]:.1f}%" if t_test[3] is not None else "None"
    print(f"{label:<26s} {tr:>12s} {te:>12s} {dd:>7s}")

open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复。")
