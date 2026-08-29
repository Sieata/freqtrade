"""双信号叠加检验：funding 极端负（FS 触发）× OI 急缩+急跌（OIFlush 触发）同 K 线共振。

假设（RESEARCH 12.7）：两个独立信息源（衍生品费率 vs 持仓量）同时发出极端信号 =
高置信事件，后续漂移应显著强于单信号。若样本足够且增强显著，FS/OIFlush 可合成
"强信号 Only"高置信变体。

口径：同 metrics_phase1/h4r（非重叠、次根开盘入场、持有后开盘出场、扣 0.1% 往返）。
品种：13 个有 metrics 的币；TEST 20220101-20240828。
用法: .venv/bin/python user_data/scripts/signal_stack_check.py
"""
import numpy as np
import pandas as pd

FUT = "user_data/data/binance/futures"
METRICS = "user_data/data/binance/futures_metrics"
PAIRS = ["BTC", "ETH", "BNB", "XRP", "SOL", "ZEC", "DOGE", "ADA", "AVAX", "DOT", "TRX", "XMR"]
FEE_RT = 0.001
T_START, T_END = "2022-01-01", "2024-08-28"
WIN = 1080


def load(pair):
    k = pd.read_feather(f"{FUT}/{pair}_USDT_USDT-4h-futures.feather")
    k = k[["date", "open", "close"]].set_index("date").sort_index()
    m = pd.read_feather(f"{METRICS}/{pair}_USDT_USDT-4h-metrics.feather").set_index("date").sort_index()
    df = k.join(m, how="left")
    df = df[(df.index >= T_START) & (df.index < T_END)]
    df["ret4"] = df["close"] / df["close"].shift(1) - 1
    df["oi_chg"] = df["oi_usd"] / df["oi_usd"].shift(6) - 1
    df["oi_q"] = df["oi_chg"].rolling(WIN, min_periods=360).quantile(0.05)
    # FS 信号: funding ≤ 90d p2（费率全史 ffill 到 4h）
    f = pd.read_feather(f"{FUT}/{pair}_USDT_USDT-1h-funding_rate.feather")
    s = f[f["open"] != 0].set_index("date")["open"]
    s = s[~s.index.duplicated(keep="last")]
    f4 = s.reindex(df.index, method="ffill")
    df["fund"] = f4
    df["fund_q"] = f4.rolling(540, min_periods=200).quantile(0.02)
    df["fs_sig"] = df["fund"] <= df["fund_q"]
    df["oi_sig"] = (df["oi_chg"] <= df["oi_q"]) & (df["ret4"] <= -0.05)
    df["stack"] = df["fs_sig"] & df["oi_sig"]
    return df


def collect(sig_by_pair, hold):
    events = []
    for pair, sig in sig_by_pair.items():
        df = DATA[pair]
        sig = sig.reindex(df.index).fillna(False)
        locked = -1
        for i in np.where(sig.values)[0]:
            if i <= locked or i + 1 + hold >= len(df):
                continue
            entry, exit_ = df["open"].iloc[i + 1], df["open"].iloc[i + 1 + hold]
            events.append((pair, df.index[i].year, exit_ / entry - 1 - FEE_RT))
            locked = i + hold
    return events


def agg(events, label):
    if not events:
        print(f"{label}: 无事件")
        return
    df = pd.DataFrame(events, columns=["pair", "year", "ret"])
    yr = df.groupby("year")["ret"].agg(["mean", "count"])
    ys = ", ".join(f"{y}:{r['mean'] * 100:+.2f}%(n={r['count']})" for y, r in yr.iterrows())
    pp = df.groupby("pair")["ret"].agg(["mean", "count"])
    pos = int((pp["mean"] > 0).sum())
    r = df["ret"].values
    print(f"{label}: n={len(r)} win={100 * (r > 0).mean():.1f}% mean={r.mean() * 100:+.3f}% "
          f"品种净正={pos}/{len(pp)}")
    print(f"    逐年: {ys}")


DATA = {p: load(p) for p in PAIRS}

print("=" * 100)
print("单信号 vs 叠加（hold 48h / 72h，TEST）")
print("=" * 100)
for hold, hname in [(12, "48h"), (18, "72h")]:
    fs = {p: DATA[p]["fs_sig"] for p in DATA}
    oi = {p: DATA[p]["oi_sig"] for p in DATA}
    st = {p: DATA[p]["stack"] for p in DATA}
    print(f"--- hold={hname} ---")
    n_stack = sum(int(s.sum()) for s in st.values())
    print(f"    （叠加信号原始 K 线数: {n_stack}）")
    agg(collect(fs, hold), f"  FS 单信号")
    agg(collect(oi, hold), f"  OI 单信号")
    agg(collect(st, hold), f"  双信号叠加")
