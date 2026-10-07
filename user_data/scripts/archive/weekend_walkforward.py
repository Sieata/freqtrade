"""逐年滚动验证 — 最严格的过拟合检测

每年: 用截止前一年底的数据定 offset, 下一年盲测。
通过标准: 每年盲测都盈利, 且选出的 offset 稳定(不跳来跳去)。
"""
import subprocess, re

PAIRS_ARG = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT BNB/USDT:USDT ZEC/USDT:USDT HOME/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"
STRAT = "user_data/strategies/WeekendReverseV1.py"
orig = open(STRAT, "r", encoding="utf-8").read()

OFFSETS = [0.012, 0.015, 0.02, 0.022, 0.025]

def run_backtest(timerange):
    r = subprocess.run(
        f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange {timerange} {PAIRS_ARG}',
        shell=True, capture_output=True, text=True, timeout=300, cwd=CWD)
    out = r.stdout + "\n" + r.stderr
    profit = None
    for line in out.split("\n"):
        if "TOTAL" in line and "|" in line:
            p = [x.strip() for x in line.split("|") if x.strip()]
            if len(p) >= 7 and p[1].isdigit():
                profit = float(p[3])
    return profit

def set_offset(offset):
    c = orig.replace("use_exit_signal = True", "use_exit_signal = False")
    c = c.replace("trailing_stop_positive_offset = 0.012", f"trailing_stop_positive_offset = {offset}")
    open(STRAT, "w", encoding="utf-8").write(c)

# 逐年滚动: train到 Y-1 年底, 在 train 上选最优 offset, 测 Y 年
print(f"{'Year':<6s} {'BestOffset':>10s} {'Test$':>12s} {'Verdict':>8s}")
print("-" * 44)

for year in [2023, 2024, 2025, 2026]:
    train_end = f"{year-1}1231"
    test_range = f"{year}0101-{year}1231" if year != 2026 else f"{year}0101-"

    # 训练集扫 offset
    best_offset, best_p = None, float("-inf")
    for off in OFFSETS:
        set_offset(off)
        p = run_backtest(f"20220101-{train_end}")
        if p is not None and p > best_p:
            best_p, best_offset = p, off

    # 测试集用最优
    set_offset(best_offset)
    test_p = run_backtest(test_range)
    verdict = "PASS" if (test_p is not None and test_p > 0) else "FAIL"
    print(f"{year:<6d} {best_offset:>9.1%} {test_p:>12,.0f} {verdict:>8s}")

open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复。")
