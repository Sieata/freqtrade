"""Print a one-line summary of freqtrade backtest result zip(s).

用法: .venv/bin/python user_data/scripts/bt_summary.py <zip> [<zip> ...]
"""
import json
import sys
import zipfile


def summarize(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if not n.endswith(".json"):
                continue
            d = json.loads(z.read(n))
            if not (isinstance(d, dict) and "strategy" in d):
                continue
            for name, s in d["strategy"].items():
                dd = s.get("max_drawdown_account", s.get("max_drawdown_abs", 0))
                print(f"{zip_path.split('/')[-1][:44]:<44} {name:<20} "
                      f"trades={s.get('total_trades','?'):>4} "
                      f"profit={s.get('profit_total_abs', 0):>12,.0f}$ "
                      f"({s.get('profit_total', 0)*100:>7.2f}%) "
                      f"win%={s.get('winrate', 0)*100:>5.1f} "
                      f"PF={s.get('profit_factor', 0):>5.2f} "
                      f"dd={dd*100 if isinstance(dd, float) else dd:>5.1f}% "
                      f"[{d.get('timerange', '?')}]")
            return
    print(f"{zip_path}: no strategy results found")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        summarize(p)
