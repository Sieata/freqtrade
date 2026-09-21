"""Print a one-line summary of freqtrade backtest result zip(s).

用法: .venv/bin/python user_data/scripts/bt_summary.py <zip> [<zip> ...]

回撤两个口径并列输出，勿混用（AGENTS.md「高频坑速记」）:
  ddW = max_relative_drawdown —— 钱包口径，文档/报告一律引用这个
  ddA = max_drawdown_account  —— 账户口径，数值明显偏小，仅作参考
两者在满仓复利下差异极大（同一次回测 32.1% vs 20.2%），只看 ddA 会低估风险。
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
                ddw = float(s.get("max_relative_drawdown", 0) or 0)
                dda = float(s.get("max_drawdown_account", 0) or 0)
                tspan = d.get("timerange") or (
                    f"{str(s.get('backtest_start', ''))[:10]}~"
                    f"{str(s.get('backtest_end', ''))[:10]}")
                print(f"{zip_path.split('/')[-1][:44]:<44} {name:<20} "
                      f"trades={s.get('total_trades','?'):>4} "
                      f"profit={s.get('profit_total_abs', 0):>12,.0f}$ "
                      f"({s.get('profit_total', 0)*100:>7.2f}%) "
                      f"win%={s.get('winrate', 0)*100:>5.1f} "
                      f"PF={s.get('profit_factor', 0):>5.2f} "
                      f"ddW={ddw*100:>5.1f}% ddA={dda*100:>5.1f}% "
                      f"[{tspan}]")
            return
    print(f"{zip_path}: no strategy results found")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        summarize(p)
