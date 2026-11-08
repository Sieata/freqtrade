"""paper8 池摩擦敏感度（TEST-only，独立口径 $1000/笔）。

回答 26.20 遗留问题：forward-test 实际使用的 paper8 池（含 BANK/CYS 两个小币）
在真实费率下能否像 TOP10 一样存活？26.11 的费用阶梯只在 TOP10 上做过。

方法：research_lib.run_backtest 对 paper8 显式品种跑 TEST 段，费率阶梯
{基线0.05%, 0.10, 0.20, 0.30, 0.40, 0.50}（单边，往返=2×），每档统计：
总利润 / 保留% / PF / 盈利品种数；并拆分 core6（非 BANK/CYS）vs small2（BANK+CYS）。

仅 TEST，不消耗 VAL、不记台账。纯分析工具，无源码/配置变更。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_lib as R

STRATEGY = "WeekendReverseV2"
CONFIG = R.ROOT / "user_data" / "config_perpetual.json"
TIMERANGE = "20220101-20240828"  # TEST 段
PAPER8 = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
    "ZEC/USDT:USDT", "BANK/USDT:USDT", "CYS/USDT:USDT", "HYPE/USDT:USDT",
]
SMALL = {"BANK/USDT:USDT", "CYS/USDT:USDT"}
FEES = [0.0005, 0.0010, 0.0020, 0.0030, 0.0040, 0.0050]


def summarize(zip_path, label):
    stats, trades = R.read_result(zip_path)
    df = R.trades_frame(trades)
    if df is None or len(df) == 0:
        return None
    df = df.copy()
    stake = R.STAKE
    # 独立口径：每笔 profit_ratio * $1000
    if "profit_ratio" in df.columns:
        df["pnl"] = df["profit_ratio"].astype(float) * stake
    else:
        df["pnl"] = df["profit_abs"].astype(float)
    total = df["pnl"].sum()
    n = len(df)
    wins = df[df["pnl"] > 0]["pnl"].sum()
    losses = -df[df["pnl"] < 0]["pnl"].sum()
    pf = wins / losses if losses > 0 else float("inf")
    # 品种
    pair_col = "pair" if "pair" in df.columns else None
    if pair_col:
        per = df.groupby(pair_col)["pnl"].sum()
        n_pos = int((per > 0).sum())
        small_pnl = df[df[pair_col].isin(SMALL)]["pnl"].sum()
        core_pnl = df[~df[pair_col].isin(SMALL)]["pnl"].sum()
        small_n = int(df[pair_col].isin(SMALL).sum())
        core_n = n - small_n
    else:
        n_pos = -1; small_pnl = core_pnl = small_n = core_n = 0
    return dict(label=label, n=n, total=total, pf=pf, n_pos=n_pos,
                small_pnl=small_pnl, core_pnl=core_pnl, small_n=small_n, core_n=core_n)


def main():
    R.enable_utf8_console()
    base = None
    rows = []
    for fee in FEES:
        print(f"\n=== fee {fee*100:.2f}%/边 ===", flush=True)
        zp = R.run_backtest(STRATEGY, CONFIG, TIMERANGE, PAPER8, fee=fee, echo=True)
        s = summarize(zp, f"{fee*100:.2f}%")
        if s is None:
            print("  (无交易)", flush=True)
            continue
        if base is None:
            base = s["total"]
        rows.append(s)
    print("\n\n===== paper8 TEST 费用阶梯 =====")
    print(f"{'边长':>6} {'往返':>6} {'笔数':>5} {'利润$':>10} {'保留%':>7} {'PF':>6} {'盈利品种':>8} {'core6利润':>10} {'small2利润':>10}")
    for s in rows:
        retain = s["total"] / base * 100 if base else 0
        print(f"{s['label']:>6} {float(s['label'].rstrip('%'))*2:>5.2f}% {s['n']:>5} "
              f"{s['total']:>10.0f} {retain:>6.1f}% {s['pf']:>6.2f} {s['n_pos']:>6}/8 "
              f"{s['core_pnl']:>10.0f} {s['small_pnl']:>10.0f}")


if __name__ == "__main__":
    main()
