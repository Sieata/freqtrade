"""Phase1 信号统计：三个新方向假设的事件研究（严格只用 TEST 20220101-20240828）。

假设（与现有库互补、且未被既有失败记录覆盖）:
  H1 资金费极端负值做多 —— 空头拥挤=轧空燃料（SynthPut 只证伪了做空侧，做多侧未测）
  H1b 资金费极端正值做多 —— "涨了继续涨"的杠杆拥挤变体（S11 做空侧已证伪，做多侧未测）
  H2 波动率压缩后突破（1d squeeze breakout）—— 与 BigMove 的 3 日动量不同源，看"蓄势后突破"
  H3 震荡市均值回归（4h 下轨回收，weekday）—— 针对 BigMove 2025 震荡年空档，与周末反转互补

方法: 事件研究，信号 K 线收盘确认 → 下一根 K 开盘入场 → 固定持有后开盘出场（对齐 freqtrade 语义）。
      收益已扣往返摩擦 0.1%。输出: 合并事件统计 + 逐年均值 + 逐品种广度（净均值>0 的品种数）。

用法: .venv/bin/python user_data/scripts/newedge_phase1.py
"""
import numpy as np
import pandas as pd

DATA = "user_data/data/binance/futures"
PAIRS = ["BTC", "ETH", "BNB", "XRP", "SOL", "ZEC", "DOGE", "ADA", "AVAX", "DOT"]  # core∩data 去 HYPE(无TEST数据)
FEE_RT = 0.001
T_START, T_END = "2022-01-01", "2024-08-28"
HOLDS_4H = [6, 12, 18]   # 24h / 48h / 72h
HOLDS_1D = [5, 10, 20]   # 5 / 10 / 20 天


def load_klines(pair, tf):
    df = pd.read_feather(f"{DATA}/{pair}_USDT_USDT-{tf}-futures.feather")
    df = df[["date", "open", "high", "low", "close", "volume"]].set_index("date").sort_index()
    return df[(df.index >= T_START) & (df.index < T_END)]


def load_funding(pair):
    f = pd.read_feather(f"{DATA}/{pair}_USDT_USDT-1h-funding_rate.feather")
    s = f[["date", "open"]].rename(columns={"open": "rate"}).set_index("date").sort_index()
    return s[~s.index.duplicated(keep="last")]


def agg(events, hold_label):
    """events: list of (pair, year, ret)。打印合并统计 + 逐年 + 品种广度。"""
    if not events:
        print(f"{hold_label}: 无事件")
        return
    df = pd.DataFrame(events, columns=["pair", "year", "ret"])
    yearly = df.groupby("year")["ret"].agg(["mean", "count"])
    ys = ", ".join(f"{y}:{r['mean'] * 100:+.3f}%(n={r['count']})" for y, r in yearly.iterrows())
    per_pair = df.groupby("pair")["ret"].agg(["mean", "count"])
    pos = int(((per_pair["mean"] > 0) & (per_pair["count"] >= 8)).sum())
    tot = int((per_pair["count"] >= 8).sum())
    ret = df["ret"].values
    print(f"{hold_label}: n={len(ret)} win={100 * (ret > 0).mean():.1f}% mean={ret.mean() * 100:+.3f}% "
          f"median={np.median(ret) * 100:+.3f}% 品种净正={pos}/{tot}")
    print(f"    逐年: {ys}")


def run_h1(hold_list):
    print("\n" + "=" * 100)
    print("H1 资金费极端负做多 / H1b 极端正做多（4h，信号=开盘已公布的最近 funding）")
    print("=" * 100)
    variants = {
        "H1 fund<=-0.03%": lambda k: k["fund"] <= -0.0003,
        "H1 fund<=-0.05%": lambda k: k["fund"] <= -0.0005,
        "H1 fund<=-0.10%": lambda k: k["fund"] <= -0.001,
        "H1 fund<=p2(90d)": lambda k: k["fund"] <= k["fund"].rolling(540, min_periods=200).quantile(0.02),
        "H1 fund<=-0.03%+跌>2%": lambda k: (k["fund"] <= -0.0003) & (k["ret1"].shift(1) < -0.02),
        "H1b fund>=+0.10%": lambda k: k["fund"] >= 0.001,
        "H1b fund>=p98(90d)": lambda k: k["fund"] >= k["fund"].rolling(540, min_periods=200).quantile(0.98),
    }
    caches = {}
    for vname, fn in variants.items():
        for h in hold_list:
            events, base = [], []
            for pair in PAIRS:
                k = caches.setdefault(pair, _prep_h1(pair))
                sig = fn(k)
                sig &= k["volume"] > 0
                pos = np.where(sig.fillna(False).values)[0]
                opens = k["open"].values
                ok = pos[pos + 1 + h < len(k)]
                if len(ok):
                    r = opens[ok + 1 + h] / opens[ok + 1] - 1 - FEE_RT
                    yrs = k.index[ok + 1].year
                    events += [(pair, int(y), float(x)) for y, x in zip(yrs, r)]
                # 基线: 全部 K 线同持有期
                allr = opens[1 + h:] / opens[1:len(k) - h] - 1 - FEE_RT
                base.append(float(np.mean(allr)))
            agg(events, f"{vname} hold={h * 4}h")
            if h == hold_list[0]:
                print(f"    [基线 hold={h * 4}h] 无条件均值={np.mean(base) * 100:+.3f}%")
    return


