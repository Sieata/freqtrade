"""Phase1-metrics：H4/H5/H6 事件研究（严格只用 TEST 20220101-20240828，口径同 newedge_phase1）。

H4 清算去杠杆尾声做多: oi_usd 24h 变化 ≤ 自身 180d p5 且 4h 收益 ≤ -5%
H5 大户极端看空做多:   top_ls_pos 24h 均值 ≤ 自身 180d p2
H6 taker 卖压衰竭做多: taker_ls 24h 均值 ≤ 自身 180d p2

方法: 信号 K 线收盘确认 → 次根 K 开盘入场 → 固定持有后开盘出场，扣往返摩擦 0.1%。
     同一假设内事件不重叠（信号触发后锁到持有期结束，防同一波行情重复计数）。
     独立性: 报告与 FundingSqueeze(funding p2 状态)、CrashBuy(近似: 前根 4h ≤-9% 且本根收阳) 重叠率。

用法: .venv/bin/python user_data/scripts/metrics_phase1.py
"""
import numpy as np
import pandas as pd

DATA = "user_data/data/binance/futures"
PAIRS = ["BTC", "ETH", "BNB", "XRP", "SOL", "ZEC", "DOGE", "ADA", "AVAX", "DOT"]
FEE_RT = 0.001
T_START, T_END = "2022-01-01", "2024-08-28"
HOLDS = {"24h": 6, "48h": 12, "72h": 18}
WIN_180D = 1080  # 180d @ 4h


def load(pair):
    k = pd.read_feather(f"{DATA}/{pair}_USDT_USDT-4h-futures.feather")
    k = k[["date", "open", "high", "low", "close", "volume"]].set_index("date").sort_index()
    m = pd.read_feather(f"{DATA}/{pair}_USDT_USDT-4h-metrics.feather").set_index("date").sort_index()
    df = k.join(m, how="left")
    return df[(df.index >= T_START) & (df.index < T_END)]


def load_fund_state(pair):
    """FundingSqueeze 的费率状态（近似复刻: open!=0 过滤 + ffill + 90d p2）。"""
    f = pd.read_feather(f"{DATA}/{pair}_USDT_USDT-1h-funding_rate.feather")
    s = f[f["open"] != 0][["date", "open"]].rename(columns={"open": "rate"}).set_index("date").sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s["rate"]


def agg(events, label):
    if not events:
        print(f"{label}: 无事件")
        return
    df = pd.DataFrame(events, columns=["pair", "year", "ret", "fs_ov", "cb_ov"])
    yearly = df.groupby("year")["ret"].agg(["mean", "count"])
    ys = ", ".join(f"{y}:{r['mean'] * 100:+.3f}%(n={r['count']})" for y, r in yearly.iterrows())
    per_pair = df.groupby("pair")["ret"].agg(["mean", "count"])
    pos = int(((per_pair["mean"] > 0) & (per_pair["count"] >= 8)).sum())
    tot = int((per_pair["count"] >= 8).sum())
    ret = df["ret"].values
    print(f"{label}: n={len(ret)} win={100 * (ret > 0).mean():.1f}% mean={ret.mean() * 100:+.3f}% "
          f"median={np.median(ret) * 100:+.3f}% 品种净正={pos}/{tot} "
          f"FS重叠={100 * df['fs_ov'].mean():.0f}% CB重叠={100 * df['cb_ov'].mean():.0f}%")
    print(f"    逐年: {ys}")


def collect(signals_by_pair, hold):
    """signals_by_pair: {pair: bool Series(信号K)} → 非重叠事件 (pair, year, ret, fs_ov, cb_ov)。"""
    events = []
    for pair, sig in signals_by_pair.items():
        df = DATA_CACHE[pair]
        idx = df.index
        sig = sig.reindex(idx).fillna(False)
        entered_until = -1
        for i in np.where(sig.values)[0]:
            if i <= entered_until or i + 1 + hold >= len(idx):
                continue
            entry, exit_ = df["open"].iloc[i + 1], df["open"].iloc[i + 1 + hold]
            ret = exit_ / entry - 1 - FEE_RT
            events.append((pair, idx[i].year, ret,
                           bool(FS_STATE[pair].reindex([idx[i]]).fillna(False).iloc[0]),
                           bool(CB_SIG[pair].reindex([idx[i]]).fillna(False).iloc[0])))
            entered_until = i + hold
    return events


DATA_CACHE, FS_STATE, CB_SIG = {}, {}, {}

for p in PAIRS:
    df = load(p)
    if df["oi_usd"].notna().sum() < WIN_180D // 2:
        print(f"[skip] {p}: metrics 覆盖不足")
        continue
    df["ret4"] = df["close"] / df["close"].shift(1) - 1
    df["oi_chg"] = df["oi_usd"] / df["oi_usd"].shift(6) - 1
    df["oi_p5"] = df["oi_chg"].rolling(WIN_180D, min_periods=360).quantile(0.05)
    df["top24"] = df["top_ls_pos"].rolling(6).mean()
    df["top_p2"] = df["top24"].rolling(WIN_180D, min_periods=360).quantile(0.02)
    df["tk24"] = df["taker_ls"].rolling(6).mean()
    df["tk_p2"] = df["tk24"].rolling(WIN_180D, min_periods=360).quantile(0.02)
    df["prev_ret"] = df["ret4"].shift(1)
    DATA_CACHE[p] = df
    # FundingSqueeze 状态: 费率 ffill 到 4h 网格，≤90d p2（严格复刻策略: 过滤 0 后 ffill）
    fr = load_fund_state(p)
    f4 = fr.reindex(df.index, method="ffill")
    fund_q = f4.rolling(540, min_periods=200).quantile(0.02)
    FS_STATE[p] = (f4 <= fund_q)
    # CrashBuy 近似: 前根 4h ≤ -9% 且本根收阳
    CB_SIG[p] = (df["prev_ret"] <= -0.09) & (df["close"] > df["open"])

print("=" * 100)
print("H4 清算去杠杆尾声做多（oi_chg24 ≤ 180d p5 & ret4 ≤ -5%）")
print("=" * 100)
h4 = {p: (DATA_CACHE[p]["oi_chg"] <= DATA_CACHE[p]["oi_p5"]) & (DATA_CACHE[p]["ret4"] <= -0.05)
      for p in DATA_CACHE}
for label, hold in HOLDS.items():
    agg(collect(h4, hold), f"H4 hold={label}")

print("=" * 100)
print("H5 大户极端看空做多（top_ls_pos 24h均值 ≤ 180d p2）")
print("=" * 100)
h5 = {p: DATA_CACHE[p]["top24"] <= DATA_CACHE[p]["top_p2"] for p in DATA_CACHE}
for label, hold in HOLDS.items():
    agg(collect(h5, hold), f"H5 hold={label}")

print("=" * 100)
print("H6 taker 卖压衰竭做多（taker_ls 24h均值 ≤ 180d p2）")
print("=" * 100)
h6 = {p: DATA_CACHE[p]["tk24"] <= DATA_CACHE[p]["tk_p2"] for p in DATA_CACHE}
for label, hold in HOLDS.items():
    agg(collect(h6, hold), f"H6 hold={label}")
