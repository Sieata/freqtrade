"""H7 资金费套利事件研究（cash-and-carry：多现货 + 空永续，delta 中性收资金费）。

口径（预注册，RESEARCH 十三；严格只用 TEST 20220101-20240828）：
  触发: 最近一次 8h 结算费率 >= θ（扫描 0.03%/0.05%/0.1%；主形态 0.05% ≈ 55% APR）
  入场: 信号 4h 收盘，现货多 1x + 永续空 1x（delta 中性）
  退出: 持有 H 天（扫描 3/7/14，主 7）
  收益 = Σ(持有窗口内结算费率，空头正费率收/负费率付) + (b_entry − b_exit) − 摩擦
       其中 b = 永续收盘/现货收盘 − 1（做空时卖出溢价，基差收敛为正贡献）
       摩擦 0.3% = 双边 taker（现货 0.1% + 永续 0.05% ×2 腿×2 边，保守）
  事件不重叠（持有期内不再触发）；品种域 = TOP10 中有现货对的 8 币
  （HYPE 币安无现货对、XMR 现货无历史数据，均无法构建现货腿）。

用法: .venv/bin/python user_data/scripts/carry_phase1.py
"""
import numpy as np
import pandas as pd

SPOT = "user_data/data/binance"
FUT = "user_data/data/binance/futures"
PAIRS = ["BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "ZEC", "DOGE"]
FEE = 0.003
T_START, T_END = "2022-01-01", "2024-08-28"
THETAS = [0.0003, 0.0005, 0.001]   # 8h 费率触发阈值
HOLDS = {"3d": 18, "7d": 42, "14d": 84}   # 4h 根数


def load(pair):
    spot = pd.read_feather(f"{SPOT}/{pair}_USDT-4h.feather")[["date", "close"]].rename(
        columns={"close": "spot"}).set_index("date")
    fut = pd.read_feather(f"{FUT}/{pair}_USDT_USDT-4h-futures.feather")[["date", "close"]].rename(
        columns={"close": "perp"}).set_index("date")
    df = fut.join(spot, how="inner")
    df["b"] = df["perp"] / df["spot"] - 1
    f = pd.read_feather(f"{FUT}/{pair}_USDT_USDT-1h-funding_rate.feather")
    s = f[f["open"] != 0].set_index("date")["open"].rename("rate")
    s = s[~s.index.duplicated(keep="last")]
    df = df[(df.index >= T_START) & (df.index < T_END)]
    return df, s


def run(theta, hold, events, pair, df, fund):
    """资金费套利事件：信号收盘入场，持有 hold 根 4h。"""
    locked_until = -1
    rates = fund.values
    dates = fund.index
    for i in range(1, len(df) - hold):
        if i <= locked_until:
            continue
        t_close = df.index[i]
        prior = dates[dates <= t_close]
        if len(prior) == 0 or rates[dates.get_loc(prior[-1])] < theta:
            continue
        entry_t, exit_t = t_close, df.index[i + hold]
        f_sum = rates[(dates > entry_t) & (dates <= exit_t)].sum()
        ret = f_sum + (df["b"].iloc[i] - df["b"].iloc[i + hold]) - FEE
        events.append((pair, entry_t.year, ret, f_sum, df["b"].iloc[i] - df["b"].iloc[i + hold]))
        locked_until = i + hold


def agg(events, label):
    if not events:
        print(f"{label}: 无事件")
        return
    df = pd.DataFrame(events, columns=["pair", "year", "ret", "fund", "basis"])
    yr = df.groupby("year")["ret"].agg(["mean", "count", "sum"])
    ys = ", ".join(f"{y}:{r['sum'] * 100:+.2f}%(n={r['count']})" for y, r in yr.iterrows())
    per_pair = df.groupby("pair")["ret"].agg(["mean", "count"])
    pos = int((per_pair["mean"] > 0).sum())
    r = df["ret"].values
    hold_d = {"3d": 3, "7d": 7, "14d": 14}[label.split("h=")[1].split("(")[0].strip()]
    apr = r.mean() / hold_d * 365 * 100
    print(f"{label}: n={len(r)} win={100 * (r > 0).mean():.1f}% mean={r.mean() * 100:+.3f}% "
          f"median={np.median(r) * 100:+.3f}% 最差={r.min() * 100:+.2f}% "
          f"品种净正={pos}/{len(per_pair)} 在场年化≈{apr:+.1f}%")
    print(f"    逐年合计: {ys}")
    worst = df.nsmallest(3, "ret")
    for _, w in worst.iterrows():
        print(f"    最差 {w['pair']} {w['year']}: {w['ret'] * 100:+.2f}% (fund {w['fund'] * 100:+.2f}%, basis {w['basis'] * 100:+.2f}%)")


def main():
    data = {}
    for p in PAIRS:
        df, fund = load(p)
        data[p] = (df, fund)
        apr = fund[(fund.index >= T_START) & (fund.index < T_END)].mean() * 3 * 365 * 100
        print(f"{p}: funding 基准 APR≈{apr:+.1f}%  基差均值 {(df['b'].mean()) * 100:+.4f}%")

    for theta in THETAS:
        for hold_name, hold in HOLDS.items():
            events = []
            for p, (df, fund) in data.items():
                run(theta, hold, events, p, df, fund)
            agg(events, f"θ={theta * 100:.2f}% h={hold_d_str(hold)}")


def hold_d_str(h):
    return {18: "3d", 42: "7d", 84: "14d"}[h]


if __name__ == "__main__":
    main()
