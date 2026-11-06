"""Print a one-line summary of freqtrade backtest result zip(s).

用法: .venv/bin/python user_data/scripts/bt_summary.py <zip> [<zip> ...]

回撤两个口径并列输出，勿混用（AGENTS.md「高频坑速记」）:
  ddW = max_relative_drawdown —— 钱包口径，文档/报告一律引用这个
  ddA = max_drawdown_account  —— 账户口径，数值明显偏小，仅作参考
两者在满仓复利下差异极大（同一次回测 32.1% vs 20.2%），只看 ddA 会低估风险。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_lib import iter_results  # noqa: E402


def _num(v, default=0.0):
    return float(v) if v is not None else default


def summarize(zip_path):
    """打印 zip 内**全部**策略各一行。

    旧实现循环里直接 return，多策略 zip 只显示第一个——iter_results 的 docstring
    与单测都明确"bt_summary 需遍历全部策略"，消费端却把契约丢了。
    """
    shown = 0
    for name, s, _, meta in iter_results(zip_path):
        ddw = _num(s.get("max_relative_drawdown"))
        dda = _num(s.get("max_drawdown_account"))
        tspan = meta.get("timerange") or (
            f"{str(s.get('backtest_start', ''))[:10]}~"
            f"{str(s.get('backtest_end', ''))[:10]}")
        # 裸文件名（常见的 `*.zip` 通配结果）不能直接 split('/') 取末段
        print(f"{Path(zip_path).name[:44]:<44} {name:<20} "
              f"trades={s.get('total_trades', '?'):>4} "
              f"profit={_num(s.get('profit_total_abs')):>12,.0f}$ "
              f"({_num(s.get('profit_total'))*100:>7.2f}%) "
              f"win%={_num(s.get('winrate'))*100:>5.1f} "
              f"PF={_num(s.get('profit_factor')):>5.2f} "
              f"ddW={ddw*100:>5.1f}% ddA={dda*100:>5.1f}% "
              f"[{tspan}]")
        shown += 1
    if not shown:
        print(f"{zip_path}: no strategy results found")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or any(a in ("-h", "--help") for a in args):
        print(__doc__.strip())
        sys.exit(0)
    for p in args:
        summarize(p)
