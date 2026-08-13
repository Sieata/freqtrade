"""WeekendReverseV1 10大品种 最近3年 详细简报"""
import subprocess, json, zipfile
from collections import defaultdict

PAIRS_10 = "--pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT XRP/USDT:USDT BNB/USDT:USDT ZEC/USDT:USDT HOME/USDT:USDT BANK/USDT:USDT CYS/USDT:USDT HYPE/USDT:USDT"
PYTHON = r"C:\Users\sieata\Documents\freqtrade\.venv\Scripts\python.exe"
CWD = r"C:\Users\sieata\Documents\freqtrade"

r = subprocess.run(
    f'{PYTHON} -m freqtrade backtesting --config user_data/config_perpetual.json --strategy WeekendReverseV1 --timerange 20230813- {PAIRS_10} --export trades',
    shell=True, capture_output=True, text=True, timeout=300, cwd=CWD)

last = json.loads(open("user_data/backtest_results/.last_result.json").read())
with zipfile.ZipFile(f"user_data/backtest_results/{last['latest_backtest']}") as z:
    data = json.loads(z.read(z.namelist()[0]))
s = data["strategy"]["WeekendReverseV1"]

print("=== 总览 ===")
print(f"timerange: {s['timerange']}")
print(f"trades={s['total_trades']}  winrate={s['winrate']*100:.1f}%")
print(f"profit_abs=${s['profit_total_abs']:,.0f}  DD={s['max_drawdown_account']*100:.1f}%")
print(f"cagr={s['cagr']*100:.1f}%  sharpe={s['sharpe']:.2f}  sortino={s['sortino']:.2f}")
print(f"profit_factor={s['profit_factor']:.2f}  calmar={s['calmar']:.2f}")
print(f"expectancy=${s.get('expectancy', 0):,.2f}  expectancy_ratio={s.get('expectancy_ratio', 0):.2f}")
print(f"holding_avg={s.get('holding_avg_s', 0)/3600:.1f}h  wins={s['wins']} losses={s['losses']} draws={s['draws']}")

print("\n=== 逐品种 ===")
pairs = sorted([r2 for r2 in s['results_per_pair'] if r2['key'] != 'TOTAL'], key=lambda x: -x['profit_total_abs'])
print(f"{'pair':<16s} {'trades':>6s} {'win%':>6s} {'profit':>12s} {'dd%':>7s}")
for p in pairs:
    print(f"{p['key'].split('/')[0]:<16s} {p['trades']:>6d} {p['winrate']*100:>5.1f}% {p['profit_total_abs']:>12,.0f} {p['max_drawdown_account']*100:>6.1f}%")

print("\n=== 退出原因 ===")
for item in s['exit_reason_summary']:
    if item['key'] == 'TOTAL':
        continue
    print(f"{item['key']:<30s} trades={item['trades']:>4d}  win%={item['winrate']*100:>5.1f}%  profit=${item['profit_total_abs']:>12,.0f}")

print("\n=== 年度分布 ===")
trades = s['trades']
by_year = defaultdict(lambda: [0, 0.0, 0])
for t in trades:
    y = t['close_date'][:4]
    by_year[y][0] += 1
    by_year[y][1] += t['profit_abs']
    if t['profit_abs'] > 0:
        by_year[y][2] += 1
for y in sorted(by_year):
    n, prof, w = by_year[y]
    print(f"{y}: trades={n:>4d}  win%={w/n*100:>5.1f}%  profit=${prof:>12,.0f}")

print("\n=== 单笔分布 ===")
profs = [t['profit_abs'] for t in trades]
profs.sort()
print(f"n={len(profs)}  min=${profs[0]:,.0f}  max=${profs[-1]:,.0f}")
print(f"median=${profs[len(profs)//2]:,.0f}  mean=${sum(profs)/len(profs):,.0f}")
# 最大单笔亏损（止损触发）
losers = [p for p in profs if p < 0]
print(f"losers={len(losers)}  biggest_loss=${min(losers):,.0f}  avg_loss=${sum(losers)/len(losers):,.0f}")
winners = [p for p in profs if p > 0]
print(f"winners={len(winners)}  biggest_win=${max(winners):,.0f}  avg_win=${sum(winners)/len(winners):,.0f}")

# 最大单笔亏损的明细
worst = min(trades, key=lambda t: t['profit_abs'])
print(f"\n最差单笔: {worst['pair']} {worst['open_date'][:10]}->{worst['close_date'][:10]} ${worst['profit_abs']:,.0f} ({worst['exit_reason']})")
best = max(trades, key=lambda t: t['profit_abs'])
print(f"最佳单笔: {best['pair']} {best['open_date'][:10]}->{best['close_date'][:10]} ${best['profit_abs']:,.0f} ({best['exit_reason']})")
