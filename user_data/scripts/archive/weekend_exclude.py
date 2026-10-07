"""验证排除结构性弱品种 BNB+HOME — 同 BigMoveV1 排除 LINK 的模式"""
import subprocess, re, json, zipfile

# 8 品种(去掉 BNB、HOME)
PAIRS_8 = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT ZEC/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"
STRAT = "user_data/strategies/WeekendReverseV1.py"
orig = open(STRAT, "r", encoding="utf-8").read()

def run(offset, pairs_arg, export=False):
    c = orig.replace("use_exit_signal = True", "use_exit_signal = False")
    c = c.replace("trailing_stop_positive_offset = 0.012", f"trailing_stop_positive_offset = {offset}")
    open(STRAT, "w", encoding="utf-8").write(c)
    cmd = f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange 20220101- {pairs_arg}'
    if export:
        cmd += " --export trades"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd=CWD)
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
    pairs = {}
    if export:
        last = json.loads(open("user_data/backtest_results/.last_result.json").read())
        with zipfile.ZipFile(f"user_data/backtest_results/{last['latest_backtest']}") as z:
            data = json.loads(z.read(z.namelist()[0]))
        s = data["strategy"]["WeekendReverseV1"]
        pairs = {r["key"]: r["profit_total_abs"] for r in s["results_per_pair"] if r["key"] != "TOTAL"}
    return trades, win, profit, dd, pairs

print(f"{'Config':<30s} {'Trades':>6s} {'Win%':>6s} {'Profit$':>12s} {'DD%':>7s} {'Pos':>5s}")
print("-" * 74)

for label, off, pairs_arg, export in [
    ("8品种 off=1.2%", 0.012, PAIRS_8, False),
    ("8品种 off=2.0%", 0.02, PAIRS_8, True),
    ("8品种 off=2.2%", 0.022, PAIRS_8, False),
]:
    t, w, p, d, pp = run(off, pairs_arg, export)
    npos = sum(1 for v in pp.values() if v > 0) if pp else None
    pos_str = f"{npos}/8" if npos is not None else "?"
    print(f"{label:<30s} {str(t):>6s} {str(w):>6s} {p:>12,.0f} {d:>7.1f} {pos_str:>5s}")
    if pp:
        for k, v in sorted(pp.items(), key=lambda x: -x[1]):
            flag = "  <-- 亏" if v < 0 else ""
            print(f"    {k:<20s} ${v:>12,.0f}{flag}")

open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复。")
