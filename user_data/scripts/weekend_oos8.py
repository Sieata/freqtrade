"""8品种(去BNB/HOME)样本外+逐年验证 — 排除品种是否稳健?"""
import subprocess, re, json, zipfile

PAIRS_8 = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT ZEC/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"
STRAT = "user_data/strategies/WeekendReverseV1.py"
orig = open(STRAT, "r", encoding="utf-8").read()

def run(timerange, offset):
    c = orig.replace("use_exit_signal = True", "use_exit_signal = False")
    c = c.replace("trailing_stop_positive_offset = 0.012", f"trailing_stop_positive_offset = {offset}")
    open(STRAT, "w", encoding="utf-8").write(c)
    r = subprocess.run(
        f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange {timerange} {PAIRS_8}',
        shell=True, capture_output=True, text=True, timeout=300, cwd=CWD)
    out = r.stdout + "\n" + r.stderr
    profit = dd = None
    for line in out.split("\n"):
        if "TOTAL" in line and "|" in line:
            p = [x.strip() for x in line.split("|") if x.strip()]
            if len(p) >= 7 and p[1].isdigit():
                profit = float(p[3])
        m = re.search(r"Absolute drawdown \(wallet balance\)\s*\|\s*[\d.]+ USDT \(([\d.]+)%\)", out)
        if m: dd = float(m.group(1))
    return profit, dd

# 1. 时间分割: 训练 2022-2024, 测试 2025-2026
print("=== 时间分割 (8品种, 去EMA) ===")
print(f"{'Offset':>7s} {'Train$':>12s} {'Test$':>12s} {'TestDD':>7s}")
print("-" * 44)
for off in [0.012, 0.015, 0.02, 0.022, 0.025]:
    tr, _ = run("20220101-20241231", off)
    te, dd = run("20250101-", off)
    print(f"{off:>6.1%} {tr:>12,.0f} {te:>12,.0f} {dd:>7.1f}%")

# 2. 逐年滚动
print("\n=== 逐年滚动 (8品种, 去EMA) ===")
print(f"{'Year':<6s} {'BestOffset':>10s} {'Test$':>12s} {'Verdict':>8s}")
print("-" * 44)
OFFSETS = [0.012, 0.015, 0.02, 0.022, 0.025]
for year in [2023, 2024, 2025, 2026]:
    train_end = f"{year-1}1231"
    test_range = f"{year}0101-{year}1231" if year != 2026 else f"{year}0101-"
    best_off, best_p = None, float("-inf")
    for off in OFFSETS:
        p, _ = run(f"20220101-{train_end}", off)
        if p is not None and p > best_p:
            best_p, best_off = p, off
    te, _ = run(test_range, best_off)
    verdict = "PASS" if (te is not None and te > 0) else "FAIL"
    print(f"{year:<6d} {best_off:>9.1%} {te:>12,.0f} {verdict:>8s}")

open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复。")
