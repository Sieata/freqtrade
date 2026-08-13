"""定版前验证 — 候选 offset 的全期表现 + 普适性(每品种盈利数)"""
import subprocess, re, json, zipfile

PAIRS_ARG = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT BNB/USDT:USDT ZEC/USDT:USDT HOME/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"
STRAT = "user_data/strategies/WeekendReverseV1.py"
orig = open(STRAT, "r", encoding="utf-8").read()

def run_backtest(offset, export):
    c = orig.replace("use_exit_signal = True", "use_exit_signal = False")
    c = c.replace("trailing_stop_positive_offset = 0.012", f"trailing_stop_positive_offset = {offset}")
    open(STRAT, "w", encoding="utf-8").write(c)
    cmd = f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange 20220101- {PAIRS_ARG}'
    if export:
        cmd += " --export trades --export-filename user_data/backtest_results/weekend_final.zip"
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300, cwd=CWD)
    return r.stdout + "\n" + r.stderr

def parse_summary(out):
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

print(f"{'Offset':>7s} {'Trades':>7s} {'Win%':>6s} {'Profit$':>12s} {'DD%':>7s}")
print("-" * 46)
for off in [0.012, 0.015, 0.02, 0.022, 0.025]:
    out = run_backtest(off, export=(off == 0.02))
    t, w, p, d = parse_summary(out)
    print(f"{off:>6.1%} {t:>7d} {w:>6.1f} {p:>12,.0f} {d:>7.1f}%")

# 用 offset=0.02 的导出看普适性
print("\n=== offset=2.0% 每品种普适性 ===")
try:
    with zipfile.ZipFile("user_data/backtest_results/backtest-result-2026-08-13_08-31-11.zip") as z:
        pass
    # 找最新的 final.zip
    import glob
    files = glob.glob("user_data/backtest_results/weekend_final*.zip")
    if not files:
        # fallback: 用 .last_result
        import os
        last = open("user_data/backtest_results/.last_result.json").read()
        import json as J
        zipname = J.loads(last)["latest_backtest"]
    else:
        zipname = files[-1]
    with zipfile.ZipFile(f"user_data/backtest_results/{zipname}") as z:
        data = json.loads(z.read(z.namelist()[0]))
    s = data["strategy"]["WeekendReverseV1"]
    rpp = sorted(s["results_per_pair"], key=lambda x: -x["profit_total_abs"])
    npos = 0
    for r in rpp:
        if r["key"] == "TOTAL": continue
        flag = "  <-- 亏损" if r["profit_total_abs"] < 0 else ""
        if r["profit_total_abs"] > 0: npos += 1
        print(f"  {r['key']:<20s} {r['trades']:>4d}笔  ${r['profit_total_abs']:>12,.0f}{flag}")
    print(f"\n盈利品种: {npos}/10")
except Exception as e:
    print(f"解析失败: {e}")

open(STRAT, "w", encoding="utf-8").write(orig)
print("\nDone. 策略已恢复。")
