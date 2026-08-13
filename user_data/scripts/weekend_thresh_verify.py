"""freqtrade 真实回测验证 WeekendReverseV1 降阈值加笔数

扫描入场阈值 1% / 1.5% / 2% (止损固定 -10%),看笔数和利润。
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
        shell=True, capture_output=True, text=True, timeout=300, cwd=CWD
    )
    out = r.stdout + "\n" + r.stderr
    trades = win = profit = dd = None
    worst_pair = ""
    for line in out.split("\n"):
        if "TOTAL" in line and "|" in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 7 and parts[1].isdigit():
                trades = int(parts[1]); profit = float(parts[3])
                wt = parts[6].split()
                if wt: win = float(wt[-1])
        m = re.search(r"Absolute drawdown \(wallet balance\)\s*\|\s*[\d.]+ USDT \(([\d.]+)%\)", out)
        if m: dd = float(m.group(1))
        m2 = re.search(r"Worst Pair\s*\|\s*([\w/]+:[\w]+)\s+([-\d.]+)%", out)
        if m2: worst_pair = f"{m2.group(1)} {m2.group(2)}%"
    return trades, win, profit, dd, worst_pair

def set_thresh(pct_str):
    c = orig.replace('ret_1p"].shift(1) < -0.02', f'ret_1p"].shift(1) < -{pct_str}')
    c = c.replace('ret_1p"] >= -0.02', f'ret_1p"] >= -{pct_str}')
    assert c != orig, f"replace failed for {pct_str}"
    open(STRAT, "w", encoding="utf-8").write(c)

print(f"{'Thresh':>7s} {'Trades':>7s} {'Win%':>6s} {'Profit$':>10s} {'Drawdown':>9s} {'WorstPair':>20s}")
print("-" * 65)
for thresh in ["0.01", "0.015", "0.02", "0.025"]:
    set_thresh(thresh)
    t, w, p, d, wp = run_backtest()
    tag = "  <== baseline" if thresh == "0.02" else ""
    print(f"{float(thresh):>6.1%}  {t:>7d} {w:>6.1f} {p:>10.0f} {d:>8.1f}% {wp:>20s}{tag}")

open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复原样。")
