"""freqtrade 真实回测验证 WeekendReverseV1 提高并行持仓数

max_open_trades=1 是全局单仓,10 品种信号排队。提高到 3/5/8 看笔数/利润/回撤。
"""
import subprocess, re

PAIRS_ARG = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT BNB/USDT:USDT ZEC/USDT:USDT HOME/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"
CONFIG = "user_data/config_perpetual.json"

orig_cfg = open(CONFIG, "r", encoding="utf-8").read()

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
                trades = int(parts[1]); profit = float(parts[3])
                wt = parts[6].split()
                if wt: win = float(wt[-1])
        m = re.search(r"Absolute drawdown \(wallet balance\)\s*\|\s*[\d.]+ USDT \(([\d.]+)%\)", out)
        if m: dd = float(m.group(1))
    return trades, win, profit, dd

def set_max(n):
    c = re.sub(r'"max_open_trades":\s*\d+', f'"max_open_trades": {n}', orig_cfg)
    if n != 1:
        assert c != orig_cfg, f"replace failed for {n}"
    open(CONFIG, "w", encoding="utf-8").write(c)

print(f"{'MaxOpen':>7s} {'Trades':>7s} {'Win%':>6s} {'Profit$':>10s} {'Drawdown':>9s}")
print("-" * 46)
for n in [1, 3, 5, 8, 12]:
    set_max(n)
    t, w, p, d = run_backtest()
    tag = "  <== baseline" if n == 1 else ""
    print(f"{n:>7d} {t:>7d} {w:>6.1f} {p:>10.0f} {d:>8.1f}%{tag}")

open(CONFIG, "w", encoding="utf-8").write(orig_cfg)
print("\nDone. config 已恢复。")
