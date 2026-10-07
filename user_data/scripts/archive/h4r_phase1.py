"""H4-R 复活事件研究：OI 急缩+急跌 × 趋势体制过滤（TEST 20220101-20240828）。

预注册（RESEARCH 12.3）：两个过滤候选在 TEST 比较——
  (a) 收盘 > 1d MA200；(b) 30 天动量 > 0（close/close[180] − 1 > 0）
判据：2022 ≥ 0、均值 ≥ +1.5%/笔、品种净正 ≥ 9/10、BigMove K 线重叠 ≤ 50%。
事件口径同 metrics_phase1：非重叠、信号收盘确认→次根开盘入场→持有后开盘出场、扣 0.1% 往返。

用法: .venv/bin/python user_data/scripts/h4r_phase1.py
"""
import numpy as np
import pandas as pd

SPOT_FUT = "user_data/data/binance/futures"
METRICS = "user_data/data/binance/futures_metrics"
PAIRS = ["BTC", "ETH", "BNB", "XRP", "SOL", "ZEC", "DOGE", "ADA", "AVAX", "DOT",
         "TRX", "HYPE", "XMR"]
FEE_RT = 0.001
T_START, T_END = "2022-01-01", "2024-08-28"
WIN_180D = 1080


def load(pair):
    k = pd.read_feather(f"{SPOT_FUT}/{pair}_USDT_USDT-4h-futures.feather")
    k = k[["date", "open", "high", "low", "close", "volume"]].set_index("date").sort_index()
    m = pd.read_feather(f"{METRICS}/{pair}_USDT_USDT-4h-metrics.feather").set_index("date").sort_index()
    df = k.join(m, how="left")
    df = df[(df.index >= T_START) & (df.index < T_END)]
    # 趋势过滤
    daily = df["close"].resample("1d").last()
    df["ma200_1d"] = daily.rolling(200).mean().reindex(df.index, method="ffill")
    df["f_ma"] = df["close"] > df["ma200_1d"]
    df["f_mom"] = df["close"] / df["close"].shift(180) - 1 > 0
    # 指标
    df["ret4"] = df["close"] / df["close"].shift(1) - 1
    df["oi_chg"] = df["oi_usd"] / df["oi_usd"].shift(6) - 1
    df["oi_q"] = df["oi_chg"].rolling(WIN_180D, min_periods=360).quantile(0.05)
    df["prev_ret"] = df["ret4"].shift(1)
    # BigMove 近似（其参数：ret=pct_change(3)>12% & close>SMA200(4h) & BTC>SMA200(4h)）
    df["sma200_4h"] = df["close"].rolling(200).mean()
    df["bm_sig"] = (df["close"].pct_change(3) > 0.12) & (df["close"] > df["sma200_4h"])
    df["cb_sig"] = (df["prev_ret"] <= -0.09) & (df["close"] > df["open"])
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
            events.append((pair, df.index[i].year, exit_ / entry - 1 - FEE_RT,
                           bool(df["bm_sig"].iloc[i]), bool(df["cb_sig"].iloc[i])))
            locked = i + hold
    return events


def agg(events, label):
    if not events:
        print(f"{label}: 无事件")
        return
    df = pd.DataFrame(events, columns=["pair", "year", "ret", "bm", "cb"])
    yr = df.groupby("year")["ret"].agg(["mean", "count"])
    ys = ", ".join(f"{y}:{r['mean'] * 100:+.2f}%(n={r['count']})" for y, r in yr.iterrows())
    pp = df.groupby("pair")["ret"].agg(["mean", "count"])
    pos = int((pp["mean"] > 0).sum())
    r = df["ret"].values
    print(f"{label}: n={len(r)} win={100 * (r > 0).mean():.1f}% mean={r.mean() * 100:+.3f}% "
          f"品种净正={pos}/{len(pp)} BM重叠={100 * df['bm'].mean():.0f}% CB重叠={100 * df['cb'].mean():.0f}%")
    print(f"    逐年: {ys}")


DATA = {p: load(p) for p in PAIRS if p != "HYPE"}  # HYPE 2025-05 上市, TEST 无数据
print(f"品种: {list(DATA)}  metrics 缺 HYPE(上市晚)")
flush = {p: (d["oi_chg"] <= d["oi_q"]) & (d["ret4"] <= -0.05) for p, d in DATA.items()}

print("=" * 100)
print("基线（无过滤）")
agg(collect(flush, 12), "H4 hold=48h")
agg(collect(flush, 18), "H4 hold=72h")

for fname, flabel in [("f_ma", "过滤(a) 收盘>1d MA200"), ("f_mom", "过滤(b) 30天动量>0")]:
    print("=" * 100)
    print(f"H4-R {flabel}")
    sig = {p: flush[p] & DATA[p][fname] for p in DATA}
    agg(collect(sig, 12), f"H4-R {flabel} hold=48h")
    agg(collect(sig, 18), f"H4-R {flabel} hold=72h")
