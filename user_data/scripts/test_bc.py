"""B: 参数稳定性 + C: 逐年滚动"""
import subprocess, sys

PAIRS_ARG = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT BNB/USDT:USDT ZEC/USDT:USDT HOME/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
orig = open("user_data/strategies/WeekendReverseV1.py", "r", encoding="utf-8").read()

PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"

def backtest(timerange):
    r = subprocess.run(
        f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange {timerange} {PAIRS_ARG}',
        shell=True, capture_output=True, text=True, timeout=180, cwd=r"C:\Users\sieata\Documents\freqtrade"
    )
    for line in r.stdout.split("\n"):
        if "TOTAL" in line and "|" in line and "USDT" not in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 5:
                return f"{parts[1]}笔 {parts[3]}利润"
    if r.stderr:
        for line in r.stderr.split("\n"):
            if "ERROR" in line or "error" in line:
                return f"ERROR: {line[:80]}"
    return "NO RESULT"

def set_params(step, offset):
    c = orig.replace("trailing_stop_positive = 0.003", f"trailing_stop_positive = {step}")
    c = c.replace("trailing_stop_positive_offset = 0.012", f"trailing_stop_positive_offset = {offset}")
    open("user_data/strategies/WeekendReverseV1.py", "w", encoding="utf-8").write(c)

# ── B: 参数稳定性（测试集 2025-2026） ──
print("B. 参数稳定性 (测试集 2025-01~2026-08):")
for step in [0.002, 0.003, 0.004, 0.005, 0.006, 0.008]:
    set_params(step, step * 4)
    result = backtest("20250101-")
    print(f"  步长{step:.1%} (激活{step*4:.1%}): {result}")

# ── C: 逐年滚动 ──
print("\nC. 逐年滚动验证 (历史数据定参 → 下一年盲测):")
set_params(0.003, 0.012)
for train_end, test_year in [("20221231", "2023"), ("20231231", "2024"), ("20241231", "2025"), ("20251231", "2026")]:
    # 在训练集上找最优参数
    best_step, best_profit = 0.003, 0
    for step in [0.002, 0.003, 0.004, 0.005, 0.006]:
        set_params(step, step * 4)
        r = subprocess.run(
            f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange 20220101-{train_end} {PAIRS_ARG}',
            shell=True, capture_output=True, text=True, timeout=180, cwd=r"C:\Users\sieata\Documents\freqtrade"
        )
        for line in r.stdout.split("\n"):
            if "TOTAL" in line and "|" in line and "USDT" not in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 5:
                    try:
                        p = float(parts[3])
                        if p > best_profit:
                            best_profit = p
                            best_step = step
                    except: pass

    # 用最优参数跑下一年
    set_params(best_step, best_step * 4)
    test_start = f"{test_year}0101"
    test_end = f"{test_year}1231" if test_year != "2026" else ""
    timerange = f"{test_start}-{test_end}" if test_end else f"{test_start}-"
    result = backtest(timerange)
    print(f"  训练→{test_year}: 最优步长{best_step:.1%} → 测试: {result}")

# 恢复原版
open("user_data/strategies/WeekendReverseV1.py", "w", encoding="utf-8").write(orig)
print("\nDone.")