def _prep_h1(pair):
    k = load_klines(pair, "4h")
    fund = load_funding(pair)
    fk = fund.reindex(k.index, method="ffill")
    k = k.assign(fund=fk["rate"], ret1=k["close"].pct_change())
    return k


def run_h2(hold_list):
    print("\n" + "=" * 100)
    print("H2 波动率压缩后突破（1d：20日新高 × 近10日 squeeze；对照=裸突破）")
    print("=" * 100)
    variants = {
        "H2 squeeze20%+break20": lambda k: (k["brk20"]) & (k["squeeze20"].rolling(10).max() == 1),
        "H2 squeeze20%+break10": lambda k: (k["brk10"]) & (k["squeeze20"].rolling(10).max() == 1),
        "H2 squeeze40%+break20": lambda k: (k["brk20"]) & (k["squeeze40"].rolling(10).max() == 1),
        "对照 裸break20": lambda k: k["brk20"],
    }
    for vname, fn in variants.items():
        for h in hold_list:
            events, base = [], []
            for pair in PAIRS:
                k = _prep_h2(pair)
                sig = fn(k).fillna(False) & (k["volume"] > 0)
                pos = np.where(sig.values)[0]
                opens = k["open"].values
                ok = pos[pos + 1 + h < len(k)]
                if len(ok):
                    r = opens[ok + 1 + h] / opens[ok + 1] - 1 - FEE_RT
                    yrs = k.index[ok + 1].year
                    events += [(pair, int(y), float(x)) for y, x in zip(yrs, r)]
                allr = opens[1 + h:] / opens[1:len(k) - h] - 1 - FEE_RT
                base.append(float(np.mean(allr)))
            agg(events, f"{vname} hold={h}d")
            if vname == "对照 裸break20" and h == hold_list[0]:
                print(f"    [基线 hold={h}d] 无条件均值={np.mean(base) * 100:+.3f}%")


def _prep_h2(pair):
    k = load_klines(pair, "1d")
    mid = k["close"].rolling(20).mean()
    sd = k["close"].rolling(20).std()
    width = (4 * sd) / mid  # BB(20,2) 宽度
    k = k.assign(
        brk20=k["close"] > k["high"].rolling(20).max().shift(1),
        brk10=k["close"] > k["high"].rolling(10).max().shift(1),
        squeeze20=(width <= width.rolling(120, min_periods=60).quantile(0.20)).astype(float),
        squeeze40=(width <= width.rolling(120, min_periods=60).quantile(0.40)).astype(float),
    )
    return k


def run_h3(hold_list):
    print("\n" + "=" * 100)
    print("H3 震荡市均值回归（4h 下轨回收 × weekday；regime=BTC 1d<MA200 或不加 regime）")
    print("=" * 100)
    btc1 = load_klines("BTC", "1d")
    btc_ma = btc1["close"].rolling(200).mean()
    btc_below = (btc1["close"] < btc_ma).reindex(btc1.index)
    variants = {
        "H3 回收(无regime)": lambda k: k["reenter"],
        "H3 回收×BTC<MA200": lambda k: k["reenter"] & k["btc_below"],
        "H3 回收×跌>2%": lambda k: k["reenter"] & (k["ret1"].shift(1) < -0.02),
        "H3 回收×跌>2%×BTC<MA200": lambda k: k["reenter"] & (k["ret1"].shift(1) < -0.02) & k["btc_below"],
    }
    for vname, fn in variants.items():
        for h in hold_list:
            events = []
            for pair in PAIRS:
                k = _prep_h3(pair, btc_below)
                sig = (fn(k).fillna(False).astype(bool)) & (k["volume"] > 0)
                weekday = k.index.dayofweek < 5
                sig &= weekday
                pos = np.where(sig.values)[0]
                opens = k["open"].values
                ok = pos[pos + 1 + h < len(k)]
                if len(ok):
                    r = opens[ok + 1 + h] / opens[ok + 1] - 1 - FEE_RT
                    yrs = k.index[ok + 1].year
                    events += [(pair, int(y), float(x)) for y, x in zip(yrs, r)]
            agg(events, f"{vname} hold={h * 4}h")


def _prep_h3(pair, btc_below):
    k = load_klines(pair, "4h")
    mid = k["close"].rolling(20).mean()
    sd = k["close"].rolling(20).std()
    lower = mid - 2 * sd
    below_prev = k["close"].shift(1) < lower.shift(1)
    back = k["close"] > lower
    d1 = btc_below.reindex(k.index, method="ffill")
    return k.assign(reenter=(below_prev & back), ret1=k["close"].pct_change(),
                    btc_below=d1.fillna(False).astype(bool))


if __name__ == "__main__":
    run_h1(HOLDS_4H)
    run_h2(HOLDS_1D)
    run_h3([3, 6])  # 12h / 24h
    print("\nDONE")
