"""定版回测 — 最终配置(8品种/去EMA/offset1.5%) 导出逐品种表，供 RESEARCH.md 使用"""
import subprocess, json, zipfile

PAIRS_8 = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT ZEC/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"

r = subprocess.run(
    f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange 20220101- {PAIRS_8} --export trades',
    shell=True, capture_output=True, text=True, timeout=300, cwd=CWD)

last = json.loads(open("user_data/backtest_results/.last_result.json").read())
with zipfile.ZipFile(f"user_data/backtest_results/{last['latest_backtest']}") as z:
    data = json.loads(z.read(z.namelist()[0]))
s = data["strategy"]["WeekendReverseV1"]
pairs = {r2["key"]: r2 for r2 in s["results_per_pair"] if r2["key"] != "TOTAL"}

print("=== 最终配置逐品种 ===")
for k, v in sorted(pairs.items(), key=lambda x: -x[1]["profit_total_abs"]):
    print(f"{k.split('/')[0]:<6s}  trades={v['trades']:>3d}  win={v['winrate']*100:>5.1f}%  profit=${v['profit_total_abs']:>12,.0f}")

tot = s
print(f"\nTOTAL  trades={tot['total_trades']}  win={tot['winrate']*100:.1f}%  profit=${tot['profit_total_abs']:,.0f}  DD={tot['max_drawdown_account']*100:.1f}%")
# 利润因子 / 夏普
print(f"profit_factor={tot.get('profit_factor'):.2f}  sharpe={tot.get('sharpe'):.2f}  sortino={tot.get('sortino'):.2f}  calmar={tot.get('calmar'):.2f}")
