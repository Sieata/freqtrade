"""CrashBuyV1 A/B/C 过拟合检测"""
import subprocess
PAIRS = "BTC/USDT:USDT ETH/USDT:USDT BNB/USDT:USDT SOL/USDT:USDT DOGE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"
PAIRS_ARG = "--pairs " + PAIRS
orig = open("user_data/strategies/CrashBuyV1.py", "r", encoding="utf-8").read()

def backtest(timerange):
    r = subprocess.run(f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy CrashBuyV1 --timerange {timerange} {PAIRS_ARG}',
                       shell=True, capture_output=True, text=True, timeout=180, cwd=CWD)
    for line in r.stdout.split("\n"):
        if "TOTAL" in line and "|" in line and "USDT" not in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 5:
                return f"{parts[1]}笔 {parts[3]}利润"
    for line in r.stderr.split("\n"):
        if "ERROR" in line: return f"ERR:{line[:60]}"
    return "NO RESULT"

def set_trail(step, offset):
    c = orig
    c = c.replace("trailing_stop_positive = 0.02", f"trailing_stop_positive = {step}")
    c = c.replace("trailing_stop_positive_offset = 0.05", f"trailing_stop_positive_offset = {offset}")
    open("user_data/strategies/CrashBuyV1.py", "w", encoding="utf-8").write(c)

# A: 盲参 — 用 WeekendReverseV1 的紧尾随
print("A. 盲参测试 (WeekendReverseV1的紧尾随 0.3%/1.2%):")
set_trail(0.003, 0.012)
print(f"  {backtest('20220101-')}")

# B: 参数稳定性
print("\nB. 参数稳定性 (测试集 2025-2026):")
for step in [0.01, 0.02, 0.03, 0.04, 0.05]:
    set_trail(step, step * 2.5)
    print(f"  步长{step:.0%}: {backtest('20250101-')}")

# C: 逐年滚动
print("\nC. 逐年滚动:")
for train_end, test_year in [("20221231", "2023"), ("20231231", "2024"), ("20241231", "2025"), ("20251231", "2026")]:
    best_step, best_profit = 0.02, 0
    for step in [0.01, 0.02, 0.03, 0.04, 0.05]:
        set_trail(step, step * 2.5)
        r = subprocess.run(f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy CrashBuyV1 --timerange 20220101-{train_end} {PAIRS_ARG}',
                           shell=True, capture_output=True, text=True, timeout=180, cwd=CWD)
        for line in r.stdout.split("\n"):
            if "TOTAL" in line and "|" in line and "USDT" not in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 5:
                    try:
                        p = float(parts[3])
                        if p > best_profit: best_profit = p; best_step = step
                    except: pass
    set_trail(best_step, best_step * 2.5)
    ts = f"{test_year}0101-{test_year}1231" if test_year != "2026" else "20260101-"
    print(f"  训练→{test_year}: 最优{best_step:.0%} → {backtest(ts)}")

# 恢复
open("user_data/strategies/CrashBuyV1.py", "w", encoding="utf-8").write(orig)
print("\nDone.")
