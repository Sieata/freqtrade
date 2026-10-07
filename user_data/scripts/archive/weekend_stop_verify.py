"""freqtrade 真实回测验证 WeekendReverseV1 收紧止损

扫描 stoploss -3% ~ -10%,解析 笔数/胜率/总利润/回撤。
真实 freqtrade 处理 max_open_trades=1(全局)、滑点/跳空、下一根开盘成交。
"""
import subprocess, re, sys

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
    for line in out.split("\n"):
        if "TOTAL" in line and "|" in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 7 and parts[1].isdigit():
                trades = int(parts[1])
                profit = float(parts[3])
                win_tok = parts[6].split()
                if win_tok:
                    win = float(win_tok[-1])
        m = re.search(r"Absolute drawdown \(wallet balance\)\s*\|\s*[\d.]+ USDT \(([\d.]+)%\)", out)
        if m:
            dd = float(m.group(1))
    return trades, win, profit, dd

def set_stop(pct):
    c = orig.replace("stoploss = -0.10", f"stoploss = -{pct}")
    open(STRAT, "w", encoding="utf-8").write(c)

print(f"{'Stop':>6s} {'Trades':>7s} {'Win%':>6s} {'Profit$':>10s} {'Drawdown':>9s}")
print("-" * 46)
for stop in [0.03, 0.04, 0.05, 0.06, 0.08, 0.10]:
    set_stop(stop)
    t, w, p, d = run_backtest()
    tag = "  <== baseline" if stop == 0.10 else ""
    if t is None:
        print(f"{stop:>5.0%}  NO RESULT")
    else:
        print(f"{stop:>5.0%}  {t:>7d} {w:>6.1f} {p:>10.0f} {d:>8.1f}%{tag}")

# 恢复
open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复原样。")
