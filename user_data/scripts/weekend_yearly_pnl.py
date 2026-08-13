"""逐年 PnL 对比 — 基线(10品种/EMA/off1.2%) vs 优化后(8品种/去EMA/off1.5%)"""
import subprocess, json, zipfile

PAIRS_10 = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT BNB/USDT:USDT ZEC/USDT:USDT HOME/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PAIRS_8 = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT ZEC/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"
STRAT = "user_data/strategies/WeekendReverseV1.py"
orig = open(STRAT, "r", encoding="utf-8").read()

# 基线字符串: 回退到旧参数
base = orig.replace("use_exit_signal = False", "use_exit_signal = True").replace(
    "trailing_stop_positive_offset = 0.015", "trailing_stop_positive_offset = 0.012")

def run(strat_text, pairs_arg, timerange):
    open(STRAT, "w", encoding="utf-8").write(strat_text)
    r = subprocess.run(
        f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange {timerange} {pairs_arg} --export trades',
        shell=True, capture_output=True, text=True, timeout=300, cwd=CWD)
    last = json.loads(open("user_data/backtest_results/.last_result.json").read())
    with zipfile.ZipFile(f"user_data/backtest_results/{last['latest_backtest']}") as z:
        data = json.loads(z.read(z.namelist()[0]))
    s = data["strategy"]["WeekendReverseV1"]
    return s["profit_total_abs"], s["max_drawdown_account"]*100, s["winrate"]*100, s["total_trades"]

years = [
    ("2022", "20220101-20221231"),
    ("2023", "20230101-20231231"),
    ("2024", "20240101-20241231"),
    ("2025", "20250101-20251231"),
    ("2026", "20260101-"),
]

print(f"{'年份':<6s} | {'基线 利润':>14s} {'DD%':>6s} {'胜率':>6s} {'笔数':>5s} | {'优化 利润':>14s} {'DD%':>6s} {'胜率':>6s} {'笔数':>5s} | {'Δ利润':>14s}")
print("-" * 100)
tot_b = tot_o = 0.0
for y, tr in years:
    bp, bd, bw, bt = run(base, PAIRS_10, tr)
    op, od, ow, ot = run(orig, PAIRS_8, tr)
    tot_b += bp; tot_o += op
    print(f"{y:<6s} | {bp:>14,.0f} {bd:>5.1f}% {bw:>5.1f}% {bt:>5d} | {op:>14,.0f} {od:>5.1f}% {ow:>5.1f}% {ot:>5d} | {op-bp:>+14,.0f}")
print("-" * 100)
print(f"{'合计':<6s} | {tot_b:>14,.0f} {'':>19s} | {tot_o:>14,.0f} {'':>19s} | {tot_o-tot_b:>+14,.0f}")

open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复为优化后状态。")
